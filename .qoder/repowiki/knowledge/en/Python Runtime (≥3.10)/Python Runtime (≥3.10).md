---
kind: external_dependency
name: Python Runtime (≥3.10)
slug: python
category: external_dependency
category_hints:
    - client_constraint
scope:
    - '**'
---

NetForge requires Python ≥3.10 and is built entirely on the standard library plus a small set of third-party packages. The runtime constraint is enforced in `pyproject.toml` (`requires-python = ">=3.10"`) and all diagnostics use stdlib `subprocess`/`socket`/`http.server`/`sqlite3`, so deployment must target a compatible CPython 3.10+ environment.