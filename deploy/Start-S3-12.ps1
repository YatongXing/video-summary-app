[CmdletBinding()]
param(
    [ValidateSet('Start','Update','UpgradeFormal')][string]$Action = 'Start',
    [string]$ProjectName = 'vsa-s312-test',
    [ValidateRange(1024,65535)][int]$HttpPort = 58081,
    [string]$StateDirectory,
    [string]$PrivateEnvPath,
    [string]$ModelEnvPath,
    [switch]$TestQwen
)
$ErrorActionPreference = 'Stop'
foreach ($builtin in @('Microsoft.PowerShell.Management','Microsoft.PowerShell.Utility')) {
    Import-Module ([IO.Path]::Combine($PSHOME,'Modules',$builtin,"$builtin.psd1"))
}
if (!$StateDirectory) { $StateDirectory = Join-Path $env:LOCALAPPDATA 'VideoSummaryApp\S3-12' }
$root = Split-Path -Parent $PSScriptRoot
$formal = $Action -eq 'UpgradeFormal'
if ($formal) {
    if (!$PrivateEnvPath -or $TestQwen -or
        ($PSBoundParameters.ContainsKey('ProjectName') -and $ProjectName -cne 'vsa-local') -or
        ($PSBoundParameters.ContainsKey('HttpPort') -and $HttpPort -ne 58080)) {
        throw 'UpgradeFormal requires -PrivateEnvPath and uses project vsa-local on port 58080.'
    }
    $ProjectName = 'vsa-local'
    $HttpPort = 58080
    $database = 'video_summary'
} else {
    if ($ProjectName -cnotmatch '^vsa-s312-[a-z0-9][a-z0-9-]{0,37}\z' -or $HttpPort -eq 58080 -or $PrivateEnvPath) {
        throw 'Use a vsa-s312-* isolated project, a port other than 58080, and no -PrivateEnvPath.'
    }
    $PrivateEnvPath = Join-Path $StateDirectory "$ProjectName.env"
    $database = 'video_summary_test'
}
if (!$ModelEnvPath) { $ModelEnvPath = Join-Path $root '.env' }
$baseUrl = 'https://maas.qianwenaiapi.com/compatible-mode/v1'
$model = 'qwen3.8-omni-flash'
$manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'manifest.json') | ConvertFrom-Json
$images = @{
    api=$manifest.images.api
    worker=$manifest.images.worker
    web=$manifest.images.web
    db=$manifest.images.db
    'object-store'=$manifest.images.object_store
}
$appServices = @('api','worker','web')
$volumes = @('postgres-data','object-data','deletion-control','input-staging','processing-work')
$volumePaths = @{
    api=@('/control/journal','/control/staging','/control/processing')
    worker=@('/control/journal','/control/staging','/control/processing')
    db=@('/var/lib/postgresql/data')
    'object-store'=@('/data')
    web=@()
}
$privateVolumes = @{
    '/control/journal'='deletion-control'
    '/control/staging'='input-staging'
    '/control/processing'='processing-work'
    '/var/lib/postgresql/data'='postgres-data'
    '/data'='object-data'
}
$ossFields = @('REGION','BUCKET','ACCESS_KEY_ID','ACCESS_KEY_SECRET')
$manifestMigrations = @($manifest.migrations.PSObject.Properties | Sort-Object Name | ForEach-Object {
    if ($_.Name -cnotmatch '^[0-9]{3}_[a-z0-9_]+\.sql\z' -or $_.Value -cnotmatch '^[0-9a-f]{64}\z') {
        throw 'Invalid approved migration manifest.'
    }
    "$($_.Name)|$($_.Value)"
})
if ($manifestMigrations.Count -ne 11) { throw 'Expected eleven approved migration hashes.' }

function Invoke-Docker([string[]]$Arguments, [string]$Step, [switch]$Optional) {
    $ErrorActionPreference = 'Continue'
    $output = & docker --context desktop-linux @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($Optional) { return }
        throw "Docker step failed: $Step"
    }
    $output
}

