---
kind: logging_system
name: Minimal Python stdlib logging used only in the rule engine
category: logging_system
scope:
    - '**'
source_files:
    - analysis/engine.py
    - cli.py
    - agent/__main__.py
    - controller/__main__.py
---

## What system/approach is used

NetForge does **not** implement a centralized logging subsystem. The only structured logging in the codebase is the standard-library `logging` module, used in a single place: `analysis/engine.py`, which creates a per-module logger via `logging.getLogger(__name__)` and emits an exception trace with `logger.exception(...)` when an individual diagnostic rule fails during evaluation. There is no `logging.basicConfig()` call anywhere, so log output defaults to the Python stdlib default (stderr, WARNING level) unless a caller configures it externally.

All other user-facing output goes through Rich (`rich.console.Console.print`) for colored CLI panels/tables, or plain `print()` for JSON dumps — these are presentation, not logging.

## Key files and packages

- `analysis/engine.py` — the sole file that imports `logging` and calls `logger.exception`. It logs rule-evaluation failures with the rule id embedded in the message.
- `cli.py` — uses Rich `Console.print` for all terminal output; no logging.
- `agent/__main__.py`, `controller/__main__.py` — entry points configure services from env/config but perform no logging setup.
- `requirements.txt` / `pyproject.toml` — no logging framework dependencies beyond the stdlib.

## Architecture and conventions

- **No global logger configuration**: No module calls `logging.basicConfig`, `addHandler`, or sets root-level levels/formatters. Log routing is entirely inherited from the process environment.
- **Per-module logger instances**: The pattern `logger = logging.getLogger(__name__)` is used (in `analysis/engine.py`), following the standard convention of one logger per module.
- **Single log site**: Only one `logger.exception` call exists in the entire repo, inside the rule engine's try/except around each rule's `evaluate()`. This isolates rule failures without crashing the analysis pipeline.
- **Structured fields via message interpolation**: The logged message embeds the failing rule id as a positional argument (`logger.exception("Rule %s failed during evaluation", rule.rule_id)`). There is no structured-keyword-field logging (e.g., no `extra=` dict, no JSON formatter).
- **Log level strategy**: Not defined by the application. With no explicit handler configuration, the effective level is the stdlib default (`WARNING`).
- **Output separation**: Diagnostic results are returned as typed objects (`DiagnosticResult`, `DiagnosisReport`) and rendered either as JSON (`model_dump_json`) or via Rich console formatting — this is the intended observable output channel, distinct from any stderr logs.

## Conventions and constraints

- Observers should not expect any log output unless they explicitly configure the root logger before importing NetForge modules.
- Rule failures are captured programmatically in `self.rule_errors` (a list of `{rule_id, error}` dicts) and also surfaced in the report's `metadata.rule_errors`; the same failure is additionally emitted to stderr via `logger.exception`.
- No log rotation, file sinks, remote sinks, or correlation IDs are implemented.
- All non-error informational output is intentionally UI-oriented (Rich panels/tables) rather than log lines.