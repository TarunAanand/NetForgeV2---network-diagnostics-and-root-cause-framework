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

& $python -m controller --host 0.0.0.0 --monitor --poll-interval 5 @args
exit $LASTEXITCODE