function Confirm-Files {
    foreach ($file in @('deploy/compose.wp07.yaml','deploy/compose.s3-12.yaml','deploy/compose.s3-12-isolated.yaml')) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $root $file)).Hash.ToLowerInvariant()
        if ($hash -cne $manifest.files.PSObject.Properties[$file].Value) { throw 'Compose file identity mismatch.' }
    }
}

function Confirm-Images {
    $missing = $false
    foreach ($image in $images.Values) {
        if ($image.id -cnotmatch '^sha256:[0-9a-f]{64}\z' -or $image.tag -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_./:-]+\z') {
            throw 'Invalid image manifest.'
        }
        $actual = Invoke-Docker @('image','inspect','--format','{{.Id}}',$image.tag) 'local image identity' -Optional
        if (!$actual) { $missing = $true }
        elseif ($actual -cne $image.id) { throw "Existing image tag differs from the package: $($image.tag)" }
    }
    if ($missing) {
        $archive = Join-Path $root 'images.tar'
        if ((Get-FileHash -LiteralPath $archive).Hash.ToLowerInvariant() -cne $manifest.archive_sha256) {
            throw 'Image archive identity mismatch.'
        }
        $null = Invoke-Docker @('load','-i',$archive) 'load checked package images'
    }
    foreach ($image in $images.Values) {
        if ((Invoke-Docker @('image','inspect','--format','{{.Id}}',$image.tag) 'package image identity') -cne $image.id) {
            throw "Local image ID differs from the package: $($image.tag)"
        }
    }
}

function Read-ModelSettings {
    $result = @{
        VSA_S312_MODE='offline'
        VSA_S312_MODEL_CALLS='false'
        VSA_S312_TIMEOUT='120'
        VSA_S312_KEY=''
    }
    foreach ($field in $ossFields) { $result["VSA_S312_OSS_$field"] = '' }
    if (!$formal -and !$TestQwen) { return $result }
    $values = @{}
    foreach ($line in (Get-Content -LiteralPath $ModelEnvPath)) {
        if ($line -match '^\s*(DASHSCOPE_API_KEY|DASHSCOPE_BASE_URL|QWEN_MODEL|QWEN_OSS_REGION|QWEN_OSS_BUCKET|QWEN_OSS_ACCESS_KEY_ID|QWEN_OSS_ACCESS_KEY_SECRET)\s*=(.*)$') {
            $name = $Matches[1]
            if ($values.ContainsKey($name)) { throw 'Duplicate model or OSS setting in model .env.' }
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and $value[0] -eq $value[$value.Length-1] -and $value[0] -in @('"',"'")) {
                $value = $value.Substring(1,$value.Length-2)
            }
            $values[$name] = $value
        }
    }
    $key = [string]$values.DASHSCOPE_API_KEY
    $prefix = if ($key.StartsWith('sk-ws-')) {'sk-ws-'} else {'sk-'}
    if ($key.StartsWith('sk-sp-') -or !$key.StartsWith($prefix) -or $key.Substring($prefix.Length) -cnotmatch '^[A-Za-z0-9._~+/-]+=*\z') {
        throw 'Fill the pay-as-you-go DASHSCOPE_API_KEY in the model .env.'
    }
    if (($values.DASHSCOPE_BASE_URL -and $values.DASHSCOPE_BASE_URL.TrimEnd('/') -cne $baseUrl) -or
        ($values.QWEN_MODEL -and $values.QWEN_MODEL -cne $model)) {
        throw 'The model .env must use the approved Qianwen endpoint and model.'
    }
    $ossCount = @($ossFields | Where-Object { $values["QWEN_OSS_$_"] }).Count
    if ($ossCount -ne 0 -and $ossCount -ne $ossFields.Count) { throw 'Fill all four QWEN_OSS settings or leave all empty.' }
    if ($ossCount -and ($values.QWEN_OSS_REGION -cnotmatch '^(?!oss-)[a-z0-9]+(?:-[a-z0-9]+)+\z' -or
        $values.QWEN_OSS_BUCKET -cnotmatch '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\z')) { throw 'Invalid OSS region or bucket.' }
    if ($TestQwen -and ($key -cnotmatch '^sk-ws-synthetic-[a-zA-Z0-9_-]+\z' -or
        ($ossCount -and ($values.QWEN_OSS_ACCESS_KEY_ID -cnotmatch '^synthetic-' -or $values.QWEN_OSS_ACCESS_KEY_SECRET -cnotmatch '^synthetic-')))) {
        throw 'TestQwen accepts synthetic values only. Use sk-ws-synthetic-* and OSS credentials starting synthetic-.'
    }
    if ($formal -and $key -cmatch '^sk-ws-synthetic-') { throw 'A synthetic key cannot be used for UpgradeFormal.' }
    $result.VSA_S312_MODE = 'qwen'
    $result.VSA_S312_MODEL_CALLS = 'true'
    $result.VSA_S312_TIMEOUT = '600'
    $result.VSA_S312_KEY = $key
    foreach ($field in $ossFields) { $result["VSA_S312_OSS_$field"] = [string]$values["QWEN_OSS_$field"] }
    $values.Clear()
    $result
}

