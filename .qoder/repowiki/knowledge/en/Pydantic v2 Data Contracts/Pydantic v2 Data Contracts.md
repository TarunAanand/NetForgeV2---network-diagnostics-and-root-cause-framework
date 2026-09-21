---
kind: external_dependency
name: Pydantic v2 Data Contracts
slug: pydantic
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
---

All cross-module data contracts (`DiagnosticResult`, `AgentObservation`, `RemoteObservationContext`, rule models, HTTP request/response models) are Pydantic v2 models. This is the stable contract boundary between agent/controller, diagnostics, and analysis layers; changes to these models require version bumps because they are serialized over HTTP and stored in SQLite history.