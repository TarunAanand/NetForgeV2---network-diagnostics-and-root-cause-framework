# Diagnostic Accuracy Plan — Eliminating False-Positive Root Causes

**Status:** Proposed (not yet implemented)
**Scope:** `analysis/`, `storage/`, `diagnostics/path/`, `core/result.py`
**Goal:** Stop NetForge from jumping `Observation → Root Cause`. Introduce an
evidence-correlation pipeline so that ICMP artifacts, normal ECMP/route churn,
and statistically-insignificant latency wiggle are reported as *minor,
unconfirmed anomalies* instead of scary `FAILURE` verdicts.

---

## 1. The problem, mapped to code

A perfectly healthy network currently produces a `FAILURE` verdict because four
pieces of logic over-classify:

| # | Symptom | Current code | Why it is wrong |
|---|---------|--------------|-----------------|
| P1 | Isolated intermediate hop loss (e.g. Hop 5 = 66.7%) → `HIGH`, 82% "Elevated Packet Loss on Path Hops" | `HighHopLossRule.evaluate` — `analysis/rules/path_rules.py:40-78`; flags any addressed hop with `loss_percent >= 50` | Routers rate-limit/deprioritize ICMP TTL-expired replies while still forwarding traffic. A hop that loses replies but whose *successors and the target are clean* is ICMP suppression, not forwarding loss. |
| P2 | Path fingerprint change → `MEDIUM`, 88% "Forwarding Path Change Detected" | `PathChangeRule.evaluate` — `analysis/rules/path_rules.py:9-37`; any `ctx.path_changed()` | Internet routes change constantly (ECMP, load-balancing, BGP, MPLS). A change with no correlated degradation is not a fault. |
| P3 | Latency 21.6 ms vs baseline 12.75 ms (ratio 1.70) → "Sudden Performance Degradation" | `record_and_compare` — `storage/baselines.py:9-91` (`ratio >= 1.5` warn, `>= 2.5` fail); `SuddenDegradationRule` — `analysis/rules/baseline_rules.py:9-48` | Ratio-to-**mean** ignores the historical **distribution**. 21 ms can be inside normal p95. No statistical significance, no absolute floor, no minimum sample count. |
| P4 | Any single `HIGH`/`CRITICAL` issue flips the whole report to `FAILED` | `RuleEngine.analyze` — `analysis/engine.py:79-84` | There is no distinction between an *unconfirmed anomaly* and a *corroborated fault*, and no correlation stage that requires independent agreement. |

Supporting gaps:

- `HistoryStore.rolling_baseline` (`storage/history.py:92-114`) returns **only the
  mean** — no median/p95/MAD/stddev, so P3 cannot be fixed without extending it.
- Per-hop loss is computed against a **hardcoded** `expected = 3` probes
  (`diagnostics/path/traceroute.py:105` and `:249`), so loss % is wrong whenever
  the probe count differs.
- `detect_path_change` (`diagnostics/path/traceroute.py:279-339`) stores only the
  fingerprint; it cannot express *persistence* (single transient change vs a
  stable new path) or correlate with latency/loss history.

---

## 2. Guiding principle — four distinct tiers

Every signal must be labeled with what it actually is:

```
OBSERVATION   "Something was measured."            (raw DiagnosticResult)
   ↓  is it unusual vs a statistical/behavioral norm?
ANOMALY       "Something differs from normal."      (candidate, unconfirmed)
   ↓  is it corroborated by INDEPENDENT evidence?
FAULT         "The network is actually degraded."   (confirmed problem)
   ↓  which component/condition explains the correlated evidence?
ROOT CAUSE    "This is responsible."                (ranked, actionable)
```

Rules of the new pipeline:

1. A single, un-corroborated signal may only ever be an **ANOMALY**.
2. An anomaly is promoted to a **FAULT** only when ≥1 *independent* evidence
   source agrees (see the corroboration matrix, §5).
3. Only confirmed **FAULT/ROOT_CAUSE** items may set report status to
   `DEGRADED`/`FAILED`. Anomalies never do — they render as "minor anomalies,
   not confirmed".
4. Every demoted anomaly carries an explicit **interpretation** string
   ("Likely ICMP rate limiting; forwarding loss NOT established") and appears in
   a report-level **`not_confirmed`** list.

---

## 3. Target architecture

