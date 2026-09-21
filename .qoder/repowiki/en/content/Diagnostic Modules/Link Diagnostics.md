# Link Diagnostics

<cite>
**Referenced Files in This Document**
- [collector.py](file://diagnostics/link/collector.py)
- [congestion.py](file://core/metrics/congestion.py)
- [bandwidth.py](file://core/metrics/bandwidth.py)
- [throughput.py](file://core/metrics/throughput.py)
- [result.py](file://core/result.py)
- [interface.py](file://diagnostics/host/interface.py)
- [link_rules.py](file://analysis/rules/link_rules.py)
- [context.py](file://analysis/context.py)
- [formatter.py](file://analysis/formatter.py)
- [README.md](file://README.md)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document explains the NetForge link diagnostics module, which provides link-level network monitoring for interfaces on the local host. It covers:
- Interface utilization monitoring (RX/TX throughput and utilization percentage)
- Error rate analysis (packet drops and interface errors per second)
- Congestion detection (composite score combining utilization, drops/errors, and latency delta)
- Data collection methods, metric calculations, and alerting mechanisms
- Configuration options for monitoring intervals and reporting formats
- Examples for interpreting link health metrics and identifying potential issues

The module integrates with the broader NetForge framework to produce standardized diagnostic results that can be consumed by rule engines and formatters for reporting.

## Project Structure
The link diagnostics functionality is implemented under the diagnostics/link package and relies on shared core metrics and result models. Related components include:
- Local interface inspection under diagnostics/host
- Rule-based analysis under analysis/rules
- Shared metric utilities under core/metrics
- Standardized result model under core/result

```mermaid
graph TB
subgraph "Link Diagnostics"
LC["diagnostics/link/collector.py"]
end
subgraph "Core Metrics"
BW["core/metrics/bandwidth.py"]
TH["core/metrics/throughput.py"]
CG["core/metrics/congestion.py"]
end
subgraph "Host Inspection"
IF["diagnostics/host/interface.py"]
end
subgraph "Analysis & Rules"
CTX["analysis/context.py"]
LR["analysis/rules/link_rules.py"]
FM["analysis/formatter.py"]
end
subgraph "Result Model"
RES["core/result.py"]
end
LC --> BW
LC --> TH
LC --> CG
LC --> RES
IF --> RES
LR --> CTX
LR --> RES
FM --> RES
```

**Diagram sources**
- [collector.py:1-210](file://diagnostics/link/collector.py#L1-L210)
- [bandwidth.py:1-17](file://core/metrics/bandwidth.py#L1-L17)
- [throughput.py:1-24](file://core/metrics/throughput.py#L1-L24)
- [congestion.py:1-51](file://core/metrics/congestion.py#L1-L51)
- [interface.py:1-147](file://diagnostics/host/interface.py#L1-L147)
- [context.py:1-199](file://analysis/context.py#L1-L199)
- [link_rules.py:1-96](file://analysis/rules/link_rules.py#L1-L96)
- [formatter.py:1-82](file://analysis/formatter.py#L1-L82)
- [result.py:1-47](file://core/result.py#L1-L47)

**Section sources**
- [README.md:1-22](file://README.md#L1-L22)

## Core Components
- Link utilization measurement: captures RX/TX bytes over a configurable interval, converts to bits/sec, estimates capacity from interface speed, and computes utilization percentage.
- Link error measurement: counts packet drops and interface errors over an interval and reports rates per second.
- Congestion detection: combines utilization, drop/error rates, and optional latency delta into a composite score and maps it to a coarse state.
- Host interface inspection: enumerates interfaces, their operational state, addresses, MAC, speed, and MTU.
- Rule engine integration: uses context queries to correlate link metrics across modules and generate diagnosed issues with recommendations.
- Reporting: standardizes outputs as DiagnosticResult objects; console tables are printed for quick inspection.

**Section sources**
- [collector.py:29-179](file://diagnostics/link/collector.py#L29-L179)
- [congestion.py:4-50](file://core/metrics/congestion.py#L4-L50)
- [bandwidth.py:4-16](file://core/metrics/bandwidth.py#L4-L16)
- [throughput.py:4-23](file://core/metrics/throughput.py#L4-L23)
- [interface.py:16-109](file://diagnostics/host/interface.py#L16-L109)
- [context.py:164-179](file://analysis/context.py#L164-L179)
- [link_rules.py:52-95](file://analysis/rules/link_rules.py#L52-L95)
- [result.py:9-47](file://core/result.py#L9-L47)

## Architecture Overview
The link diagnostics pipeline collects raw counters, computes metrics, evaluates thresholds, and produces standardized results. The rule engine consumes these results via a context object to detect higher-level issues.

```mermaid
sequenceDiagram
participant CLI as "CLI / Caller"
participant LC as "Link Collector"
participant PS as "psutil Counters"
participant MET as "Metrics Utils"
participant RES as "DiagnosticResult"
participant RULE as "Rule Engine"
participant CTX as "AnalysisContext"
participant FMT as "Formatter"
CLI->>LC : run_link_diagnostics(interval)
LC->>PS : net_io_counters(pernic=True) x2
LC->>MET : bps_from_byte_delta(), utilization_percent()
LC->>RES : create link_utilization results
LC->>PS : net_io_counters(pernic=True) x2
LC->>MET : compute drops/errors per sec
LC->>RES : create link_errors results
LC->>MET : congestion_score(), congestion_state()
LC->>RES : create link_congestion results
LC-->>CLI : list[DiagnosticResult]
CLI->>RULE : evaluate rules with ctx(results)
RULE->>CTX : query link metrics
RULE-->>CLI : DiagnosedIssue(s)
CLI->>FMT : render_diagnosis_report(report)
FMT-->>CLI : Console report
```

**Diagram sources**
- [collector.py:182-210](file://diagnostics/link/collector.py#L182-L210)
- [throughput.py:11-23](file://core/metrics/throughput.py#L11-L23)
- [congestion.py:4-50](file://core/metrics/congestion.py#L4-L50)
- [context.py:164-179](file://analysis/context.py#L164-L179)
- [formatter.py:12-82](file://analysis/formatter.py#L12-L82)

## Detailed Component Analysis

### Link Utilization Monitoring
- Data collection:
  - Captures per-interface byte counters before and after a sampling interval using system counters.
  - Computes RX and TX bit rates from deltas over the interval.
- Metric calculation:
  - Converts interface speed from Mbps to bits/sec to determine capacity.
  - Calculates utilization percentage as total_bps divided by capacity, capped at 100%.
- Thresholds and status:
  - Utilization >= 90% → DEGRADED/HIGH
  - Utilization >= 75% → DEGRADED/MEDIUM
  - Otherwise → HEALTHY/INFO
- Output:
  - Produces DiagnosticResult entries with rx_bps, tx_bps, total_bps, util_percent, speed_mbps, and evidence strings.

```mermaid
flowchart TD
Start(["Start measure_link_utilization"]) --> SampleBefore["Sample counters before sleep"]
SampleBefore --> Sleep["Sleep interval seconds"]
Sleep --> SampleAfter["Sample counters after sleep"]
SampleAfter --> ComputeRates["Compute RX/TX bps from deltas"]
ComputeRates --> GetSpeed["Get interface speed (Mbps)"]
GetSpeed --> Capacity["Convert to capacity (bps)"]
Capacity --> Util["Compute utilization %"]
Util --> Threshold{"Utilization >= 75%?"}
Threshold --> |Yes| SetDegraded["Set DEGRADED + severity"]
Threshold --> |No| SetHealthy["Set HEALTHY + INFO"]
SetDegraded --> Result["Create DiagnosticResult"]
SetHealthy --> Result
Result --> End(["Return results"])
```

**Diagram sources**
- [collector.py:29-84](file://diagnostics/link/collector.py#L29-L84)
- [bandwidth.py:4-8](file://core/metrics/bandwidth.py#L4-L8)
- [throughput.py:11-23](file://core/metrics/throughput.py#L11-L23)

**Section sources**
- [collector.py:29-84](file://diagnostics/link/collector.py#L29-L84)
- [bandwidth.py:4-8](file://core/metrics/bandwidth.py#L4-L8)
- [throughput.py:11-23](file://core/metrics/throughput.py#L11-L23)

### Link Error Rate Analysis
- Data collection:
  - Samples per-interface drop and error counters before and after the same interval.
- Metric calculation:
  - Computes drops_per_sec and errors_per_sec by dividing counter deltas by the interval.
- Thresholds and status:
  - Drops or errors >= 5/s → FAILED/HIGH
  - Drops or errors >= 0.5/s → DEGRADED/MEDIUM
  - Otherwise → HEALTHY/INFO
- Output:
  - Produces DiagnosticResult entries with drops_per_sec, errors_per_sec, and warnings when active drops/errors are detected.

```mermaid
flowchart TD
StartE(["Start measure_link_errors"]) --> SampleBE["Sample counters before sleep"]
SampleBE --> SleepE["Sleep interval seconds"]
SleepE --> SampleAE["Sample counters after sleep"]
SampleAE --> CalcDrops["drops_per_sec = (dropin+dropout delta)/interval"]
CalcDrops --> CalcErrs["errors_per_sec = (errin+errout delta)/interval"]
CalcErrs --> CheckThresh{"drops>=5 or errors>=5?"}
CheckThresh --> |Yes| Failed["FAILED/HIGH"]
CheckThresh --> |No| CheckMid{"drops>=0.5 or errors>=0.5?"}
CheckMid --> |Yes| Degraded["DEGRADED/MEDIUM"]
CheckMid --> |No| Healthy["HEALTHY/INFO"]
Failed --> ResultE["Create DiagnosticResult"]
Degraded --> ResultE
Healthy --> ResultE
ResultE --> EndE(["Return results"])
```

**Diagram sources**
- [collector.py:87-129](file://diagnostics/link/collector.py#L87-L129)

**Section sources**
- [collector.py:87-129](file://diagnostics/link/collector.py#L87-L129)

### Congestion Detection
- Inputs:
  - Utilization percent from link utilization results
  - Drop and error rates from link error results
  - Optional latency delta in milliseconds
- Scoring:
  - Adds points based on utilization thresholds, drop/error thresholds, and latency delta increases.
  - Score is bounded to [0, 100].
- State mapping:
  - clear (<20), mild (20–44), moderate (45–69), severe (>=70).
- Status mapping:
  - severe → FAILED/HIGH
  - moderate/mild → DEGRADED/MEDIUM
  - clear → HEALTHY/INFO
- Output:
  - Produces DiagnosticResult entries with congestion_score, congestion_state, and contributing metrics.

```mermaid
flowchart TD
StartC(["Start measure_link_congestion"]) --> Gather["Gather util, drops, errors, latency_delta"]
Gather --> Score["Compute congestion_score(...)"]
Score --> MapState["Map score to congestion_state"]
MapState --> MapStatus{"state == severe?"}
MapStatus --> |Yes| SetFailed["FAILED/HIGH"]
MapStatus --> |No| CheckMildMod{"state in {mild, moderate}?"}
CheckMildMod --> |Yes| SetDegraded["DEGRADED/MEDIUM"]
CheckMildMod --> |No| SetHealthy["HEALTHY/INFO"]
SetFailed --> ResultC["Create DiagnosticResult"]
SetDegraded --> ResultC
SetHealthy --> ResultC
ResultC --> EndC(["Return results"])
```

**Diagram sources**
- [collector.py:132-179](file://diagnostics/link/collector.py#L132-L179)
- [congestion.py:4-50](file://core/metrics/congestion.py#L4-L50)

**Section sources**
- [collector.py:132-179](file://diagnostics/link/collector.py#L132-L179)
- [congestion.py:4-50](file://core/metrics/congestion.py#L4-L50)

### Host Interface Inspection
- Enumerates all network interfaces and determines operational state.
- Collects IPv4/IPv6 addresses, MAC address, speed, and MTU.
- Determines whether any non-loopback interface is up; marks down interfaces accordingly.
- Produces DiagnosticResult entries suitable for correlation by the rule engine.

```mermaid
classDiagram
class InterfaceInspector {
+inspect_interfaces() list[DiagnosticResult]
+run_interface_diagnostics() list[DiagnosticResult]
}
class DiagnosticResult {
+module : str
+category : str
+status : DiagnosticStatus
+severity : Severity
+summary : str
+target : str?
+metrics : dict
+evidence : list[str]
+warnings : list[str]
+errors : list[str]
+metadata : dict
}
InterfaceInspector --> DiagnosticResult : "produces"
```

**Diagram sources**
- [interface.py:16-109](file://diagnostics/host/interface.py#L16-L109)
- [result.py:24-47](file://core/result.py#L24-L47)

**Section sources**
- [interface.py:16-109](file://diagnostics/host/interface.py#L16-L109)
- [result.py:24-47](file://core/result.py#L24-L47)

### Rule-Based Analysis and Alerting
- Context queries:
  - Aggregates max link utilization and max link drop rates across modules.
- Rules:
  - AllInterfacesDownRule: triggers when every non-loopback interface is down.
  - ActivePacketDropsRule: triggers when active drops/errors exceed thresholds.
- Recommendations:
  - Provide actionable steps such as checking cabling, drivers, or RF interference.
- Reporting:
  - Formatted diagnosis reports summarize key observations and identified root causes.

```mermaid
sequenceDiagram
participant R as "Rules"
participant C as "AnalysisContext"
participant L as "Link Results"
R->>C : get_max_link_utilization()
C-->>R : float
R->>C : get_active_drop_rate()
C-->>R : float
R->>R : evaluate thresholds
R-->>R : build DiagnosedIssue + Recommendations
R-->>Caller : issue(s)
```

**Diagram sources**
- [context.py:164-179](file://analysis/context.py#L164-L179)
- [link_rules.py:9-49](file://analysis/rules/link_rules.py#L9-L49)
- [link_rules.py:52-95](file://analysis/rules/link_rules.py#L52-L95)

**Section sources**
- [context.py:164-179](file://analysis/context.py#L164-L179)
- [link_rules.py:9-49](file://analysis/rules/link_rules.py#L9-L49)
- [link_rules.py:52-95](file://analysis/rules/link_rules.py#L52-L95)
- [formatter.py:12-82](file://analysis/formatter.py#L12-L82)

## Dependency Analysis
- collector.py depends on:
  - psutil for system counters and interface stats
  - core.metrics.bandwidth for capacity conversion
  - core.metrics.throughput for bps and utilization calculations
  - core.metrics.congestion for scoring and state mapping
  - core.result for standardized output
- interface.py depends on:
  - psutil for interface enumeration and stats
  - core.result for standardized output
- link_rules.py depends on:
  - analysis.context for querying aggregated metrics
  - core.result for severity
- formatter.py depends on:
  - analysis.models for report structures
  - core.result for status/severity rendering

```mermaid
graph LR
COL["collector.py"] --> BW["bandwidth.py"]
COL --> TH["throughput.py"]
COL --> CG["congestion.py"]
COL --> RES["result.py"]
IFACE["interface.py"] --> RES
RULES["link_rules.py"] --> CTX["context.py"]
RULES --> RES
FMT["formatter.py"] --> RES
```

**Diagram sources**
- [collector.py:1-210](file://diagnostics/link/collector.py#L1-L210)
- [interface.py:1-147](file://diagnostics/host/interface.py#L1-L147)
- [link_rules.py:1-96](file://analysis/rules/link_rules.py#L1-L96)
- [context.py:1-199](file://analysis/context.py#L1-L199)
- [formatter.py:1-82](file://analysis/formatter.py#L1-L82)
- [bandwidth.py:1-17](file://core/metrics/bandwidth.py#L1-L17)
- [throughput.py:1-24](file://core/metrics/throughput.py#L1-L24)
- [congestion.py:1-51](file://core/metrics/congestion.py#L1-L51)
- [result.py:1-47](file://core/result.py#L1-L47)

**Section sources**
- [collector.py:1-210](file://diagnostics/link/collector.py#L1-L210)
- [interface.py:1-147](file://diagnostics/host/interface.py#L1-L147)
- [link_rules.py:1-96](file://analysis/rules/link_rules.py#L1-L96)
- [context.py:1-199](file://analysis/context.py#L1-L199)
- [formatter.py:1-82](file://analysis/formatter.py#L1-L82)

## Performance Considerations
- Sampling interval:
  - Short intervals provide timely detection but may increase CPU usage due to frequent polling.
  - Longer intervals smooth out transient spikes but reduce responsiveness.
- Counter granularity:
  - System counters are coarse-grained; very short intervals may yield noisy rates.
- Loopback exclusion:
  - Loopback interfaces are excluded to avoid skewing utilization and error metrics.
- Throughput estimation:
  - Utilization percentage assumes accurate negotiated link speed; unknown speeds result in None utilization.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- High utilization:
  - If util_percent >= 75%, expect DEGRADED status; investigate traffic sources and consider bandwidth upgrades or traffic shaping.
- Active drops/errors:
  - Drops/errors >= 0.5/s trigger DEGRADED; >= 5/s trigger FAILED. Inspect physical cabling, switch ports, duplex settings, and wireless interference.
- Congestion state:
  - Severe congestion indicates combined pressure from high utilization, drops/errors, and/or latency rise. Correlate with path and transport metrics.
- All interfaces down:
  - When no non-loopback interface is UP, the host is isolated. Verify physical connections and adapter status.

**Section sources**
- [collector.py:53-58](file://diagnostics/link/collector.py#L53-L58)
- [collector.py:106-111](file://diagnostics/link/collector.py#L106-L111)
- [collector.py:154-159](file://diagnostics/link/collector.py#L154-L159)
- [link_rules.py:14-49](file://analysis/rules/link_rules.py#L14-L49)

## Conclusion
NetForge’s link diagnostics module delivers robust, standardized monitoring of interface utilization, error rates, and congestion. By combining low-level counter sampling with heuristic scoring and rule-based analysis, it enables early detection of link saturation, hardware issues, and congestion patterns. The standardized DiagnosticResult format ensures consistent consumption by higher-level analysis and reporting components.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Configuration Options
- Monitoring interval:
  - Configurable via the interval parameter in link diagnostics functions. Default is 1.0 second. Adjust based on desired responsiveness and overhead.
- Thresholds:
  - Utilization thresholds: 75% and 90% map to DEGRADED levels.
  - Error/drop thresholds: 0.5/s and 5/s map to DEGRADED and FAILED respectively.
  - Congestion scoring thresholds: utilization bands, drop/error bands, and latency delta increments influence score and state.
- Reporting formats:
  - Console tables are printed for quick inspection.
  - Structured DiagnosticResult objects enable programmatic consumption and integration with rule engines and formatters.

**Section sources**
- [collector.py:29-84](file://diagnostics/link/collector.py#L29-L84)
- [collector.py:87-129](file://diagnostics/link/collector.py#L87-L129)
- [collector.py:132-179](file://diagnostics/link/collector.py#L132-L179)
- [congestion.py:4-50](file://core/metrics/congestion.py#L4-L50)
- [formatter.py:12-82](file://analysis/formatter.py#L12-L82)

### Interpreting Link Health Metrics
- Example scenarios:
  - Stable link: util_percent < 75%, drops/errors near 0, congestion_state clear → HEALTHY.
  - Approaching saturation: util_percent between 75% and 90%, minor drops/errors → DEGRADED/MEDIUM.
  - Overloaded link: util_percent >= 90%, elevated drops/errors, congestion_state moderate/severe → DEGRADED/FAILED.
  - Physical issues: high drops/errors even with low utilization → inspect cabling, NIC, or wireless environment.
- Actionable steps:
  - For saturation: identify top talkers, apply QoS or traffic shaping, or upgrade link capacity.
  - For errors: replace cables, check switch port configuration, verify duplex settings, assess Wi-Fi SNR and channel congestion.
  - For congestion: correlate with path loss and latency; adjust routing or application behavior if necessary.

**Section sources**
- [collector.py:53-58](file://diagnostics/link/collector.py#L53-L58)
- [collector.py:106-111](file://diagnostics/link/collector.py#L106-L111)
- [collector.py:154-159](file://diagnostics/link/collector.py#L154-L159)
- [link_rules.py:52-95](file://analysis/rules/link_rules.py#L52-L95)