---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### DiagnosticResult
- Definition：The canonical evidence model used throughout NetForge — every probe (host, link, path, traffic, flow, mesh) returns a `DiagnosticResult` containing module/category/status/severity/summary/metrics/evidence/warnings/errors. It is the single type that flows from collectors into the rule engine.
- Aliases：diagnostic result

### EvidenceQuality
- Definition：A confidence scale (unknown → verified) mapped to numeric confidence values (0.0 → 0.95) that describes how trustworthy a measurement method was, independent of whether the probe passed or failed. Used by `AgentObservation` to carry provenance about the observation's reliability.
- Aliases：evidence quality、confidence level

### AgentObservation
- Definition：An `AgentObservation` wraps a `RemoteObservationContext` (agent_id, hostname, timestamps, topology tags, raw evidence) around a `DiagnosticResult`, providing full provenance when results come from a remote agent rather than the local process.
- Aliases：agent observation

### AnalysisContext
- Definition：An indexed query layer over a batch of `DiagnosticResult`s that rules use to correlate evidence across domains (e.g. `is_gateway_reachable()`, `is_dns_resolution_working()`, `get_max_packet_loss()`). It is the bridge between raw measurements and root-cause reasoning.
- Aliases：analysis context

### RuleEngine
- Definition：The orchestrator that runs all diagnostic rules in isolation, resolves conflicts/subsumption (a root-cause rule suppresses downstream symptom rules via `suppressed_rules`), sorts findings by severity then confidence, computes an overall status, and synthesizes a human-readable verdict identifying primary root cause and secondary factors.
- Aliases：rule engine

### DiagnosedIssue
- Definition：The output of a single rule match — carries severity, confidence, root-cause description, correlated evidence, prioritized recommendations, and a list of rules it suppresses. This is the unit of actionable diagnosis produced by the rule engine.
- Aliases：diagnosed issue

### Path fingerprint
- Definition：A SHA-256 hash of the hop sequence from a traceroute, used to detect path changes over time by comparing against stored baselines in the SQLite history store.
- Aliases：path hash、traceroute fingerprint

### Baseline delta
- Definition：A comparison of a current metric against a rolling mean stored in `storage/baselines.py`; emits a warning at 1.5× deviation and a failure at 2.5×, enabling sudden-degradation detection without hard thresholds.
- Aliases：baseline deviation、rolling baseline

### Fan-out job
- Definition：A controller-side diagnosis job dispatched concurrently to many registered agents via `ThreadPoolExecutor` (bounded workers); the job status becomes `completed`, `partial`, or `failed` depending on per-agent errors, enabling multi-vantage diagnosis from multiple network locations simultaneously.
- Aliases：fanout job、multi-vantage job

### TargetNotAllowedError
- Definition：A security-boundary exception raised when a probe target is not present in the agent's allow-list; enforces that agents can only be instructed to probe pre-approved hosts, preventing arbitrary outbound probing.
- Aliases：target not allowed

### Milestone plan (M0–M6)
- Definition：The project's staged roadmap documented in `IMPLEMENTATION_PLAN.md`: M0 (versioned observation contract + evidence quality), M1 (authenticated remote agent), M2 (controller + fan-out jobs + SQLite history), M3 (validated topology & service inventory), M4 (two-sided/third-vantage diagnosis with evidence trails), M5 (scheduling, baselines, alerts, incidents), M6 (SNMPv3 counters, LLDP, host-to-switch-port correlation).
- Aliases：milestones、roadmap
