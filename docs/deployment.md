# NetForge Deployment Guide

How to run NetForge **agents** and the **controller** as long-lived background
services on Linux (systemd) and Windows (NSSM or Task Scheduler).

For the command reference, tokens, and data-file schemas, see
[`USAGE.md`](../USAGE.md). This document focuses on turning the processes into
daemons.

---

## Files in this guide

```
deploy/
├── systemd/
│   ├── netforge-agent.service         # systemd unit for an agent
│   ├── netforge-controller.service    # systemd unit for the controller
│   ├── agent.env.example              # env template -> /etc/netforge/agent.env
│   └── controller.env.example         # env template -> /etc/netforge/controller.env
└── windows/
    ├── run-agent.ps1                  # loads env, starts the agent
    ├── run-controller.ps1             # loads env, starts the controller (+monitor)
    ├── agent.env.ps1                  # agent env (edit me)
    └── controller.env.ps1             # controller env (edit me)
```

Both units/scripts assume NetForge is installed (`pip install .`) so that
`python -m agent` and `python -m controller` resolve. There is **no console
script** for the agent/controller — only `netforge` (the CLI) — so services
invoke the modules with `python -m`.

### Token recap (critical)

| Token | Env var | Shared by |
|-------|---------|-----------|
| Controller token | `NETFORGE_CONTROLLER_TOKEN` | CLI clients → controller |
| Agent token | `NETFORGE_AGENT_TOKEN` | controller → **every** agent (must match exactly) |

Generate strong random values and keep the env files secret (mode `0600` on
Linux; ACL-restricted on Windows).

---

## Linux (systemd)

### 1. Install NetForge

```bash
sudo useradd --system --home /opt/netforge --shell /usr/sbin/nologin netforge
sudo mkdir -p /opt/netforge /etc/netforge /var/lib/netforge
# Copy or clone the repo into /opt/netforge, then:
cd /opt/netforge
sudo python3 -m venv venv
sudo ./venv/bin/pip install .
sudo chown -R netforge:netforge /opt/netforge /var/lib/netforge
```

> If you use the virtualenv (recommended), edit the unit's `ExecStart` to
> `/opt/netforge/venv/bin/python -m agent ...` (and the same for the controller).

### 2. Agent host

```bash
sudo cp deploy/systemd/agent.env.example /etc/netforge/agent.env
sudo chown root:netforge /etc/netforge/agent.env
sudo chmod 0640 /etc/netforge/agent.env
sudoedit /etc/netforge/agent.env          # set NETFORGE_AGENT_ID + token

sudo cp deploy/systemd/netforge-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netforge-agent
sudo systemctl status netforge-agent
journalctl -u netforge-agent -f           # follow logs
```

Repeat on every remote machine, giving each a unique `NETFORGE_AGENT_ID` but the
**same** `NETFORGE_AGENT_TOKEN`.

### 3. Controller host

```bash
sudo cp deploy/systemd/controller.env.example /etc/netforge/controller.env
sudo chown root:netforge /etc/netforge/controller.env
sudo chmod 0640 /etc/netforge/controller.env
sudoedit /etc/netforge/controller.env     # set both tokens + DB path

sudo cp deploy/systemd/netforge-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netforge-controller
sudo systemctl status netforge-controller
```

The unit starts the controller with `--monitor --poll-interval 5`, so due
schedules fire automatically. Drop `--monitor` from `ExecStart` if you prefer to
trigger runs manually with `netforge controller monitor run`.

### 4. Verify from the CLI

```bash
export NETFORGE_CONTROLLER_URL="http://<controller-host>:8080"
export NETFORGE_CONTROLLER_TOKEN="<controller token>"
netforge controller health
netforge controller agent list
```

### Firewall

```bash
# controller host
sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload
# agent hosts
sudo firewall-cmd --add-port=8081/tcp --permanent && sudo firewall-cmd --reload
```

(Or the equivalent `ufw allow 8080/tcp` / `ufw allow 8081/tcp`.)

---

## Windows

