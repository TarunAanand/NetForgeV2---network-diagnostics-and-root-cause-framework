---
kind: external_dependency
name: Typer CLI Framework
slug: typer
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
---

The `netforge` entry point (`python -m cli:app`) is implemented with Typer, exposing commands like `host all`, `diagnose host`, `path trace --target`, `link all`, `traffic speed`. Typer handles argument parsing and rich terminal output via its integration with the `rich` package.