[CmdletBinding()]
param(
    [switch]$Offline,
    [string]$SettingsPath = 'E:\VideoSummaryApp-Records\WP07-A\deployment\s3-11-settings.json'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$settings = Get-Content -Raw -LiteralPath $SettingsPath | ConvertFrom-Json
$base = $settings.compose
$overlay = Join-Path $PSScriptRoot 'compose.s3-11.yaml'
$privateEnv = $settings.private_env
$baseUrl = 'https://maas.qianwenaiapi.com/compatible-mode/v1'
$model = 'qwen3.8-omni-flash'

function Run-Docker([string[]]$Command, [string]$Step) {
    $ErrorActionPreference = 'Continue'
    $output = & docker --context desktop-linux @Command 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Docker step failed: $Step" }
    $output
}

function Confirm-Image($image) {
    $actual = Run-Docker @('image','inspect','--format','{{.Id}}',$image.tag) 'local image identity'
    if ($actual -cne $image.id) { throw 'A local image does not match its verified ID.' }
}

$compose = @('compose','--project-name','vsa-local','--env-file',$privateEnv,'-f',$base)
$candidate = $compose + @('-f',$overlay)

function Gate([string]$Command) {
    $raw = Run-Docker ($compose + @('--profile','maintenance','run','--rm','--no-deps','--pull','never',
        'maintenance','python','-m','app.maintenance',$Command)) "maintenance $Command"
    $gate = $raw | ConvertFrom-Json
    if ($gate.deployment_id -ne $settings.deployment_id -or ($Command -eq 'status' -and (!$gate.trusted -or !$gate.required))) {
        throw 'Maintenance state is untrusted or belongs to a different deployment.'
    }
    $gate
}

function Confirm-Idle {
    $query = "SELECT (SELECT count(*) FROM tasks WHERE status IN ('QUEUED','PROCESSING') AND deletion_requested_at IS NULL AND deleted_at IS NULL), (SELECT count(*) FROM media_assets WHERE asset_kind='QWEN_TRANSFER' AND cleanup_state<>'DELETED');"
    $value = Run-Docker @('exec','vsa-local-db-1','psql','-X','-U','vsa_init','-d','video_summary','-At','-v','ON_ERROR_STOP=1','-c',$query) 'check pending tasks'
    if ($value -ne '0|0') { throw 'Wait for queued tasks, processing tasks, and pending OSS video cleanup before updating.' }
}

function Read-Status([string]$Path) {
    try {
        return [int](Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:58080' + $Path) -TimeoutSec 5).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function Wait-Http([string]$Path, [int]$Expected) {
    for ($i=0; $i -lt 20; $i++) {
        if ((Read-Status $Path) -eq $Expected) { return }
        Start-Sleep -Milliseconds 750
    }
    throw "Unexpected HTTP status for $Path."
}

function Wait-Worker {
    for ($i=0; $i -lt 20; $i++) {
        try {
            $null = Run-Docker @('exec','vsa-local-worker-1','python','-m','app.worker','--healthcheck') 'Worker health'
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    throw 'Worker health did not become ready.'
}

function Confirm-Services($workerId, $webId) {
    $targetImages = @{worker=$workerId;web=$webId;api=$settings.api.id;db=$settings.db.id;'object-store'=$settings.object_store.id}
    foreach ($service in $targetImages.Keys) {
        $name = "vsa-local-$service-1"
        $info = ((Run-Docker @('inspect',$name) 'live image and binding') -join [Environment]::NewLine | ConvertFrom-Json)
        $ports = $info.HostConfig.PortBindings
        $portCount = @($ports.PSObject.Properties).Count
        $validBinding = $portCount -eq 0
        if ($service -eq 'web') {
            $published = @($ports.'8080/tcp')
            $validBinding = $portCount -eq 1 -and $published.Count -eq 1 -and
                $published[0].HostIp -ceq '127.0.0.1' -and $published[0].HostPort -ceq '58080'
        }
        if ($info.Image -cne $targetImages[$service] -or !$info.State.Running -or
            $info.Config.Labels.'com.docker.compose.project' -ne 'vsa-local' -or !$validBinding) {
            throw "Unexpected identity or port binding for $service."
        }
        if ($service -in @('api','db','object-store') -and $info.Id -cne $preservedIds[$service]) {
            throw "Existing service identity changed: $service."
        }
    }
}

if (!(Test-Path -LiteralPath $privateEnv -PathType Leaf) -or
    (Get-FileHash -LiteralPath $base).Hash.ToLowerInvariant() -cne $settings.compose_sha256 -or
    (Get-FileHash -LiteralPath $overlay).Hash.ToLowerInvariant() -cne $settings.overlay_sha256) {
    throw 'Deployment configuration does not match the validated files.'
}
foreach ($image in @($settings.api,$settings.db,$settings.object_store,$settings.original_web,$settings.worker,$settings.web)) {
    Confirm-Image $image
}

$key = ''
$values = @{}
$modelEnv = Join-Path $root '.env'
if (Test-Path -LiteralPath $modelEnv -PathType Leaf) {
    foreach ($line in (Get-Content -LiteralPath $modelEnv)) {
        if ($line -match '^\s*(DASHSCOPE_API_KEY|DASHSCOPE_BASE_URL|QWEN_MODEL|QWEN_OSS_REGION|QWEN_OSS_BUCKET|QWEN_OSS_ACCESS_KEY_ID|QWEN_OSS_ACCESS_KEY_SECRET)\s*=(.*)$') {
            $name = $Matches[1]
            if ($values.ContainsKey($name)) { throw 'Duplicate model or OSS variable in .env.' }
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and $value[0] -eq $value[$value.Length-1] -and $value[0] -in @('"',"'")) {
                $value = $value.Substring(1,$value.Length-2)
            }
            $values[$name] = $value
        }
    }
}
$ossFields = @('QWEN_OSS_REGION','QWEN_OSS_BUCKET','QWEN_OSS_ACCESS_KEY_ID','QWEN_OSS_ACCESS_KEY_SECRET')
$ossCount = @($ossFields | Where-Object { $values[$_] }).Count
if ($ossCount -ne 0 -and $ossCount -ne $ossFields.Count) {
    throw 'Complete all four QWEN_OSS settings in .env, or leave all four empty.'
}
if ($ossCount -and ($values.QWEN_OSS_REGION -cnotmatch '^(?!oss-)[a-z0-9]+(?:-[a-z0-9]+)+\z' -or
        $values.QWEN_OSS_BUCKET -cnotmatch '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\z')) {
    throw 'Invalid OSS region or bucket in .env.'
}
if (!$Offline) {
    $key = [string]$values.DASHSCOPE_API_KEY
    if (!$key) { throw 'Fill DASHSCOPE_API_KEY in the project .env before applying, or use -Offline.' }
    $prefix = if ($key.StartsWith('sk-ws-')) {'sk-ws-'} else {'sk-'}
    if ($key.StartsWith('sk-sp-') -or !$key.StartsWith($prefix) -or $key.Substring($prefix.Length) -cnotmatch '^[A-Za-z0-9._~+/-]+=*\z') {
        throw 'Use a Qianwen AI pay-as-you-go key.'
    }
    if ($values.DASHSCOPE_BASE_URL -and $values.DASHSCOPE_BASE_URL.TrimEnd('/') -cne $baseUrl) {
        throw 'DASHSCOPE_BASE_URL in .env must use the verified Qianwen AI endpoint.'
    }
    if ($values.QWEN_MODEL -and $values.QWEN_MODEL -cne $model) { throw 'QWEN_MODEL in .env must be qwen3.8-omni-flash.' }
}
$mode = if ($Offline) {'offline'} else {'qwen'}
$variables = @{
    COMPOSE_DISABLE_ENV_FILE='1'
    VSA_S311_WORKER_IMAGE=$settings.worker.tag
    VSA_S311_WEB_IMAGE=$settings.web.tag
    VSA_S311_WORKER_MODE=$mode
    VSA_S311_MODEL_CALLS= $(if ($Offline) {'false'} else {'true'})
    VSA_S311_TIMEOUT_SECONDS= $(if ($Offline) {'120'} else {'600'})
    VSA_S311_KEY=$key
    VSA_S311_BASE_URL=$baseUrl
    VSA_S311_MODEL=$model
    VSA_S311_OSS_REGION=[string]$values.QWEN_OSS_REGION
    VSA_S311_OSS_BUCKET=[string]$values.QWEN_OSS_BUCKET
    VSA_S311_OSS_ACCESS_KEY_ID=[string]$values.QWEN_OSS_ACCESS_KEY_ID
    VSA_S311_OSS_ACCESS_KEY_SECRET=[string]$values.QWEN_OSS_ACCESS_KEY_SECRET
}
$values.Clear()
$previousEnv = @{}
$changed = $false
$closedByUs = $false
$wasOpen = $false
try {
    foreach ($entry in (Get-ChildItem Env: | Where-Object { $_.Name -like 'WP07_*' -or $_.Name -like 'VSA_S311_*' -or $_.Name -in @('COMPOSE_PROFILES','COMPOSE_ENV_FILES','COMPOSE_DISABLE_ENV_FILE') })) {
        $previousEnv[$entry.Name] = $entry.Value
        [Environment]::SetEnvironmentVariable($entry.Name,$null,'Process')
    }
    foreach ($name in $variables.Keys) {
        if (!$previousEnv.ContainsKey($name)) { $previousEnv[$name]=$null }
        [Environment]::SetEnvironmentVariable($name,$variables[$name],'Process')
    }
    $null = Run-Docker ($candidate + @('config','--quiet')) 'compose validation'
    $preservedIds = @{}
    foreach ($service in @('api','db','object-store')) {
        $preservedIds[$service] = Run-Docker @('inspect','--format','{{.Id}}',"vsa-local-$service-1") 'existing container identity'
    }
    $state = Gate 'status'
    if ($state.state -notin @('OPEN','CLOSED')) { throw 'Maintenance state cannot be safely preserved.' }
    $wasOpen = $state.state -eq 'OPEN'
    $currentWorker = Run-Docker @('inspect','--format','{{.Image}}','vsa-local-worker-1') 'current Worker image'
    $currentWeb = Run-Docker @('inspect','--format','{{.Image}}','vsa-local-web-1') 'current Web image'
    if ($currentWorker -notin @($settings.api.id,$settings.worker.id,$settings.previous_worker_id) -or
        $currentWeb -notin @($settings.original_web.id,$settings.web.id,$settings.previous_web_id)) {
        throw 'Unexpected existing Worker or Web image.'
    }
    Confirm-Services $currentWorker $currentWeb
    Confirm-Idle
    if ($wasOpen) {
        if ((Gate 'close').state -ne 'CLOSED') { throw 'Could not close maintenance gate.' }
        $closedByUs = $true
    }
    Confirm-Idle
    $changed = $true
    $null = Run-Docker ($candidate + @('up','-d','--no-deps','--no-build','--pull','never','worker','web')) 'replace Worker and Web'
    Confirm-Services $settings.worker.id $settings.web.id
    Wait-Http '/api/health/live' 200
    Wait-Http '/api/health/ready' 503
    $configuration = Run-Docker @('exec','vsa-local-worker-1','python','-c',
        "import os; from app.worker import _configured_processor; assert _configured_processor(); print(os.environ['WORKER_PROCESSOR_MODE'])") 'Worker mode validation'
    if ($configuration -cne $mode) { throw 'Worker did not read the requested model mode.' }
    Wait-Worker
    if ($wasOpen) {
        if ((Gate 'open').state -ne 'OPEN') { throw 'Could not restore OPEN.' }
        Wait-Http '/api/health/ready' 200
    }
    $ossMode = if ($mode -eq 'qwen' -and $ossCount) {'configured'} else {'disabled'}
    Write-Output "S3-11 applied. Worker mode: $mode. OSS: $ossMode. Web: http://127.0.0.1:58080/"
} catch {
    $failure = $_.Exception.Message
    if ($changed -or $closedByUs) {
        try {
            $gate = Gate 'status'
            if ($changed) {
                if ($gate.state -eq 'OPEN' -and (Gate 'close').state -ne 'CLOSED') { throw 'Rollback gate close failed.' }
                $null = Run-Docker ($compose + @('up','-d','--no-deps','--no-build','--pull','never','worker','web')) 'restore WP07-A Worker and Web'
                Confirm-Services $settings.api.id $settings.original_web.id
                Wait-Http '/api/health/live' 200
            }
            if ($wasOpen -and (Gate 'status').state -eq 'CLOSED') {
                if ((Gate 'open').state -ne 'OPEN') { throw 'Rollback reopen failed.' }
                Wait-Http '/api/health/ready' 200
            }
            Write-Warning 'The existing instance was restored to its safe maintenance state. If replacement had started, WP07-A offline Worker and Web were restored.'
        } catch {
            Write-Warning 'Automatic rollback did not finish. Inspect the current gate and containers before proceeding.'
        }
    }
    throw $failure
} finally {
    foreach ($name in $previousEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name,$previousEnv[$name],'Process')
    }
    $key = $null
    $variables.Clear()
}