NetForge has no native Windows service wrapper, so use one of:

- **NSSM** (Non-Sucking Service Manager) — runs the wrapper scripts as real
  services with automatic restart. Download from nssm.cc.
- **Task Scheduler** — no extra download; runs the wrapper at startup.

First install NetForge into a venv and edit the env scripts:

```powershell
cd C:\netforge
py -m venv venv
.\venv\Scripts\pip install .
notepad deploy\windows\agent.env.ps1        # or controller.env.ps1
```

Set `NETFORGE_PYTHON` in the env script if `python` on PATH is not the venv
interpreter, e.g. `$env:NETFORGE_PYTHON = "C:\netforge\venv\Scripts\python.exe"`.

Test the wrapper in the foreground first:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\run-agent.ps1
# Ctrl+C to stop
```

### Option A — NSSM (recommended)

```powershell
nssm install netforge-agent "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
  "-ExecutionPolicy Bypass -File C:\netforge\deploy\windows\run-agent.ps1"
nssm set netforge-agent AppDirectory C:\netforge
nssm set netforge-agent Start SERVICE_AUTO_START
nssm start netforge-agent

nssm install netforge-controller "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
  "-ExecutionPolicy Bypass -File C:\netforge\deploy\windows\run-controller.ps1"
nssm set netforge-controller AppDirectory C:\netforge
nssm set netforge-controller Start SERVICE_AUTO_START
nssm start netforge-controller
```

Manage with `nssm restart|stop|remove <name>`.

### Option B — Task Scheduler (run at startup)

Run PowerShell **as Administrator**:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-ExecutionPolicy Bypass -File C:\netforge\deploy\windows\run-agent.ps1" `
  -WorkingDirectory "C:\netforge"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "NetForge Agent" `
  -Action $action -Trigger $trigger -Principal $principal
Start-ScheduledTask -TaskName "NetForge Agent"
```

Repeat with `run-controller.ps1` for the controller. Note: a scheduled task does
not auto-restart on crash the way NSSM does — prefer NSSM for production.

### Firewall

```powershell
New-NetFirewallRule -DisplayName "NetForge Controller" -Direction Inbound `
  -Protocol TCP -LocalPort 8080 -Action Allow
New-NetFirewallRule -DisplayName "NetForge Agent" -Direction Inbound `
  -Protocol TCP -LocalPort 8081 -Action Allow
```

---

## Security notes

- **No TLS in this build.** Agent/controller traffic is plain HTTP with bearer
  tokens. Deploy on a trusted network, or terminate TLS in front of them
  (reverse proxy) or wrap connections in an SSH tunnel:
  ```bash
  ssh -L 8080:localhost:8080 user@controller-host
  netforge controller --url http://127.0.0.1:8080 health
  ```
- **Least privilege.** Run agents as a non-root service user (the systemd unit
  already hardens with `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`).
- **Restrict probe targets.** Set `NETFORGE_ALLOWED_TARGETS` on agents so a
  compromised controller cannot use them to probe arbitrary hosts.
- **Protect the env files.** They contain the shared secrets — `0600`/`0640` on
  Linux, ACL-restricted on Windows. Never commit filled-in copies.
- **Bind deliberately.** `--host 0.0.0.0` exposes the service to the network;
  use `127.0.0.1` plus an SSH tunnel when only local/remote-forwarded access is
  needed.

---

## Upgrade & rollback

```bash
# Linux (systemd)
cd /opt/netforge && sudo ./venv/bin/pip install .
sudo systemctl restart netforge-agent netforge-controller

# The controller DB is additive; back it up before upgrading:
sudo cp /var/lib/netforge/controller.db /var/lib/netforge/controller.db.bak
```

```powershell
# Windows (NSSM)
cd C:\netforge; .\venv\Scripts\pip install .
nssm restart netforge-agent; nssm restart netforge-controller
Copy-Item "$env:ProgramData\netforge\controller.db" "$env:ProgramData\netforge\controller.db.bak"
```

---

_Back to the [Usage Guide](../USAGE.md)._
