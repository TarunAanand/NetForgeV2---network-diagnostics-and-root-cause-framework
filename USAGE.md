# NetForge Usage Guide

NetForge is a Python network-diagnostics and root-cause framework. It runs as a
local CLI for single-machine diagnostics, and as a distributed **agent +
controller** system for multi-machine, multi-vantage diagnosis, monitoring,
alerting, and host-to-switch-port correlation.

This guide covers installation, every CLI command, how to connect other machines
on the same network, the controller workflows, data-file schemas, passive
telemetry ingest (including the bundled SNMPv3 engine), and how to run the tests.

---

## Table of contents

1. [Architecture at a glance](#architecture-at-a-glance)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [CLI reference](#cli-reference)
   - [host](#netforge-host) · [link](#netforge-link) · [path](#netforge-path) ·
     [traffic](#netforge-traffic) · [flow](#netforge-flow) ·
     [mesh](#netforge-mesh) · [diagnose](#netforge-diagnose) ·
     [controller](#netforge-controller)
5. [Connecting other machines (agents + controller)](#connecting-other-machines)
6. [Controller workflows end to end](#controller-workflows-end-to-end)
7. [Data file schemas](#data-file-schemas)
8. [Passive telemetry ingest (SNMP / LLDP / flows)](#passive-telemetry-ingest)
9. [Running tests](#running-tests)
10. [Configuration reference (environment variables)](#configuration-reference)
11. [Good-to-have features](#good-to-have-features)
12. [Exit codes and troubleshooting](#exit-codes-and-troubleshooting)

---

## Architecture at a glance

```
                         ┌──────────────────────────────────────────┐
   netforge <cmd>        │  Controller  (python -m controller)       │
  ───────────────►       │  HTTP :8080, bearer-token auth            │
   local CLI             │  • agent registry   • topology / services │
   (this machine)        │  • multi-vantage diagnosis (M4)           │
                         │  • schedules / alerts / incidents (M5)    │
                         │  • host↔switch-port correlation (M6)      │
                         └───────────────┬──────────────────────────┘
                                         │ fan-out probe jobs (HTTP)
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                     ▼
              ┌──────────┐         ┌──────────┐          ┌──────────┐
              │ Agent    │         │ Agent    │          │ Agent    │
              │ :8081    │         │ :8081    │          │ :8081    │
              │ host A   │         │ host B   │          │ host C   │
              └──────────┘         └──────────┘          └──────────┘
```

- **Local CLI** (`netforge ...`) runs diagnostics directly on the current
  machine. No server required.
- **Agent** (`python -m agent`) is a small authenticated HTTP service on each
  remote machine. It exposes allow-listed probes (ICMP, TCP, DNS, traceroute,
  interfaces, route, gateway).
- **Controller** (`python -m controller`) registers agents, fans out probe jobs
  to them concurrently, and layers diagnosis, monitoring, alerting, incidents,
  and device correlation on top. State is persisted in a local SQLite file.
- **`netforge controller ...`** subcommands are a *client* of a running
  controller — they let you drive the whole distributed system from the CLI
  instead of raw HTTP.

Everything is pure Python with a deliberately small dependency set
(`typer`, `rich`, `pydantic`, `psutil`). HTTP servers use the standard library;
SNMPv3 uses a bundled, self-contained engine — **no third-party SNMP/ASN.1 or
native dependencies**.

---

## Installation

Requires **Python ≥ 3.10**.

```bash
# From the repository root
pip install -e ".[dev]"      # editable install + pytest (dev extra)
```

This registers the `netforge` console script. Verify:

```bash
netforge --help
```

If you prefer not to install, you can run the CLI directly from the repo root:

```bash
python -m cli --help
```

> **Windows / virtualenv note.** If you use a project virtualenv (e.g. `venv\`),
> invoke its interpreter explicitly so you get the installed dependencies:
> `venv\Scripts\python.exe -m cli ...` and
> `venv\Scripts\python.exe -m pytest`.

---

## Quick start

```bash
# 1. Full local host diagnostic suite with a summary
netforge host all

# 2. Root-cause diagnosis of this host via the rule engine
netforge diagnose host --target google.com

# 3. Traceroute to a target
netforge path trace --target 1.1.1.1

# 4. Link utilization / errors / congestion
netforge link all

# 5. Download goodput estimate
netforge traffic speed
```

Every command group prints rich, color-coded tables and panels. Add `--json`
where supported to emit machine-readable output, and `--strict` to turn an
unhealthy result into a non-zero exit code (useful in CI).

---

## CLI reference

Top-level help:

```bash
netforge --help
```

Command groups:

| Group | Purpose |
|-------|---------|
| `host` | Host-level (node/endpoint) diagnostics |
| `link` | Link-level interface diagnostics |
| `path` | Path-level traceroute diagnostics |
| `traffic` | Traffic / bandwidth / jitter probes |
| `flow` | Flow analysis over passive telemetry |
| `mesh` | Ping-mesh / distributed observations |
| `diagnose` | Intelligent root-cause diagnosis (rule engine) |
| `controller` | Drive a running controller over HTTP |

Get help for any group or command:

```bash
netforge host --help
netforge host all --help
netforge controller --help
```

### `netforge host`

Host-level diagnostics for the local machine.

| Command | Description | Key options |
|---------|-------------|-------------|
| `connectivity` | Check basic network connectivity | — |
| `interface` | Inspect network interfaces | — |
| `routing` | Routing table + default gateway | `--verbose/-v` |
| `gateway` | Probe default gateway reachability | `--count/-c` (default 4) |
| `dns` | Resolve a hostname, list DNS servers | `--hostname/-h` (default `google.com`) |
| `transport` | Test TCP/UDP to common ports | `--host` (default `1.1.1.1`) |
| `packet-loss` | Measure packet loss | `--count/-c` (default 5) |
| `latency` | Measure latency and jitter | `--count/-c` (default 5) |
| `resources` | Host resources + network activity | — |
| `all` | Full host suite + summary | `--strict`, `--diagnose`, `--target/-t` |

Examples:

```bash
netforge host all
netforge host all --diagnose --strict --target 10.0.0.1
netforge host latency --count 20
netforge host dns --hostname internal.corp
```

`host all --diagnose` additionally runs the rule engine and renders a full
root-cause report.

### `netforge link`

| Command | Description | Key options |
|---------|-------------|-------------|
| `util` | Per-interface utilization | `--interval/-i` (seconds, default 1.0) |
| `errors` | Per-interface drops and errors | `--interval/-i` |
| `all` | Utilization + errors + congestion | `--interval/-i` |

```bash
netforge link all
netforge link util --interval 5
```

### `netforge path`

| Command | Description | Key options |
|---------|-------------|-------------|
| `trace` | Traceroute to a target | `--target/-t`, `--max-hops/-m` (default 30) |
| `hops` | Aggregate per-hop latency and loss | `--target/-t`, `--probes/-p` (default 2) |
| `diff` | Detect path change vs stored baseline | `--target/-t` |
| `all` | Traceroute + path-change detection | `--target/-t` |

```bash
netforge path trace --target 8.8.8.8 --max-hops 20
netforge path hops --target 1.1.1.1 --probes 3
netforge path diff --target 1.1.1.1
```

`path diff` / `path all` compare the current path against a stored baseline
(SQLite history) and flag changes.

### `netforge traffic`

| Command | Description | Key options |
|---------|-------------|-------------|
| `speed` | Estimate download goodput | `--url/-u` (default Cloudflare 5 MB) |
| `jitter` | RFC 3550 jitter | `--host/-h`, `--count/-c` (default 10) |
| `bandwidth` | Current interface bandwidth sample | `--interval/-i` |

```bash
netforge traffic speed
netforge traffic jitter --host 10.0.0.1 --count 20
netforge traffic bandwidth --interval 2
```

### `netforge flow`

Offline analysis of flow exports (NetFlow/sFlow/IPFIX rendered to JSON/JSONL).

| Command | Description | Arguments / options |
|---------|-------------|---------------------|
| `top` | Top talkers from a flow export | `PATH` (JSON/JSONL) |
| `analyze` | Elephants + top talkers | `PATH`, `--json` |

```bash
netforge flow top examples/flows_sample.json
netforge flow analyze examples/flows_sample.json --json
```

The sample file `examples/flows_sample.json` is a JSON array of records with
`src`, `dst`, `bytes`, `packets`, `proto`.

### `netforge mesh`

| Command | Description | Key options |
|---------|-------------|-------------|
| `run` | Local ping mesh to many targets | `--targets/-t` (comma-separated), `--topology` (JSON file), `--count/-c` |
| `status` | Mesh/agent capability status | — |

```bash
netforge mesh run --targets 1.1.1.1,8.8.8.8,9.9.9.9 --count 3
netforge mesh run --topology examples/mesh_topology.json
netforge mesh status
```

### `netforge diagnose`

Root-cause diagnosis via the rule engine. Each command collects observations,
runs `RuleEngine`, and renders a prioritized report.

| Command | Description | Key options |
|---------|-------------|-------------|
| `host` | Host-layer diagnosis | `--target/-t`, `--json`, `--strict` |
| `path` | Path-layer diagnosis | `--target/-t`, `--json`, `--strict` |
| `link` | Link-layer diagnosis | `--interval/-i`, `--json`, `--strict` |
| `all` | Merged host + link + path | `--target/-t`, `--json`, `--strict` |

```bash
netforge diagnose host --target google.com
netforge diagnose all --target 10.0.0.1 --strict
netforge diagnose path --target 1.1.1.1 --json > report.json
```

- `--json` prints the full `DiagnosisReport` as JSON (great for piping/archiving).
- `--strict` exits `1` if the report status is not `HEALTHY`.
- Without `--strict`, a `FAILED` status still exits `1`.

### `netforge controller`

Client commands that talk to a **running controller** over HTTP. Group-level
options (must come right after `controller`) set the endpoint and token:

```bash
netforge controller [--url/-u URL] [--token/-t TOKEN] <subcommand> ...
```

- `--url/-u` defaults to `http://127.0.0.1:8080` (env `NETFORGE_CONTROLLER_URL`).
- `--token/-t` defaults to empty (env `NETFORGE_CONTROLLER_TOKEN`). The token
  must match the controller's `NETFORGE_CONTROLLER_TOKEN` or you get a `401`.

Set them once per shell instead of repeating:

```powershell
# PowerShell
$env:NETFORGE_CONTROLLER_URL   = "http://controller-host:8080"
$env:NETFORGE_CONTROLLER_TOKEN = "super-secret-token"
```

```bash
# bash / zsh
export NETFORGE_CONTROLLER_URL="http://controller-host:8080"
export NETFORGE_CONTROLLER_TOKEN="super-secret-token"
```

Subcommand groups:

| Group / command | Description |
|-----------------|-------------|
| `controller health` | Check controller reachability |
| `controller agent register AGENT_ID URL [--tag k=v]...` | Register a remote agent |
| `controller agent list` | List registered agents |
| `controller topology import PATH` | Import + validate a topology JSON file |
| `controller topology list` | List known topology names |
| `controller service register PATH [--topology NAME]` | Register a service inventory |
| `controller service list` | List registered services |
| `controller diagnose SERVICE_ID [opts]` | Run a multi-vantage diagnosis (M4) |
| `controller diagnoses` | List stored diagnoses |
| `controller monitor run [--json]` | Run all due schedules now (M5) |
| `controller schedule list [--service ID]` | List schedules |
| `controller schedule add SERVICE_ID --interval S [opts]` | Create a schedule |
| `controller schedule remove SCHEDULE_ID` | Delete a schedule |
| `controller alert list [--service ID] [--state firing\|resolved]` | List alerts |
| `controller alert rule-list` | List alert rules |
| `controller alert rule-add [opts]` | Register an alert rule |
| `controller incident list [--service ID] [--state ...]` | List incidents |
| `controller incident show INCIDENT_ID` | Show one incident |
| `controller incident ack INCIDENT_ID` | Acknowledge an incident |
| `controller incident resolve INCIDENT_ID` | Resolve an incident |
| `controller correlate TOPOLOGY TELEMETRY_FILE [--json]` | Correlate hosts↔switch ports (M6) |
| `controller binding list [TOPOLOGY]` | List stored host-to-switch-port bindings |
| `controller augment TOPOLOGY [--json]` | Add host→switch LAN edges into a topology |

Notable option details:

- `controller diagnose SERVICE_ID`:
  `--probe-type/-p {icmp|tcp|udp|dns|http}`, `--count/-c` (default 3),
  `--source-node` (pin the source vantage), `--topology`, `--json`,
  `--strict` (exit `1` unless localization is `HEALTHY`).
- `controller schedule add SERVICE_ID`:
  `--interval/-i` (seconds, **required**), `--count/-c`, `--probe-type/-p`,
  `--source-node`, `--topology`.
- `controller alert rule-add`:
  `--name/-n`, `--min-confidence` (0.0–1.0), `--severity`, `--service/-s`
  (scope to one service; omit for all), `--fire-on` (comma-separated
  localizations; omit to fire on any unhealthy localization).
- `controller correlate TOPOLOGY TELEMETRY_FILE`: the telemetry file is a JSON
  object with a `switches` array (see [Data file schemas](#data-file-schemas)).

---

## Connecting other machines

To diagnose across machines on the same network you run an **agent** on each
remote host and one **controller** that coordinates them. Agents and the
controller authenticate with shared bearer tokens.

### Tokens (read this first)

There are two independent tokens:

| Token | Env var | Purpose |
|-------|---------|---------|
| Controller token | `NETFORGE_CONTROLLER_TOKEN` | Clients (CLI / `netforge controller`) authenticate *to the controller*. |
| Agent token | `NETFORGE_AGENT_TOKEN` | The controller authenticates *to each agent*, and each agent validates incoming probes. **The controller's agent token must match every agent's token.** |

Use long random strings. On any machine:

```powershell
# PowerShell
$env:NETFORGE_CONTROLLER_TOKEN = "ctrl-" + (-join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_}))
$env:NETFORGE_AGENT_TOKEN     = "agent-fixed-shared-secret"
```

```bash
# bash / zsh
export NETFORGE_CONTROLLER_TOKEN="ctrl-$(openssl rand -hex 16)"
export NETFORGE_AGENT_TOKEN="agent-fixed-shared-secret"
```

### Step 1 — Start an agent on each remote machine

On every remote host, install NetForge, then set its identity and start it:

```bash
export NETFORGE_AGENT_ID="agent-db-1"          # unique per machine
export NETFORGE_AGENT_TOKEN="agent-fixed-shared-secret"   # must match controller
export NETFORGE_ALLOWED_TARGETS="10.0.0.0/24,1.1.1.1,8.8.8.8"   # optional allow-list
export NETFORGE_TAG_site="lab"                 # optional topology tags (NETFORGE_TAG_*)
export NETFORGE_TAG_rack="r2"

python -m agent --host 0.0.0.0 --port 8081 --certfile /etc/netforge/agent.pem
```

Agent options:

| Option | Default | Meaning |
|--------|---------|---------|
| `--host` | `127.0.0.1` | Bind address. Pass `0.0.0.0` so other machines can reach it — only with TLS or a trusted network boundary. |
| `--port` | `8081` | TCP port. |
| `--certfile` | none | PEM certificate (chain). Supplying it serves the API over TLS 1.2+. |
| `--keyfile` | `--certfile` | PEM private key, when it is not bundled in the certificate file. |

The bearer token is sent on every request, so exposing an agent beyond
`127.0.0.1` without TLS (either `--certfile` or a TLS-terminating proxy) leaks
the shared secret to anyone on the path.

Agent environment variables:

| Variable | Required | Meaning |
|----------|----------|---------|
| `NETFORGE_AGENT_ID` | yes | Unique agent id. |
| `NETFORGE_AGENT_TOKEN` | yes | Bearer token the controller must present. |
| `NETFORGE_ALLOWED_TARGETS` | no | Comma-separated allow-list for *targeted* probes (ICMP/TCP/DNS/traceroute). If empty, targets are unrestricted. |
| `NETFORGE_TAG_<name>` | no | Arbitrary tags (lowercased `<name>`) attached to the agent, e.g. `NETFORGE_TAG_site=lab`. |

The agent exposes three endpoints (all bearer-authenticated):
`GET /v1/health`, `GET /v1/inventory`, `POST /v1/probe`.

### Step 2 — Start the controller

On the machine that will coordinate the agents:

```bash
export NETFORGE_CONTROLLER_TOKEN="ctrl-secret"           # clients authenticate with this
export NETFORGE_AGENT_TOKEN="agent-fixed-shared-secret"  # controller uses this to call agents
export NETFORGE_CONTROLLER_DB=".netforge_controller.db"  # optional; default shown

python -m controller --host 0.0.0.0 --port 8080 --certfile /etc/netforge/controller.pem
```

Controller options:

| Option | Default | Meaning |
|--------|---------|---------|
| `--host` | `127.0.0.1` | Bind address. Pass `0.0.0.0` only with TLS or a trusted network boundary. |
| `--port` | `8080` | TCP port. |
| `--certfile` | none | PEM certificate (chain). Supplying it serves the API over TLS 1.2+. |
| `--keyfile` | `--certfile` | PEM private key, when it is not bundled in the certificate file. |
| `--monitor` | off | Run the background scheduler that automatically fires *due* service diagnoses. |
| `--poll-interval` | `5.0` | Scheduler poll interval in seconds (only with `--monitor`). |

To have schedules run automatically without calling `monitor run` manually,
start the controller with `--monitor`:

```bash
python -m controller --port 8080 --monitor --poll-interval 5
```

### Step 3 — Register the remote agents with the controller

From any machine with the CLI (set `NETFORGE_CONTROLLER_URL` /
`NETFORGE_CONTROLLER_TOKEN` first, or pass `--url`/`--token`):

```bash
netforge controller health

netforge controller agent register agent-app-1 http://10.0.0.11:8081 --tag site=lab --tag rack=r1
netforge controller agent register agent-db-1  http://10.0.0.21:8081 --tag site=lab --tag rack=r2

netforge controller agent list
```

The URL must be reachable **from the controller** (that is the machine that
fans out probe jobs), not necessarily from your CLI host.

### Network / firewall checklist

- Agents listen on **TCP 8081** by default; the controller on **TCP 8080**.
  Open these between the relevant hosts (controller → agents, CLI → controller).
- Bind with `--host 0.0.0.0` on agents/controller so remote machines can
  connect (the default `127.0.0.1` only allows local access), and pair it with
  `--certfile` so the bearer tokens are not sent in cleartext.
- Request bodies are capped at 1 MiB; larger POSTs are rejected with `413`.
- Probes themselves use ICMP/UDP/TCP/DNS outbound from each agent; make sure
  agents are permitted to send those to the targets you diagnose.
- Authentication is a bearer token on every request. Serve it over TLS with
  `--certfile`, front it with a TLS-terminating proxy, or tunnel it, e.g.:
  ```bash
  ssh -L 8080:localhost:8080 user@controller-host
  netforge controller --url http://127.0.0.1:8080 health
  ```

> **Run as a service.** To keep agents and the controller running as daemons
> (auto-start on boot, restart on crash), see [`docs/deployment.md`](docs/deployment.md),
> which ships ready-to-use systemd units and Windows (NSSM / Task Scheduler)
> wrappers under `deploy/`.

---

## Controller workflows end to end

A typical session (assume the env vars above are set and agents are registered).

### 1. Import a topology and services

```bash
netforge controller topology import examples/topology.json
netforge controller topology list

netforge controller service register examples/services.json --topology lab
netforge controller service list
```

### 2. Diagnose a service from multiple vantages (M4)

```bash
# Probe db-postgres from several agent vantages and localize the fault
netforge controller diagnose db-postgres --topology lab

# Pin the source vantage, choose the probe, machine-readable output
netforge controller diagnose db-postgres --probe-type tcp --count 5 \
    --source-node app-1 --topology lab --json

netforge controller diagnoses     # history of stored diagnoses
```

The report localizes the fault (e.g. `HEALTHY`, `SOURCE_SIDE`, `TARGET_SIDE`,
`PATH_SHARED`, `VANTAGE_ISOLATED`, `INCONCLUSIVE`) with a confidence and an
evidence trail per vantage.

### 3. Schedule + monitor + alert + incidents (M5)

```bash
# Register an alert rule (fires on any unhealthy localization ≥ 60% confidence)
netforge controller alert rule-add --name db-unhealthy \
    --service db-postgres --min-confidence 0.6 --severity high

netforge controller alert rule-list

# Create a periodic diagnosis every 60s
netforge controller schedule add db-postgres --interval 60 --count 3 --topology lab
netforge controller schedule list

# Run all due schedules now (or start the controller with --monitor)
netforge controller monitor run

# Inspect what fired
netforge controller alert list --state firing
netforge controller incident list --state open
netforge controller incident show <INCIDENT_ID>
netforge controller incident ack <INCIDENT_ID>
netforge controller incident resolve <INCIDENT_ID>

# Clean up
netforge controller schedule remove <SCHEDULE_ID>
```

Alerts for the same service are grouped into an **incident** with an
`open → acknowledged → resolved` lifecycle.

### 4. Correlate hosts to switch ports (M6)

Feed device telemetry (LLDP neighbors + bridge MAC table) to fuse "which switch
port is each host on":

```bash
netforge controller correlate lab examples/device_correlation.json
netforge controller binding list lab
netforge controller augment lab            # topology with host→switch LAN edges
netforge controller augment lab --json     # machine-readable
```

Bindings carry a method (`lldp`, `mac_table`, or `lldp_and_mac`) and a
confidence; when LLDP and the MAC table agree the binding is *corroborated*
(highest confidence, `0.95`).

---

## Data file schemas

Working samples live in `examples/`.

### Topology (`examples/topology.json`)

```json
{
  "schema_version": "1.0",
  "name": "lab",
  "nodes": [
    {"node_id": "edge-gw", "label": "Edge Gateway", "role": "gateway", "address": "10.0.0.1"},
    {"node_id": "core-sw", "label": "Core Switch", "role": "switch"},
    {"node_id": "app-1", "label": "App Host 1", "role": "agent",
     "agent_id": "agent-app-1", "address": "10.0.0.11",
     "tags": {"site": "lab", "rack": "r1", "mac": "00:11:22:33:44:55"}},
    {"node_id": "db-1", "label": "Database Host", "role": "service",
     "agent_id": "agent-db-1", "address": "10.0.0.21",
     "tags": {"site": "lab", "rack": "r2", "mac": "00:11:22:33:44:66"}},
    {"node_id": "wan-1.1.1.1", "label": "Public Vantage", "role": "target", "address": "1.1.1.1"}
  ],
  "edges": [
    {"src": "app-1", "dst": "core-sw", "kind": "lan", "capacity_mbps": 1000},
    {"src": "db-1", "dst": "core-sw", "kind": "lan", "capacity_mbps": 1000},
    {"src": "core-sw", "dst": "edge-gw", "kind": "lan", "capacity_mbps": 10000},
    {"src": "edge-gw", "dst": "wan-1.1.1.1", "kind": "wan"}
  ]
}
```

- **node.role**: `agent`, `gateway`, `switch`, `host`, `target`, `service`.
- **edge.kind**: `lan`, `wan`, `link`. Self-loops are rejected; every edge
  endpoint must reference a declared node; node ids must be unique.
- Bind an agent to a node with `agent_id` (matching a registered agent) so the
  controller can select it as a diagnosis vantage.
- Put a host's `mac` in `tags` so correlation can match it against the switch
  MAC table / LLDP chassis id.

### Service inventory (`examples/services.json`)

```json
{
  "schema_version": "1.0",
  "services": [
    {"service_id": "db-postgres", "name": "Primary PostgreSQL",
     "node_id": "db-1", "host": "10.0.0.21", "port": 5432, "protocol": "tcp",
     "tags": {"tier": "data"}}
  ]
}
```

- **protocol**: `tcp` or `udp`. `port` is 1–65535. `service_id`s must be unique.
- `node_id` pins the service to a topology node (used for vantage selection).

### Device correlation telemetry (`examples/device_correlation.json`)

```json
{
  "topology": "lab",
  "switches": [
    {
      "node_id": "core-sw",
      "lldp": [
        {"local_port": "Gi1/0/1", "neighbor_sys_name": "app-1",
         "neighbor_mgmt_addr": "10.0.0.11",
         "neighbor_chassis_id": "00:11:22:33:44:55",
         "neighbor_port_id": "eth0", "ttl": 120}
      ],
      "mac_table": [
        {"mac": "00:11:22:33:44:55", "bridge_port": 1, "if_index": 1}
      ]
    }
  ]
}
```

`netforge controller correlate` accepts either the object above (it reads
`switches`) or a bare JSON array of switch objects.

### Mesh topology (`examples/mesh_topology.json`)

```json
{
  "targets": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
  "edges": [{"src": "local", "dst": "1.1.1.1"}, {"src": "local", "dst": "8.8.8.8"}]
}
```

### Flow export (`examples/flows_sample.json`)

A JSON array (or JSONL, one object per line) of records with `src`, `dst`,
`bytes`, `packets`, `proto`.

---

## Passive telemetry ingest

The `ingest` package reads device telemetry. It is **offline-first**: captured
walks/records are replayed from JSON so results are deterministic and testable.

### SNMP interface counters + MAC table

- **Replay (default, deterministic):** capture an SNMP walk as
  `{base_oid: {index_suffix: value}}` (see `examples/snmp_walk.json`) and feed
  it to `ReplaySnmpTransport`.
- **Live SNMPv3 (bundled engine):** `LiveSnmpV3Transport` polls a real device
  over UDP using the self-contained USM engine in `ingest/snmp_engine.py`
  (with `ingest/asn1.py` BER codec and `ingest/aes.py`). It supports:
  - security levels `noAuthNoPriv`, `authNoPriv`, `authPriv`;
  - auth protocols SHA / SHA2 / MD5 (HMAC);
  - privacy **AES-128-CFB** (RFC 3826). **DES is intentionally not
    implemented** — requesting it raises `SnmpError`;
  - engine-id discovery, `get`, `getnext`, and `walk`.

Credentials are described by `SnmpV3Credentials` (host, username,
security_level, auth_protocol, auth_key, priv_protocol, priv_key, context_name,
port). Example (Python):

```python
from ingest.snmp_counters import (
    SnmpV3Credentials,
    SecurityLevel,
    LiveSnmpV3Transport,
    OID_IF_HC_IN_OCTETS,
)

creds = SnmpV3Credentials(
    host="10.0.0.1", username="netforge",
    security_level=SecurityLevel.AUTH_PRIV,
    auth_protocol="sha", auth_key="authpass123",
    priv_protocol="aes", priv_key="privpass123",
)
transport = LiveSnmpV3Transport(creds, timeout=2.0)
print(transport.walk(OID_IF_HC_IN_OCTETS))
```

> **Interop caveat:** the engine is verified against an in-process fake device
> and FIPS-197 AES vectors; it has not been exercised against physical vendor
> hardware in this build. Do a live smoke test against a real switch/router
> before relying on it in production.

### LLDP neighbors

`ingest/lldp.py` loads captured LLDP neighbors from a JSON array or JSONL
(`examples/lldp_neighbors.json`), or assembles them from an LLDP-MIB
`lldpRemTable` walk. Live LLDP collection is intentionally stubbed — capture the
walk to JSON and replay it.

---

## Running tests

Tests live in `tests/` and are configured in `pyproject.toml`
(`testpaths = ["tests"]`, `pythonpath = ["."]`). Install the dev extra first:

```bash
pip install -e ".[dev]"
```

Run the full suite from the repository root:

```bash
pytest                      # if pytest is on PATH
# or, using the project virtualenv explicitly (recommended on Windows):
venv\Scripts\python.exe -m pytest
```

Useful variations:

```bash
venv\Scripts\python.exe -m pytest -q                     # quiet summary
venv\Scripts\python.exe -m pytest tests/test_snmp_engine.py        # one file
venv\Scripts\python.exe -m pytest -k controller                    # filter by name
venv\Scripts\python.exe -m pytest -x                       # stop on first failure
venv\Scripts\python.exe -m pytest --maxfail=3 -v           # verbose, cap failures
```

Notes:

- Tests spin up **in-process** loopback HTTP servers for the agent/controller
  and use an in-process fake UDP device for SNMP, so **no real network access or
  live devices are required**.
- Tests inject temporary SQLite stores, so they do not create stray `.db` files
  in the repo. If you ever see one, it came from running the controller/CLI
  manually — safe to delete.
- Compile/import sanity check without running tests:
  ```bash
  venv\Scripts\python.exe -m compileall ingest cli.py controller agent
  ```

---

## Configuration reference

All configuration is via environment variables.

### Agent

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NETFORGE_AGENT_ID` | yes | — | Unique agent identifier |
| `NETFORGE_AGENT_TOKEN` | yes | — | Bearer token (must match controller's) |
| `NETFORGE_ALLOWED_TARGETS` | no | unrestricted | Comma-separated allow-list for targeted probes |
| `NETFORGE_TAG_<name>` | no | — | Topology tag; `<name>` is lowercased |

### Controller

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NETFORGE_CONTROLLER_TOKEN` | yes | — | Bearer token clients use to reach the controller |
| `NETFORGE_AGENT_TOKEN` | yes | — | Token the controller presents to agents |
| `NETFORGE_CONTROLLER_DB` | no | `.netforge_controller.db` | SQLite state file path |

### CLI controller client

| Variable | Default | Description |
|----------|---------|-------------|
| `NETFORGE_CONTROLLER_URL` | `http://127.0.0.1:8080` | Controller base URL (or `--url/-u`) |
| `NETFORGE_CONTROLLER_TOKEN` | empty | Controller bearer token (or `--token/-t`) |

---

## Good-to-have features

- **Machine-readable output.** `--json` is available on `flow analyze`,
  `diagnose host|path|link|all`, `controller diagnose`, `controller monitor run`,
  `controller correlate`, and `controller augment` — pipe to files or `jq`.
- **CI gating with `--strict`.** Turn "not healthy" into a failing exit code on
  `host all`, all `diagnose` commands, and `controller diagnose`.
- **Baseline & path-change detection.** `path diff` / `path all` and
  `diagnose link` compare current metrics against a rolling SQLite baseline.
- **Automatic monitoring.** Start the controller with `--monitor` to fire due
  schedules in the background instead of calling `monitor run` manually.
- **Evidence-graded correlation.** Host↔port bindings report a method and
  confidence, and are upgraded to *corroborated* when two independent sources
  agree.
- **Token via env or flag.** Avoid putting secrets in shell history by exporting
  `NETFORGE_CONTROLLER_TOKEN` rather than passing `--token`.
- **No native dependencies.** Pure-Python HTTP + SNMPv3 keeps installs simple
  across platforms.

---

## Exit codes and troubleshooting

| Exit code | Meaning |
|-----------|---------|
| `0` | Success (and, with `--strict`, healthy) |
| `1` | Failure: diagnosis `FAILED`, or `--strict` with a non-healthy result, or a controller API/transport error |
| `2` | CLI usage error (bad arguments — Typer/Click) |

Common issues:

- **`controller API error (401)`** — the `--token` / `NETFORGE_CONTROLLER_TOKEN`
  does not match the controller's `NETFORGE_CONTROLLER_TOKEN`.
- **`controller API error (0)` / connection refused** — the controller is not
  running, is bound to `127.0.0.1` while you connect remotely, or the URL/port
  is wrong. Check `--url` and that the controller was started with
  `--host 0.0.0.0`.
- **`409 conflict` on diagnose** — no vantage agents available (register agents
  and bind them to topology nodes via `agent_id`), or no bindings for
  augment/correlate.
- **`404` on diagnose/service/topology** — the referenced `service_id`,
  topology name, incident, or schedule does not exist. Import/register it first.
- **Agents never get probed** — the controller's `NETFORGE_AGENT_TOKEN` must
  equal each agent's token, and the agent URLs you registered must be reachable
  *from the controller*.
- **Probe blocked / `400` from an agent** — the target is not in the agent's
  `NETFORGE_ALLOWED_TARGETS` allow-list, or the probe request is invalid
  (e.g. TCP probe without a port).
- **`SnmpError` on live SNMPv3** — wrong credentials/security level, unreachable
  device, or a request for DES privacy (unsupported). Verify with a replay
  capture first.

---

_For module layout and the milestone roadmap, see `IMPLEMENTATION_PLAN.md`._