```
                ┌───────────────────────────────┐
                │ Raw Measurements              │  diagnostics/*  → DiagnosticResult
                └───────────────┬───────────────┘
                                ↓
                ┌───────────────────────────────┐
                │ Anomaly Detection             │  rules emit tier=ANOMALY|FAULT
                │  • statistical baselines      │  (analysis/rules/*, storage/baselines)
                │  • path loss propagation      │  (analysis/path_loss.py  ← NEW)
                └───────────────┬───────────────┘
                                ↓
                ┌───────────────────────────────┐
                │ Evidence Correlation          │  analysis/correlation.py  ← NEW
                │  group by target, require     │  EvidenceCorrelator.correlate()
                │  independent agreement        │
                └───────────────┬───────────────┘
                                ↓
                ┌───────────────────────────────┐
                │ Fault Classification          │  promote / demote tiers,
                │                               │  fill not_confirmed[]
                └───────────────┬───────────────┘
                                ↓
                ┌───────────────────────────────┐
                │ Root Cause Ranking + Verdict  │  analysis/engine.py (status logic)
                └───────────────────────────────┘
```

Existing rules stay the *detectors*; the new `EvidenceCorrelator` becomes the
single place that decides promotion. This keeps rules simple and puts the
"jump to root cause" guard in one testable component.

---

## 4. Data-model changes (`analysis/models.py`, `core/result.py`)

Add an evidence-tier enum and extend the issue/report models. All additions are
**backwards compatible** (defaults preserve current behavior for untouched rules).

```python
# analysis/models.py
class EvidenceTier(str, Enum):
    OBSERVATION = "observation"
    ANOMALY     = "anomaly"       # unusual, NOT confirmed
    FAULT       = "fault"         # confirmed problem
    ROOT_CAUSE  = "root_cause"    # confirmed + explains the evidence

class DiagnosedIssue(BaseModel):
    # ... existing fields unchanged ...
    tier: EvidenceTier = EvidenceTier.FAULT        # default keeps existing rules = faults
    confirmed: bool = True
    interpretation: str | None = None              # "what it really means"
    corroboration: list[str] = Field(default_factory=list)  # independent sources that agreed

class DiagnosisReport(BaseModel):
    # ... existing fields unchanged ...
    minor_anomalies: list[DiagnosedIssue] = Field(default_factory=list)  # tier in {OBSERVATION, ANOMALY}
    not_confirmed: list[str] = Field(default_factory=list)               # explicit "ruled out / unproven" lines
    has_confirmed_fault: bool = False
```

`DiagnosticRule.build_issue` (`analysis/rule.py:28-50`) gains optional
`tier`, `confirmed`, `interpretation`, `corroboration` kwargs passed straight
through to `DiagnosedIssue`.

**Decision — no new `DiagnosticStatus` value.** Adding `MINOR_ANOMALIES` to the
`core/result.py` enum would ripple through the controller, multi-vantage report,
storage, and many tests. Instead the display label is *derived*: when
`status == HEALTHY` and `report.minor_anomalies` is non-empty, the formatter
renders `HEALTHY (minor anomalies observed — none confirmed)`.

---

## 5. Central tunables (`analysis/thresholds.py` ← NEW)

One frozen dataclass holding every magic number so they are documented,
reviewable, and testable. Defaults below are the agreed starting point.

```python
@dataclass(frozen=True)
class Thresholds:
    # --- per-hop loss classification ---
    hop_loss_notice_percent: float = 30.0     # a hop is "lossy" above this
    hop_loss_high_percent: float = 50.0       # "high" loss (matches today's rule)
    successor_clean_percent: float = 10.0     # successors below this = "clean"
    loss_persist_fraction: float = 0.5        # successor median >= this * hop loss = persists
    e2e_loss_confirm_percent: float = 5.0     # end-to-end loss that confirms forwarding loss

    # --- statistical baselines ---
    min_samples_for_stats: int = 5            # below this: ratio fallback, low confidence
    robust_z_unusual: float = 3.5             # median/MAD z-score to call "unusual"
    robust_z_with_p95: float = 2.0            # z needed when also above p95
    latency_abs_floor_ms: float = 5.0         # ignore deltas smaller than this
    latency_rel_floor: float = 0.20           # and smaller than 20% over median

    # --- persistence / path change ---
    path_change_consecutive: int = 2          # consecutive changed snapshots to treat as stable
    transport_degraded_confirm: bool = True   # TCP/UDP failure counts as corroboration

DEFAULT_THRESHOLDS = Thresholds()
```

