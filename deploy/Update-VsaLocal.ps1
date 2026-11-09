[CmdletBinding(SupportsShouldProcess)]
param([string]$PrivateEnvPath)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$release = Join-Path $repo 'release/s3-12-local'
$launcher = Join-Path $release 'deploy/Start-S3-12.ps1'
$modelEnv = Join-Path $repo '.env'
$manifest = Get-Content -Raw -LiteralPath (Join-Path $release 'manifest.json') | ConvertFrom-Json
$digest = [Security.Cryptography.SHA256]::Create()
$stream = [IO.File]::OpenRead($launcher)
try {
    $hash = [BitConverter]::ToString($digest.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
} finally {
    $stream.Dispose()
    $digest.Dispose()
}
if ($hash -cne $manifest.launcher_sha256) {
    throw 'S3-12 launcher does not match the release manifest.'
}

if (!$PrivateEnvPath) {
    $identity = & docker --context desktop-linux inspect --type container --format '{{json .Config.Labels}}' vsa-local-api-1
    if ($LASTEXITCODE -ne 0 -or !$identity) { throw 'Cannot locate the existing vsa-local API container.' }
    $labels = $identity | ConvertFrom-Json
    if ($labels.'com.docker.compose.project' -cne 'vsa-local' -or $labels.'com.docker.compose.service' -cne 'api') {
        throw 'Unexpected Compose project or service.'
    }
    $PrivateEnvPath = $labels.'com.docker.compose.project.environment_file'
}
if (!$PrivateEnvPath -or !(Test-Path -LiteralPath $PrivateEnvPath -PathType Leaf)) {
    throw 'Cannot locate the private Compose configuration. Supply its path with -PrivateEnvPath.'
}
if (!(Test-Path -LiteralPath $modelEnv -PathType Leaf)) { throw 'The repository model .env is missing.' }

if ($PSCmdlet.ShouldProcess('vsa-local', 'Apply the verified S3-12 release and repository model settings')) {
    & $launcher -Action UpgradeFormal -PrivateEnvPath $PrivateEnvPath -ModelEnvPath $modelEnv
}