function Read-Container([string]$Service) {
    (Invoke-Docker @('inspect','--type','container',"$ProjectName-$Service-1") 'container identity' | Out-String | ConvertFrom-Json)
}

function Set-StackEnvironment($StackImages, $WorkerSettings, [string]$SupportEmail) {
    $env:WP07_PROJECT_NAME = $ProjectName
    $env:WP07_HTTP_PORT = [string]$HttpPort
    $env:WP07_DB = $database
    $env:WP07_SYNTHETIC_ONLY = if ($formal) {'false'} else {'true'}
    $env:WP07_APP_IMAGE = $StackImages.api.tag
    $env:WP07_WEB_IMAGE = $StackImages.web.tag
    $env:VSA_S312_WORKER_IMAGE = $StackImages.worker.tag
    $env:SUPPORT_EMAIL = $SupportEmail
    foreach ($entry in $WorkerSettings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key,[string]$entry.Value,'Process')
    }
}

function Read-ContainerEnvironment($Container) {
    $result = @{}
    foreach ($line in $Container.Config.Env) {
        $name,$value = $line.Split('=',2)
        $result[$name] = $value
    }
    $result
}

function Confirm-PrivateSettings($CurrentEnvironments) {
    $resolved = (Invoke-Docker ($compose + @('config','--format','json')) 'resolve private settings') | Out-String | ConvertFrom-Json
    $runtimeFields = @('DATABASE_URL','S3_ENDPOINT_URL','S3_BUCKET','S3_ACCESS_KEY','S3_SECRET_KEY')
    $fields = @{
        api=$runtimeFields
        worker=$runtimeFields
        db=@('POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD')
        'object-store'=@('RUSTFS_ACCESS_KEY','RUSTFS_SECRET_KEY')
    }
    foreach ($service in $fields.Keys) {
        $planned = $resolved.services.PSObject.Properties[$service].Value.environment
        foreach ($field in $fields[$service]) {
            if ([string]$CurrentEnvironments[$service][$field] -cne [string]$planned.PSObject.Properties[$field].Value) {
                throw "The private Compose settings would change the running connection: $service/$field"
            }
        }
    }
}

function Confirm-Volumes {
    $actual = @(Invoke-Docker @('volume','ls','-q','--filter',"label=com.docker.compose.project=$ProjectName") 'project volume identity' | Sort-Object)
    $expected = @($volumes | ForEach-Object {"${ProjectName}_$_"} | Sort-Object)
    if (($actual -join '|') -cne ($expected -join '|')) { throw 'Expected the five named project volumes.' }
}