---

## 6. Phase 1 — Statistical baselines (fixes P3)

**`storage/history.py`** — add a distribution helper (pure stdlib, no numpy):

```python
def rolling_stats(self, domain, key, metric, limit=20) -> dict | None:
    """Return {count, mean, median, p95, stddev, mad, min, max, samples[]}
    for a numeric metric across recent snapshots, or None if no samples."""
```
- `median`, `p95` (nearest-rank), `stddev` (population), `mad` = median(|x−median|).

**`storage/baselines.py`** — add `record_and_compare_distribution(...)` alongside
the existing `record_and_compare` (kept for compatibility):
- Compute robust z = `0.6745 * (x − median) / mad` (guard `mad == 0` → fall back
  to stddev, then to ratio).
- `statistically_unusual = (count >= min_samples_for_stats) and (z >= robust_z_unusual or (x > p95 and z >= robust_z_with_p95))`.
- Apply absolute+relative floors (latency must exceed `median + latency_abs_floor_ms`
  **and** `median * (1 + latency_rel_floor)`).
- Emit richer metrics on the `baseline_delta` result:
  `baseline_median, baseline_p95, baseline_mad, z_score, sample_count,
  statistically_unusual, exceeds_p95, delta_abs, delta_ratio, deviated`.
- Status/severity: `statistically_unusual` → `DEGRADED/MEDIUM` (or `HIGH` if z is
  extreme AND corroborated later); low-sample or sub-floor → `HEALTHY/INFO` with
  `deviated=False` and a "insufficient history" note.
- Route `compare_probe_metrics` latency & loss through the new function; keep
  utilization on ratio for now (or migrate too — low risk).

**`analysis/rules/baseline_rules.py`** — `SuddenDegradationRule`:
- Require `r.metrics.get("statistically_unusual")` (not merely `ratio >= 1.5`).
- When sample_count < min or only ratio-based, emit `tier=ANOMALY, confirmed=False,
  severity=LOW` with interpretation "Latency elevated vs baseline but persistence
  and statistical significance not established."

**Test coverage:** `test_baselines.py` — median/p95/MAD math, robust-z, low-sample
fallback, sub-floor suppression, the exact `21.6 vs median 13 / p95 21` (not
unusual) vs `21.6 vs median 13 / p95 15` (unusual) cases from the report.

---

## 7. Phase 2 — Path loss propagation + ICMP suppression (fixes P1)

**`analysis/path_loss.py` ← NEW** (pure, dependency-free, unit-testable):

```python
class LossPattern(str, Enum):
    NONE                 = "none"
    ISOLATED_INTERMEDIATE= "isolated_intermediate"   # ICMP suppression likely
    PROPAGATING          = "propagating"              # loss continues downstream
    ONSET_PERSISTENT     = "onset_persistent"         # begins at hop H, persists to target
    TARGET_UNREACHABLE   = "target_unreachable"       # trailing timeouts, target not reached

@dataclass
class HopLossClassification:
    pattern: LossPattern
    onset_hop: int | None
    lossy_hops: list[int]
    max_hop_loss: float
    e2e_loss_percent: float | None      # from packet_loss probes, if present
    target_reached: bool
    confirmed_forwarding_loss: bool     # True only for PROPAGATING/ONSET/TARGET_UNREACHABLE + e2e
    confidence: float
    reasoning: list[str]

def classify_hop_loss(hops, *, e2e_loss_percent=None, target_reached=None,
                      thresholds=DEFAULT_THRESHOLDS) -> HopLossClassification: ...
```

Algorithm:
1. Collect `lossy = [h for h in hops if h.loss_percent >= hop_loss_notice and h.address]`.
   If empty → `NONE`.
2. For the earliest lossy hop `H`, look at successors (`hops` after `H`) and the target:
   - `successor_median_loss = median(loss of hops after H)`.
   - **ISOLATED_INTERMEDIATE** if `successor_median_loss < successor_clean_percent`
     and (target reached / `e2e_loss < e2e_loss_confirm`). → `confirmed_forwarding_loss=False`.
   - **ONSET_PERSISTENT** if `successor_median_loss >= H.loss * loss_persist_fraction`
     **and** `e2e_loss >= e2e_loss_confirm` → onset localized at `H`.
   - **PROPAGATING** if successors stay lossy but onset is ambiguous (multiple lossy
     hops, no clean prefix) and `e2e_loss >= e2e_loss_confirm`.
   - **TARGET_UNREACHABLE** if trailing hops all `timed_out` and target not reached.
