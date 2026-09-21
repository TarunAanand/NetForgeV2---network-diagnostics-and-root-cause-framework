# NetForge controller environment for Windows.
# Edit the values, then run-controller.ps1 dot-sources this file before starting.
# Treat this file as a secret (it holds bearer tokens).

$env:NETFORGE_CONTROLLER_TOKEN = "change-me-controller-secret"
$env:NETFORGE_AGENT_TOKEN      = "change-me-shared-agent-secret"
$env:NETFORGE_CONTROLLER_DB    = "$env:ProgramData\netforge\controller.db"
