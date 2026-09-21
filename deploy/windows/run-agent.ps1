# Runs the NetForge agent after loading its environment.
# Usage:  powershell -ExecutionPolicy Bypass -File run-agent.ps1 [--port 8081]
# Register this script as a service with NSSM or a Scheduled Task (see docs/deployment.md).

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\agent.env.ps1"

# Interpreter that has NetForge installed. Override with NETFORGE_PYTHON if needed.
$python = if ($env:NETFORGE_PYTHON) { $env:NETFORGE_PYTHON } else { "python" }

# 0.0.0.0 exposes the bearer-authenticated API: pass --certfile <pem> through
# @args so the token is not sent in cleartext, or bind 127.0.0.1 behind a proxy.
& $python -m agent --host 0.0.0.0 @args
exit $LASTEXITCODE
