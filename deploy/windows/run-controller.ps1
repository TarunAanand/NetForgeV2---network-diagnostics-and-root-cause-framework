# Runs the NetForge controller (with the background monitor) after loading its environment.
# Usage:  powershell -ExecutionPolicy Bypass -File run-controller.ps1 [--port 8080]
# Register this script as a service with NSSM or a Scheduled Task (see docs/deployment.md).

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\controller.env.ps1"

# Ensure the state directory exists for NETFORGE_CONTROLLER_DB.
$dbDir = Split-Path -Parent $env:NETFORGE_CONTROLLER_DB
if ($dbDir -and -not (Test-Path $dbDir)) { New-Item -ItemType Directory -Path $dbDir -Force | Out-Null }

# Interpreter that has NetForge installed. Override with NETFORGE_PYTHON if needed.
$python = if ($env:NETFORGE_PYTHON) { $env:NETFORGE_PYTHON } else { "python" }

# 0.0.0.0 exposes the bearer-authenticated API: pass --certfile <pem> through
# @args so the token is not sent in cleartext, or bind 127.0.0.1 behind a proxy.
& $python -m controller --host 0.0.0.0 --monitor --poll-interval 5 @args
exit $LASTEXITCODE
