---
kind: error_handling
name: Domain-Specific Exceptions, HTTP Middleware Mapping, and Rule-Engine Isolation
category: error_handling
scope:
    - '**'
source_files:
    - agent/service.py
    - agent/http_server.py
    - controller/service.py
    - controller/http_server.py
    - analysis/engine.py
    - core/result.py
    - cli.py
---

## Overview

NetForge uses a layered error-handling strategy: domain-specific exception classes in service modules, thin HTTP middleware that maps those exceptions to standardized JSON error responses with appropriate `http.HTTPStatus` codes, and an isolation pattern in the rule engine that swallows per-rule failures so one bad rule cannot crash the whole analysis. The CLI layer exits with non-zero status via `typer.Exit` when diagnostics report failure.

## Custom Exception Types (domain layer)

Each service module defines small, purpose-built exception classes rather than reusing generic built-ins everywhere:

- `agent/service.py`: `AuthorizationError(PermissionError)` for invalid bearer tokens; `TargetNotAllowedError(ValueError)` for probes targeting hosts not whitelisted by `allowed_targets`.
- `controller/service.py`: `UnknownAgentError(ValueError)` when dispatching a job to an unknown or disabled agent.

These are raised from pure business logic (`authorize`, `_authorize_target`, `dispatch_job`) and never caught inside the service — they bubble up to the HTTP handler layer for presentation mapping.

## HTTP Transport Error Mapping (middleware layer)

Both `agent/http_server.py` and `controller/http_server.py` implement a uniform pattern using Python's stdlib `http.server.BaseHTTPRequestHandler`:

1. **Request parsing errors** — malformed JSON bodies raise `json.JSONDecodeError`, caught immediately and returned as `{"error": "request body must be JSON"}` with `HTTPStatus.BAD_REQUEST`.
2. **Authentication errors** — controller checks the `Authorization` header inline and replies `UNAUTHORIZED`; agent delegates to `service.authorize()` which raises `AuthorizationError`, caught in `_dispatch` and mapped to `UNAUTHORIZED`.
3. **Validation / policy errors** — Pydantic `ValidationError` (from `ProbeRequest.model_validate`, `AgentRegistration.model_validate`, `FanoutJobRequest.model_validate`) and `TargetNotAllowedError` are caught together and returned as `BAD_REQUEST` with the exception message.
4. **Business errors** — `UnknownAgentError` is caught alongside validation errors and also returned as `BAD_REQUEST`.
5. **Unknown endpoints** — fall through to `NOT_FOUND` with `{"error": "unknown endpoint"}`.
6. **Centralized response helper** — every path goes through `_reply(status, payload)`, ensuring consistent `Content-Type: application/json` and `Content-Length` headers.

The two handlers are nearly identical in structure, confirming this is the enforced transport convention.

## Rule Engine Isolation

`analysis/engine.py` wraps each rule evaluation in its own `try/except Exception` block. A failing rule does NOT abort the analysis; instead the exception is recorded in `self.rule_errors` (a list of `{rule_id, error}` dicts) and logged via `logger.exception`. This isolates rule implementations so new rules can be added without risking engine stability.

## Diagnostic Result Model as Structured Errors

`core/result.py` defines `DiagnosticResult`, the canonical output shape of every diagnostic probe. It carries structured fields `status` (`healthy|degraded|failed|unknown`), `severity` (`info|low|medium|high|critical`), plus `warnings: list[str]` and `errors: list[str]` arrays. Collectors populate these lists with human-readable messages rather than raising exceptions, allowing partial results to flow through the pipeline even when individual measurements fail.

## CLI Exit Codes

The CLI (`cli.py`) uses `typer.Exit(code=1)` to signal failure to the shell. Exit code 1 is raised when:
- `--strict` is set and any diagnostic reports `DEGRADED` or `FAILED`.
- Any report has `DiagnosticStatus.FAILED`.
This is applied uniformly across all diagnose commands via the shared `_exit_from_report(report, strict)` helper.

## Controller Fanout Error Aggregation

`controller/service.dispatch_job` collects both successful observations and errors from the dispatcher into a single `FanoutJob`, then derives a composite `status` (`completed` if no errors, `partial` if some agents succeeded, `failed` if none did). Errors are persisted on the job record rather than raised, enabling clients to inspect per-agent failures after completion.

## Conventions Observed

- Domain logic raises typed exceptions; presentation layers translate them to HTTP status + JSON `{"error": ...}`.
- Validation failures (Pydantic) and authorization failures are treated as client errors (`BAD_REQUEST` / `UNAUTHORIZED`).
- Unknown routes return `NOT_FOUND` with a stable error message.
- Rule evaluations are isolated with broad `except Exception` so individual rule bugs do not cascade.
- Probes return structured `DiagnosticResult` objects with `errors`/`warnings` lists instead of raising on transient measurement failures.
- CLI commands exit with code 1 on failed/strict diagnostics for scripting friendliness.