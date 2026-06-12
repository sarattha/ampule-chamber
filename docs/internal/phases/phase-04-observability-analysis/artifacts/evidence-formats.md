# Phase 04 Evidence Formats

Runtime evidence is represented as `EvidenceArtifact` records under the
internal `chamber.observability` API.

## Required Fields

| Field | Purpose |
| --- | --- |
| `evidence_id` | Stable reference used by findings and reports. |
| `run_id` | Chamber run identifier from environment metadata. |
| `scenario_id` | Scenario identifier from the validated scenario. |
| `source` | Collector source, such as `kubernetes`, `prometheus`, or `k6`. |
| `signal_type` | MVP signal, such as `pod_status`, `error_rate`, or `logs`. |
| `resource` | Namespace, pod, service, or artifact path being observed. |
| `collected_at` | Collector timestamp in UTC ISO format or fixture provenance. |
| `start_time` / `end_time` | Optional observed window when the backend provides it. |
| `payload` | Raw backend payload preserved for evidence-backed analysis. |
| `diagnostics` | Collector warnings or errors when evidence is partial. |

## Collector Behavior

- Kubernetes pod and log evidence is scoped by
  `EnvironmentMetadata.cleanup_selectors`.
- Kubernetes events are collected at namespace scope and filtered to selected
  pod names through `involvedObject` or `regarding`, because system-generated
  Events do not inherit pod labels.
- Pod status and events are collected as raw Kubernetes JSON.
- Logs are captured per selected pod with `--all-containers=true --tail=200`.
- Built-in Prometheus queries are scoped to the chamber namespace and target
  resource names from `EnvironmentMetadata` before collection.
- Prometheus evidence is required for live metric collection in this phase.
- Missing or unreachable Prometheus backends raise actionable diagnostics
  instead of returning empty metrics.

## Finding References

Analysis findings reference evidence by `evidence_id`. This keeps observed facts
separate from suspected causes and allows phase 05 reports to include raw
evidence pointers without copying full backend payloads.