3. `confidence` scales with corroboration: isolated → ~0.25; propagating/onset with
   e2e confirmation → 0.7–0.9; without e2e data, cap at ~0.4 and mark unconfirmed.

**`analysis/rules/path_rules.py`** — rework `HighHopLossRule` (keep `rule_id =
"RULE_HIGH_HOP_LOSS"` so the `cross_rules` suppression link stays valid):
- Call `classify_hop_loss(hops, e2e_loss_percent=ctx.get_max_packet_loss(),
  target_reached=...)`. Add `ctx.target_reached()` helper (last hop addressed &
  not timed out, or matches target).
- `ISOLATED_INTERMEDIATE` → `tier=ANOMALY, confirmed=False, severity=LOW,
  confidence≈0.25`, title "Intermediate ICMP Response Loss (Forwarding Loss Not
  Confirmed)", interpretation "Later hops and the target remain reachable —
  likely ICMP rate-limiting/deprioritization, not actual packet loss."
- `PROPAGATING`/`ONSET_PERSISTENT` **with** e2e or transport corroboration →
  `tier=FAULT`, `severity=HIGH/MEDIUM`, confidence 0.7–0.9, onset hop named.
- Without corroboration → stay `ANOMALY`, capped confidence.
- Add corroboration from transport: `ctx.get_transport_result(port,"tcp")` failing
  to the same target counts as independent agreement.

**`diagnostics/path/traceroute.py`** — correctness fixes:
- Thread the real probe count through `parse_traceroute_output` /
  `measure_hop_metrics` instead of hardcoded `expected = 3` / `probes * 3`
  (Windows `tracert` default is 3 but make it a parameter; record `probes_sent`
  and `samples` per hop).
- `traceroute_to_result` status: stop marking `DEGRADED` purely on
  `high_loss_hops > 0`; use `classify_hop_loss` so isolated ICMP loss yields
  `HEALTHY`/`LOW`. Store `e2e_reachable` and `loss_pattern` in metrics.

**Test coverage:** `tests/test_path_loss.py` — the three worked cases from the
report (isolated H5; propagating H5→target; onset-persistent H5→target), plus
target-unreachable and "no e2e data → capped confidence". Rule test: isolated →
`tier=ANOMALY`, not `FAULT`.

---

## 8. Phase 3 — Path change vs path failure (fixes P2)

**`diagnostics/path/traceroute.py`** — `detect_path_change`:
- Persist per-snapshot `{fingerprint, hops, avg_latency_ms, max_hop_loss}` so we
  can (a) count **consecutive** changes and (b) correlate latency/loss with the change.
- Add `consecutive_changes` (how many of the last N snapshots differ from their
  predecessor) to the `path_change` metrics.

**`analysis/rules/path_rules.py`** — rework `PathChangeRule` (keep
`rule_id = "RULE_PATH_CHANGE"`):
- A bare change (no correlated degradation) → `tier=ANOMALY, confirmed=False,
  severity=INFO, confidence≈0.2`, title "Forwarding Path Variation", interpretation
  "May be ECMP / normal ISP routing variation; no fault established without
  correlated degradation."
- Elevate to `MEDIUM/HIGH` `tier=FAULT` **only** when correlated with ≥1 of:
  statistically-significant latency increase (Phase 1), confirmed forwarding loss
  (Phase 2), transport degradation, or persistence (`consecutive_changes >=
  path_change_consecutive`). Confidence scales with the number of agreeing signals.

**Test coverage:** change-alone → INFO/anomaly; change + significant latency +
confirmed loss → FAULT/MEDIUM+; transient single change with clean successors →
stays anomaly.

---

## 9. Phase 4 — Evidence correlation stage (`analysis/correlation.py` ← NEW, fixes P4)

A single component that runs after rules and before verdict synthesis.

