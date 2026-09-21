---
kind: configuration_system
name: Environment-Driven Configuration via Frozen Dataclasses
category: configuration_system
scope:
    - '**'
source_files:
    - agent/config.py
    - controller/config.py
    - agent/__main__.py
    - controller/__main__.py
    - storage/history.py
    - cli.py
    - pyproject.toml
---

## What system/approach is used

NetForge uses a minimal, environment-variable-driven configuration system. Each long-running process (agent, controller) defines its own frozen `dataclass` configuration object with a classmethod `from_env()` that reads values exclusively from the process environment using `os.environ.get(...)`. There are no YAML/JSON/TOML config files, no `.env` file loader, and no runtime config reload — configuration is loaded once at process startup and then treated as immutable.

The CLI (`cli.py`) does not load any external configuration; all tunables are passed directly as Typer command-line options (e.g. `--target`, `--count`, `--interval`, `--json`, `--strict`).

## Key files and packages

- `agent/config.py` — `AgentConfig` dataclass: `agent_id`, `bearer_token`, `hostname` (defaults to `socket.gethostname()`), `allowed_targets` (comma-separated env var parsed into a `frozenset`), `topology_tags` (dynamic key-value pairs from `NETFORGE_TAG_*` env vars).
- `controller/config.py` — `ControllerConfig` dataclass: `controller_token`, `agent_token`, `database_path` (default `.netforge_controller.db`, overridable via `NETFORGE_CONTROLLER_DB`).
- `storage/history.py` — default SQLite path for probe history read from `NETFORGE_HISTORY_DB` (fallback `.netforge_history.db`).
- `agent/__main__.py` and `controller/__main__.py` — entry points that parse only `--host` / `--port` via `argparse` and construct services from `Config.from_env()`.
- `cli.py` — Typer-based CLI where every diagnostic subcommand exposes its parameters as CLI flags; no global config object is consumed by the CLI.
- `pyproject.toml` — declares the `netforge` console script entry point pointing at `cli:app`; no package-level config discovery is configured.

## Architecture and conventions

1. **Per-process config objects.** The agent and controller each have their own `config.py` module defining a single frozen dataclass. This keeps configuration scoped to the process that needs it rather than sharing a central config registry.
2. **Immutable configs.** All config classes use `@dataclass(frozen=True)`, so once created they cannot be mutated at runtime.
3. **Single source of truth per process.** `from_env()` is the only constructor used in production code paths; there is no public `__init__` usage in the service wiring, which enforces environment-only configuration.
4. **Strict validation on load.** Missing required variables cause an immediate `ValueError` at startup:
   - Agent requires both `NETFORGE_AGENT_ID` and `NETFORGE_AGENT_TOKEN`.
   - Controller requires both `NETFORGE_CONTROLLER_TOKEN` and `NETFORGE_AGENT_TOKEN`.
5. **Optional / defaulted fields.** Non-critical settings have sensible defaults: `hostname` auto-resolves via `socket.gethostname()`, `allowed_targets` defaults to an empty frozenset, `topology_tags` defaults to an empty dict, `database_path` defaults to `.netforge_controller.db`, and history DB defaults to `.netforge_history.db`.
6. **Dynamic tags via prefix convention.** The agent supports arbitrary topology metadata through `NETFORGE_TAG_<key>=<value>` environment variables; keys are lowercased and stripped of the `NETFORGE_TAG_` prefix.
7. **CLI vs. service separation.** The CLI (`netforge ...`) takes all user-facing parameters as explicit Typer options and never consults environment variables for behavior. Only the agent/controller HTTP services consume environment-based configuration.
8. **No feature-flag or layered config system.** There is no concept of config profiles, override precedence (e.g. file > env), or runtime toggles beyond what can be expressed as CLI flags.

## Conventions and constraints

- **All process configuration must go through `Config.from_env()`** — the `__init__` constructors are not called directly in service wiring; this is enforced by how `__main__.py` constructs services.
- **Required tokens are validated eagerly** — a missing `NETFORGE_AGENT_ID`, `NETFORGE_AGENT_TOKEN`, `NETFORGE_CONTROLLER_TOKEN`, or `NETFORGE_AGENT_TOKEN` (for the controller) raises `ValueError` before any network I/O occurs.
- **Secrets are expected in environment variables**, not in files or package resources. The comment in `agent/config.py` explicitly marks bearer tokens as demo-only and notes mTLS is planned for M2.
- **Target allowlists are comma-separated strings** parsed into `frozenset[str]` via `NETFORGE_ALLOWED_TARGETS`; empty entries are silently dropped.
- **Topological metadata is extensible** via the `NETFORGE_TAG_*` naming convention — any additional tag can be injected without code changes.
- **Database paths are configurable but default to local SQLite files** (`.netforge_controller.db`, `.netforge_history.db`); no other storage backends are wired.
- **There is no configuration file format** (no `.yaml`, `.toml`, `.env`, or JSON config loader) anywhere in the codebase — configuration is purely environment + CLI args.