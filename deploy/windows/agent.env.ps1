# NetForge agent environment for Windows.
# Edit the values, then run-agent.ps1 dot-sources this file before starting.
# Treat this file as a secret (it holds a bearer token).

$env:NETFORGE_AGENT_ID     = "agent-db-1"
$env:NETFORGE_AGENT_TOKEN  = "change-me-shared-agent-secret"
$env:NETFORGE_ALLOWED_TARGETS = "10.0.0.0/24,1.1.1.1,8.8.8.8"
$env:NETFORGE_TAG_site     = "lab"
$env:NETFORGE_TAG_rack     = "r2"