```python
@dataclass
class CorrelationResult:
    faults: list[DiagnosedIssue]          # confirmed (tier FAULT/ROOT_CAUSE)
    anomalies: list[DiagnosedIssue]       # unconfirmed (tier OBSERVATION/ANOMALY)
    not_confirmed: list[str]              # explicit statements for the report
    has_confirmed_fault: bool

class EvidenceCorrelator:
    def __init__(self, thresholds=DEFAULT_THRESHOLDS): ...
    def correlate(self, issues: list[DiagnosedIssue], ctx: AnalysisContext) -> CorrelationResult: ...
```

Corroboration matrix (independent evidence that promotes an anomaly to a fault):

| Candidate anomaly | Independent evidence that confirms it |
|-------------------|----------------------------------------|
| Path hop loss | End-to-end `packet_loss` ≥ floor · TCP/UDP transport failure · same loss seen from ≥2 vantages (multi-vantage) · persists across ≥2 monitor runs |
| Path change | Statistically-significant latency rise · confirmed hop loss · transport degradation · `consecutive_changes ≥ N` |
| Latency vs baseline | Confirmed loss · transport degradation · persists across runs · jitter spike |
| Jitter/bufferbloat | Host CPU saturation OR link congestion OR confirmed loss |

Behavior:
- Group issues by target; for each `ANOMALY`, look up corroborating signals in
  `ctx`. If found → promote (`tier=FAULT, confirmed=True`, bump confidence,
  append to `corroboration`). If not → keep as anomaly and add a `not_confirmed`
  line ("End-to-end packet loss: NOT confirmed").
- Deduplicate/merge overlapping issues on the same target (e.g. hop-loss fault
  absorbs the path-change anomaly when both are corroborated).
- Preserve the existing `suppressed_rules` mechanism.

**`analysis/engine.py`** — `RuleEngine.analyze` rewires to:
1. run rules → `raw_issues` (as today);
2. `correlation = EvidenceCorrelator().correlate(raw_issues, ctx)`;
3. sort `correlation.faults` by severity/confidence; keep anomalies separate;
4. **status logic (replaces `engine.py:79-84`):**
   ```
   if any fault with severity in {CRITICAL, HIGH}:      status = FAILED
   elif correlation.faults or corroborated degraded:    status = DEGRADED
   else:                                                 status = HEALTHY   # anomalies only
   ```
5. populate `report.minor_anomalies`, `report.not_confirmed`,
   `report.has_confirmed_fault`;
6. `_synthesize_verdict` produces the "healthy / minor anomalies" narrative with
   explicit **Observed** and **Not confirmed** bullets when nothing is confirmed.

**Test coverage:** correlator promotion/demotion per matrix row; engine status
transitions (anomaly-only → HEALTHY; anomaly+e2e → FAILED); dedup/merge;
`not_confirmed` content.

---

## 10. Phase 5 — Formatter (`analysis/formatter.py`)

- Render confirmed faults as today (red/yellow panels), but add a tier badge and
  an **Interpretation** line per issue.
- New section for `report.minor_anomalies`: dim/blue panels labeled
  `[LOW] Intermediate ICMP Response Loss`, `[INFO] Path Variation`, etc., each
  with its interpretation and a "not confirmed" note.
- Header shows `HEALTHY (minor anomalies observed — none confirmed)` when
  `status == HEALTHY and minor_anomalies`.
- Add an **Observed / Not confirmed** two-column summary driven by
  `key_observations` and `not_confirmed`.
- `--json` output already serializes the model, so the new fields flow through
  automatically (verify no controller/CLI schema assertions break).

---

## 11. Phase 6 — Multi-vantage & persistence (corroboration at scale)

- **Multi-vantage (`analysis/multivantage.py`):** when several agents probe the
  same target, treat *agreement across vantages* as strong corroboration and
  *single-vantage-only* hop loss as weak (likely that vantage's path or ICMP
  suppression). Feed a `vantage_agreement` signal into the correlator.
