# NetForge Network Diagnostics Framework

Host, link, path, traffic, flow, and mesh diagnostics with a shared rule engine.

## Install

```bash
pip install -e ".[dev]"
```

## CLI

```bash
netforge host all
netforge diagnose host
netforge path trace --target 1.1.1.1
netforge link all
netforge traffic speed
```

See [USAGE.md](USAGE.md) for the full command reference, how to connect other
machines (agents + controller), data-file schemas, telemetry ingest, and tests.
See [docs/deployment.md](docs/deployment.md) to run agents/controller as
background services (systemd + Windows).
See the multi-domain architecture plan (`IMPLEMENTATION_PLAN.md`) for module layout.
