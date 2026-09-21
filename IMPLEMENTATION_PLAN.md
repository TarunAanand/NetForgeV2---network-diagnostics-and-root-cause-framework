# NetForge implementation plan

NetForge evolved from a local CLI into a multi-agent endpoint diagnosis platform. Device-level telemetry (SNMPv3 counters, LLDP, bridge MAC tables) is delivered both offline via replay captures and live via a self-contained, pure-stdlib SNMPv3 (USM) engine — no third-party SNMP/ASN.1 dependency.

## Delivery order

1. M0 (delivered): versioned observation contract and evidence-quality model.
2. M1 (delivered): authenticated remote agent and allow-listed internal probes.
3. M2 (delivered): controller, agent registry, fan-out jobs, and SQLite history.
4. M3 (delivered): validated topology and service inventory.
5. M4 (delivered): two-sided and third-vantage diagnosis with evidence trails.
6. M5 (delivered): scheduling, baselines, alerts, and incidents.
7. M6 (delivered): SNMPv3 counters, LLDP, and host-to-switch-port correlation.

## Post-roadmap follow-ons (delivered)

- **Controller CLI** (delivered): a `netforge controller` command group exposes the
  M4–M6 control plane over HTTP without raw curl — `diagnose`, `diagnoses`,
  `monitor run`, `schedule list|add|remove`, `alert list|rule-list|rule-add`,
  `incident list|show|ack|resolve`, `correlate`, `binding list`, `augment`, plus
  bootstrap commands (`agent`, `topology`, `service`). Backed by
  `controller/client.py` (stdlib `urllib` client with bearer auth) and
  `controller/render.py` (rich renderers for bindings/alerts/incidents/schedules/
  topology). Group-level `--url`/`--token` fall back to `NETFORGE_CONTROLLER_URL`
  / `NETFORGE_CONTROLLER_TOKEN`.
- **Live SNMPv3 engine** (delivered): `ingest/snmp_engine.py` (+ `ingest/asn1.py`
  BER codec and `ingest/aes.py` AES-128/CFB) is a dependency-free USM engine
  behind the existing `LiveSnmpV3Transport` seam. Supports `noAuthNoPriv`,
  `authNoPriv` (HMAC-SHA1/SHA2, MD5) and `authPriv` (AES-128-CFB, RFC 3826) with
  engine discovery, key localization (RFC 3414), and `get`/`getnext`/`walk`.
  DES privacy is intentionally not implemented. Verified against FIPS-197 AES
  vectors and an in-process fake UDP device across all three security levels;
  interoperability with a physical device is not covered by the offline suite.

## Proposed next (not yet implemented)

- **Diagnostic accuracy / false-positive reduction**: introduce an
  Observation → Anomaly → Fault → Root-Cause evidence pipeline so isolated
  intermediate-hop ICMP loss, normal ECMP/route churn, and statistically
  insignificant latency wiggle are reported as minor unconfirmed anomalies
  instead of `FAILURE` verdicts. Adds statistical (median/p95/MAD) baselines,
  path loss-propagation + ICMP-suppression detection, a path-change-vs-failure
  distinction, and a central evidence correlator. Full design in
  [`docs/diagnostic-accuracy-plan.md`](docs/diagnostic-accuracy-plan.md).