function Confirm-Stack($StackImages, $Preserved, [switch]$AllowStopped) {
    Confirm-Volumes
    foreach ($service in $StackImages.Keys) {
        $container = Read-Container $service
        if ($container.Image -cne $StackImages[$service].id -or (!$AllowStopped -and !$container.State.Running) -or
            $container.Config.Labels.'com.docker.compose.project' -cne $ProjectName -or
            $container.Config.Labels.'com.docker.compose.service' -cne $service -or
            (!$formal -and $container.Config.Labels.'vsa.synthetic_only' -cne 'true') -or
            ($Preserved.ContainsKey($service) -and $Preserved[$service] -cne $container.Id)) {
            throw "Unexpected container identity: $service"
        }
        $ports = $container.HostConfig.PortBindings
        $portNames = @($ports.PSObject.Properties | ForEach-Object Name)
        if ($service -eq 'web') {
            $binding = @($ports.'8080/tcp')
            if ($portNames.Count -ne 1 -or $binding.Count -ne 1 -or $binding[0].HostIp -cne '127.0.0.1' -or
                $binding[0].HostPort -cne [string]$HttpPort) { throw 'Web must bind only to the requested localhost port.' }
        } elseif ($portNames.Count) { throw "Unexpected published port: $service" }
        $mounts = @($container.Mounts | Where-Object { $_ })
        if ($mounts.Count -ne $volumePaths[$service].Count) { throw "Unexpected mounts: $service" }
        foreach ($mount in $mounts) {
            if ($mount.Type -cne 'volume' -or $mount.Destination -cnotin $volumePaths[$service] -or
                $mount.Name -cne "${ProjectName}_$($privateVolumes[$mount.Destination])") { throw "Unexpected volume mount: $service" }
        }
        $networks = @($container.NetworkSettings.Networks.PSObject.Properties | ForEach-Object Name | Sort-Object)
        $expectedNetworks = "${ProjectName}_private"
        if (!$formal -and $service -eq 'web') { $expectedNetworks += "|${ProjectName}_web_ingress" }
        if (($networks -join '|') -cne $expectedNetworks) {
            throw "Unexpected private network membership: $service"
        }
    }
    if (!$formal -and (Invoke-Docker @('network','inspect','--format','{{.Internal}}',"${ProjectName}_private") 'isolated network') -cne 'true') {
        throw 'Isolated project must have no network egress.'
    }
}

function Confirm-Migrations($Actual) {
    if ((@($Actual | Sort-Object) -join '|') -cne ($manifestMigrations -join '|')) {
        throw 'Migration bytes or database ledger differ from the approved package.'
    }
}

function Read-Ledger {
    Invoke-Docker @('exec',"$ProjectName-db-1",'psql','-X','-U','vsa_init','-d',$database,'-At','-v','ON_ERROR_STOP=1',
        '-c','SELECT version, sha256 FROM schema_migrations ORDER BY version;') 'approved database migration ledger'
}

function Confirm-Idle {
    $sql = "SELECT (SELECT count(*) FROM tasks WHERE status IN ('QUEUED','PROCESSING') AND deletion_requested_at IS NULL AND deleted_at IS NULL), (SELECT count(*) FROM input_imports WHERE state IN ('STAGING','STORING') AND expires_at>now()), (SELECT count(*) FROM media_assets WHERE asset_kind='QWEN_TRANSFER' AND cleanup_state<>'DELETED');"
    if ((Invoke-Docker @('exec',"$ProjectName-db-1",'psql','-X','-U','vsa_init','-d',$database,'-At','-v','ON_ERROR_STOP=1',
        '-c',$sql) 'pending activity') -ne '0|0|0') {
        throw 'Wait for uploads, queued tasks, processing and pending OSS cleanup before updating.'
    }
}

function Invoke-Gate([string]$Command) {
    $gate = (Invoke-Docker ($compose + @('--profile','maintenance','run','--rm','--no-deps','--pull','never',
        'maintenance','python','-m','app.maintenance',$Command)) "maintenance $Command") | ConvertFrom-Json
    if ($gate.state -notin @('OPEN','CLOSED') -or ($Command -eq 'status' -and (!$gate.trusted -or !$gate.required)) -or
        ($deploymentId -and $deploymentId -cne $gate.deployment_id)) { throw 'Untrusted maintenance identity or state.' }
    $gate
}