- **Persistence:** the M5 monitor loop already re-runs scheduled diagnoses. Add a
  lightweight "consecutive unhealthy runs" counter (reuse `HistoryStore`) so the
  correlator can require persistence before promoting latency/loss anomalies.
  Single-shot CLI runs simply cannot establish persistence — the report should
  say so ("persistence not established — run `netforge controller monitor` or
  re-run to confirm").

---

## 12. Worked example (the reported scenario)

Input: interface UP + IP; gateway `192.168.0.1` active; DNS ok; L3/L4 ok;
Hop 5 `103.16.203.73` = 66.7% loss with Hops 6–7 and target = 0%; path
fingerprint changed; latency 21.62 ms vs baseline mean 12.75 ms.

**Today:** `FAILED` — "Elevated Packet Loss on Path Hops (HIGH, 82%)",
"Forwarding Path Change (MEDIUM, 88%)", "Sudden Performance Degradation (HIGH)".

**After this plan:**
```
Status: HEALTHY (minor anomalies observed — none confirmed)

Observed:
  • Intermediate hop ICMP response loss (Hop 5: 66.7%)
  • Forwarding path variation vs baseline
  • Latency above recent median (21.6 ms vs 13 ms median)

Not confirmed:
  • End-to-end packet loss (successors + target clean → ICMP rate-limiting likely)
  • Local gateway / interface / DNS failure (all healthy)
  • Confirmed forwarding loss
  • Statistically-significant latency regression (within/near p95; low samples)

[LOW]  Intermediate ICMP Response Loss — Hop 5: 66.7%; later hops and target
       reachable. Interpretation: likely ICMP rate limiting; forwarding loss
       NOT established.
[INFO] Path Variation — differs from baseline. Interpretation: could be ECMP /
       normal ISP routing; no fault without correlated degradation.
[LOW]  Latency Variation — 21.62 ms vs 12.75 ms baseline. Interpretation:
       persistence and statistical significance not yet established.
```

---

## 13. Delivery order, effort, and risk

Each phase is independently shippable; keep the full suite green at every step.

| Phase | Deliverable | Files | Est. | Risk |
|-------|-------------|-------|------|------|
| 0 | `analysis/thresholds.py` + tier model | `analysis/models.py`, `analysis/rule.py`, `analysis/thresholds.py` | S | Low (additive) |
| 1 | Statistical baselines | `storage/history.py`, `storage/baselines.py`, `analysis/rules/baseline_rules.py` | M | Med (touches baselines used by `diagnose link`) |
| 2 | Path loss propagation + ICMP detection | `analysis/path_loss.py`, `analysis/rules/path_rules.py`, `diagnostics/path/traceroute.py` | M | Med (probe-count fix changes loss %) |
| 3 | Path change vs failure | `analysis/rules/path_rules.py`, `diagnostics/path/traceroute.py` | S | Low |
| 4 | Evidence correlator + engine status | `analysis/correlation.py`, `analysis/engine.py`, `analysis/context.py` | M | High (verdict semantics) |
| 5 | Formatter | `analysis/formatter.py` | S | Low |
| 6 | Multi-vantage + persistence | `analysis/multivantage.py`, `storage/history.py` | M | Med |

**Recommended order:** 0 → 2 → 3 → 1 → 4 → 5 → 6. (Phases 2–3 kill the largest
class of false positives fastest and are self-contained; the correlator in 4
then formalizes the promotion logic; 1 and 6 deepen it.)

---

## 14. Backwards compatibility & migration

- All new model fields have defaults preserving today's behavior for rules that
  are not reworked (gateway/DNS/interface faults stay `tier=FAULT`).
- Rule IDs are unchanged, so `suppressed_rules` links (e.g. `cross_rules.py`
  suppressing `RULE_HIGH_HOP_LOSS`) keep working.
- `record_and_compare` is retained; new code calls `record_and_compare_distribution`.
- **Existing tests that assert old severities must be updated** (expected, and
  desired): e.g. any test asserting `PathChangeRule` → `MEDIUM`, or
  `HighHopLossRule` → `HIGH` for an isolated hop. Audit `tests/test_rules.py`
  and controller/monitoring tests that assert `FAILED` from path-only signals.
- The controller alert rules (M5) fire on `FaultLocalization`; confirm the new
  HEALTHY-with-anomalies path does not accidentally trip `UNHEALTHY_LOCALIZATIONS`.

---

## 15. Out of scope (future)

- TCP-based traceroute (`tcptraceroute`/`traceroute -T`) to directly disambiguate
  ICMP suppression — needs elevated privileges; keep as a recommendation string.
- Per-hop EWMA baselining (loss/latency history *per hop* rather than per target).
- Learning thresholds per-network (adaptive baselines) instead of global tunables.
- A confidence-calibration harness (compare verdicts against labeled ground truth).

---

_Back to the [Usage Guide](../USAGE.md) · [Roadmap](../IMPLEMENTATION_PLAN.md)._
