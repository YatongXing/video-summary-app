param(
  [Parameter(Mandatory=$true)][string]$State,
  [Parameter(Mandatory=$true)][string]$Events,
  [string]$Python = "python"
)
# This starts no service or scheduled task and sends only local test-file events.
# Production notification channel and recipient require separate confirmation.
$Monitor = Join-Path $PSScriptRoot '..\backend\tools\wp07_monitor.py'
& $Python $Monitor --state $State --events $Events --interval 30 --repeat-seconds 60
exit $LASTEXITCODE