function Wait-Http([string]$Path, [int]$Expected) {
    for ($i=0; $i -lt 25; $i++) {
        try { $status = [int](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$HttpPort$Path" -TimeoutSec 5).StatusCode }
        catch { $status = if ($_.Exception.Response) {[int]$_.Exception.Response.StatusCode} else {0} }
        if ($status -eq $Expected) { return }
        Start-Sleep -Milliseconds 700
    }
    throw "Unexpected HTTP status for $Path."
}

function Confirm-Worker($WorkerSettings) {
    $container = Read-Container 'worker'
    $actual = Read-ContainerEnvironment $container
    $expected = @{
        WORKER_PROCESSOR_MODE=$WorkerSettings.VSA_S312_MODE
        MODEL_CALLS_ENABLED=$WorkerSettings.VSA_S312_MODEL_CALLS
        MODEL_ADAPTER_TIMEOUT_SECONDS=$WorkerSettings.VSA_S312_TIMEOUT
        DASHSCOPE_API_KEY=$WorkerSettings.VSA_S312_KEY
        DASHSCOPE_BASE_URL=$baseUrl
        QWEN_MODEL=$model
    }
    foreach ($field in $ossFields) { $expected["QWEN_OSS_$field"] = $WorkerSettings["VSA_S312_OSS_$field"] }
    foreach ($name in $expected.Keys) {
        if ([string]$actual[$name] -cne [string]$expected[$name]) { throw "Worker did not receive expected setting: $name" }
    }
    for ($i=0; $i -lt 20; $i++) {
        try {
            $null = Invoke-Docker @('exec',"$ProjectName-worker-1",'python','-m','app.worker','--healthcheck') 'Worker health'
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    throw 'Worker did not become healthy.'
}

function Wait-Dependencies {
    for ($i=0; $i -lt 45; $i++) {
        if ((Read-Container 'db').State.Health.Status -eq 'healthy' -and
            (Read-Container 'object-store').State.Health.Status -eq 'healthy') { return }
        Start-Sleep -Seconds 2
    }
    throw 'Existing database or object store did not become healthy.'
}

function New-IsolatedEnvironment {
    $label = "label=com.docker.compose.project=$ProjectName"
    foreach ($command in @(@('ps','-aq'),@('network','ls','-q'),@('volume','ls','-q'))) {
        if (Invoke-Docker ($command + @('--filter',$label)) 'project collision check') {
            throw 'Project resources already exist. Use Update for the existing isolated project.'
        }
    }
    if (Test-Path -LiteralPath $PrivateEnvPath) { throw 'The isolated state file already exists.' }
    $null = New-Item -ItemType Directory -Force -Path $StateDirectory
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $settings = foreach ($name in @('WP07_INIT_PASSWORD','WP07_MAINT_PASSWORD','WP07_RUNTIME_PASSWORD','WP07_OBJECT_ACCESS_KEY','WP07_OBJECT_SECRET_KEY')) {
            $size = if ($name -eq 'WP07_OBJECT_ACCESS_KEY') { 10 } else { 24 }
            $bytes = New-Object byte[] $size
            $rng.GetBytes($bytes)
            "$name=$([BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant())"
        }
        $stream = [IO.File]::Open($PrivateEnvPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {
            $raw = [Text.Encoding]::ASCII.GetBytes(($settings -join "`n") + "`n")
            $stream.Write($raw,0,$raw.Length)
        } finally { $stream.Dispose() }
    } finally { $rng.Dispose() }
}

$saved = @{}
$ownedEnv = @('SUPPORT_EMAIL')
$deploymentId = ''
$replaced = $false
$closedByUs = $false
$resume = $false
$oldImages = @{}
$oldWorker = @{}
$preserved = @{}
$originalIds = @{}
$originalEnvironments = @{}
$supportEmail = ''
try {
    foreach ($entry in (Get-ChildItem Env: | Where-Object { $_.Name -match '^(WP07_|VSA_S312_|COMPOSE_)' -or $_.Name -in $ownedEnv })) {
        $saved[$entry.Name] = $entry.Value
        [Environment]::SetEnvironmentVariable($entry.Name,$null,'Process')
    }
    $env:COMPOSE_DISABLE_ENV_FILE = '1'
    Confirm-Files
    if ($Action -eq 'Start') {
        $existing = @(Invoke-Docker @('ps','-aq','--filter',"label=com.docker.compose.project=$ProjectName") 'existing isolated project')
        if ($existing.Count -and $existing.Count -ne $images.Count) {
            throw 'The isolated project is incomplete. Inspect it or choose a fresh isolated project name.'
        }
        $resume = $existing.Count -gt 0
    }
    if (!$resume) { $workerSettings = Read-ModelSettings }
    Confirm-Images
    $hashCode = "import hashlib,pathlib; print('\n'.join(p.name+'|'+hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pathlib.Path('/srv/app/migrations').glob('[0-9][0-9][0-9]_*.sql'))))"
    Confirm-Migrations (Invoke-Docker @('run','--rm','--network','none','--pull','never','--entrypoint','python',$images.api.tag,'-c',$hashCode) 'approved migration bytes')
    if ($Action -eq 'Start' -and !$resume) { New-IsolatedEnvironment }
    if (!(Test-Path -LiteralPath $PrivateEnvPath -PathType Leaf)) { throw 'The existing private Compose environment file is required.' }
    $compose = @('compose','--project-name',$ProjectName,'--env-file',$PrivateEnvPath,
        '-f',(Join-Path $PSScriptRoot 'compose.wp07.yaml'),'-f',(Join-Path $PSScriptRoot 'compose.s3-12.yaml'))
    if (!$formal) { $compose += @('-f',(Join-Path $PSScriptRoot 'compose.s3-12-isolated.yaml')) }

    if ($Action -ne 'Start' -or $resume) {
        foreach ($service in $images.Keys) {
            $container = Read-Container $service
            $originalIds[$service] = $container.Id
            $originalEnvironments[$service] = Read-ContainerEnvironment $container
            if ($service -in $appServices) {
                $permitted = @($images[$service].id)
                if ($formal -or $Action -eq 'Update') { $permitted += $manifest.formal_previous.PSObject.Properties[$service].Value }
                if ($container.Image -cnotin $permitted -or
                    (Invoke-Docker @('image','inspect','--format','{{.Id}}',$container.Config.Image) 'rollback image identity') -cne $container.Image) {
                    throw "Unrecognized image for update or rollback: $service"
                }
                $oldImages[$service] = @{id=$container.Image;tag=$container.Config.Image}
            } else {
                $oldImages[$service] = $images[$service]
                $preserved[$service] = $container.Id
            }
            if ($service -eq 'api') { $supportEmail = [string]$originalEnvironments[$service].SUPPORT_EMAIL }
            if ($service -eq 'worker') {
                $running = $originalEnvironments[$service]
                $oldWorker = @{
                    VSA_S312_MODE=[string]$running.WORKER_PROCESSOR_MODE
                    VSA_S312_MODEL_CALLS=[string]$running.MODEL_CALLS_ENABLED
                    VSA_S312_TIMEOUT=[string]$running.MODEL_ADAPTER_TIMEOUT_SECONDS
                    VSA_S312_KEY=[string]$running.DASHSCOPE_API_KEY
                }
                foreach ($field in $ossFields) { $oldWorker["VSA_S312_OSS_$field"] = [string]$running["QWEN_OSS_$field"] }
            }
        }
        Confirm-Stack $oldImages $preserved -AllowStopped:$resume
        if ($resume) {
            Set-StackEnvironment $oldImages $oldWorker $supportEmail
            Confirm-PrivateSettings $originalEnvironments
            $null = Invoke-Docker @('start',$originalIds.db,$originalIds['object-store']) 'resume isolated storage'
            Wait-Dependencies
            Confirm-Migrations (Read-Ledger)
            $state = Invoke-Gate 'status'
            $deploymentId = $state.deployment_id
            $null = Invoke-Docker (@('start') + @($appServices | ForEach-Object {$originalIds[$_]})) 'resume isolated app containers'
            Confirm-Stack $oldImages $originalIds
            Wait-Http '/api/health/live' 200
            $ready = if ($state.state -eq 'OPEN') {200} else {503}
            Wait-Http '/api/health/ready' $ready
            Confirm-Worker $oldWorker
            Write-Output "S3-12 resumed. Project: $ProjectName. Worker: $($oldWorker.VSA_S312_MODE). Gate: $($state.state). Web: http://127.0.0.1:$HttpPort/"
            return
        }
        Confirm-Migrations (Read-Ledger)
    }

    Set-StackEnvironment $images $workerSettings $supportEmail
    $null = Invoke-Docker ($compose + @('config','--quiet')) 'Compose configuration'
    if ($Action -ne 'Start') { Confirm-PrivateSettings $originalEnvironments }
    if ($Action -eq 'Start') {
        $null = Invoke-Docker ($compose + @('up','-d','--no-build','--pull','never','--wait','--wait-timeout','90','db','object-store')) 'new isolated database and object store'
        if ((Invoke-Docker @('network','inspect','--format','{{.Internal}}',"${ProjectName}_private") 'isolated network') -cne 'true') {
            throw 'Isolated project must have no network egress.'
        }
        foreach ($service in @('volume-init','initialize')) {
            $null = Invoke-Docker ($compose + @('--profile','initialize','run','--rm','--no-deps','--pull','never',$service)) "new instance $service"
        }
        Confirm-Migrations (Read-Ledger)
    }
    $before = Invoke-Gate 'status'
    $deploymentId = $before.deployment_id
    $restoreOpen = $Action -eq 'Start' -or $before.state -eq 'OPEN'
    if ($Action -ne 'Start') { Confirm-Idle }
    if ($before.state -eq 'OPEN') {
        if ((Invoke-Gate 'close').state -cne 'CLOSED') { throw 'Could not close maintenance gate.' }
        $closedByUs = $true
    }
    if ($Action -ne 'Start') { Confirm-Idle }
    $replaced = $true
    $null = Invoke-Docker ($compose + @('up','-d','--no-deps','--no-build','--pull','never') + $appServices) 'apply API, Worker and Web'
    Confirm-Stack $images $preserved
    Confirm-Migrations (Read-Ledger)
    Wait-Http '/api/health/live' 200
    Wait-Http '/api/health/ready' 503
    Confirm-Worker $workerSettings
    if ($restoreOpen) {
        if ((Invoke-Gate 'open').state -cne 'OPEN') { throw 'Could not restore OPEN.' }
        Wait-Http '/api/health/ready' 200
    }
    $state = if ($restoreOpen) {'OPEN'} else {'CLOSED'}
    Write-Output "S3-12 $Action completed. Project: $ProjectName. Worker: $($workerSettings.VSA_S312_MODE). Gate: $state. Web: http://127.0.0.1:$HttpPort/"
} catch {
    $failure = $_.Exception.Message
    if ($Action -ne 'Start' -and ($replaced -or $closedByUs)) {
        try {
            if ($replaced) {
                if ((Invoke-Gate 'status').state -eq 'OPEN') { $null = Invoke-Gate 'close' }
                Set-StackEnvironment $oldImages $oldWorker $supportEmail
                $null = Invoke-Docker ($compose + @('up','-d','--no-deps','--no-build','--pull','never') + $appServices) 'restore prior app images'
                Confirm-Stack $oldImages $preserved
                Wait-Http '/api/health/live' 200
            }
            if ($closedByUs -and (Invoke-Gate 'status').state -eq 'CLOSED') {
                $null = Invoke-Gate 'open'
                Wait-Http '/api/health/ready' 200
            }
            Write-Warning 'The previous deployment and maintenance state were restored.'
        } catch { Write-Warning 'Rollback could not be verified. Check the gate and containers before continuing.' }
    } elseif ($Action -eq 'Start' -and !$resume -and $deploymentId) {
        try { if ((Invoke-Gate 'status').state -eq 'OPEN') { $null = Invoke-Gate 'close' } } catch {}
    }
    throw $failure
} finally {
    foreach ($entry in (Get-ChildItem Env: | Where-Object { $_.Name -match '^(WP07_|VSA_S312_|COMPOSE_)' -or $_.Name -in $ownedEnv })) {
        [Environment]::SetEnvironmentVariable($entry.Name,$null,'Process')
    }
    foreach ($entry in $saved.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key,$entry.Value,'Process')
    }
    if ($workerSettings) { $workerSettings.Clear() }
    $oldWorker.Clear()
    $originalEnvironments.Clear()
}
