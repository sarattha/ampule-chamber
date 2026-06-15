# Ampule Chamber Reliability Report

## Service Metadata
- Service: sample-service
- Owner: platform-reliability
- Repository: /Users/jobz/Works/ampule-chamber
- Commit: a11d2be

## Run Metadata
- Run ID: chamber-multi-service-dependency-001-20260615154932
- Test date: 2026-06-15
- Duration: 318 seconds
- Namespace: chamber-local-multi-service-dependency-001-8278f4e7
- Provider: kind
- Lifecycle state: completed

## Tested Scenario
- Scenario: multi-service-dependency-001 (Target contains downstream dependency failure)
- Path: scenarios/multi-service-dependency.yaml
- Traffic tool: k6
- Max virtual users: 15
- Faults: dependency_errors

## Readiness Score
- Score: 90/100
- Status: ready

## Key Findings
### 1. retry_amplification on docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/multi-service-dependency-001-k6-summary.json
- Severity: medium
- Confidence: low
- Evidence: chamber-multi-service-dependency-001-20260615154932:k6-summary:multi-service-dependency-001-k6-summary.json
- Related timeline events: traffic-start, downstream-api-dependency_errors-start, downstream-api-dependency_errors-removed, traffic-stop
- Observed facts:
  - Scenario declares retry-amplification as a failure condition.
  - k6 HTTP failure rate during dependency-path traffic was 1.31222%.
- Suspected cause: Dependency degradation may be propagating through the target request path.
- Recommended actions:
  - Review the recorded evidence and rerun the chamber scenario.


## Evidence References
- chamber-multi-service-dependency-001-20260615154932:kubernetes:pod_status:chamber-local-multi-service-dependency-001-8278f4e7: kubernetes/pod_status on chamber-local-multi-service-dependency-001-8278f4e7; collected 2026-06-15T15:54:08.892900+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:kubernetes:kubernetes_events:chamber-local-multi-service-dependency-001-8278f4e7: kubernetes/kubernetes_events on chamber-local-multi-service-dependency-001-8278f4e7; collected 2026-06-15T15:54:08.892900+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:kubernetes:logs:downstream-api-deployment-8278f4e7-564794dd48-9vbrd: kubernetes/logs on downstream-api-deployment-8278f4e7-564794dd48-9vbrd; collected 2026-06-15T15:54:08.892900+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:kubernetes:logs:sample-service-deployment-8278f4e7-6b75745f5c-kwzkc: kubernetes/logs on sample-service-deployment-8278f4e7-6b75745f5c-kwzkc; collected 2026-06-15T15:54:08.892900+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:prometheus:memory_usage:target-pods: prometheus/memory_usage on target-pods; collected 2026-06-15T15:54:08.998050+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:prometheus:cpu_usage:target-pods: prometheus/cpu_usage on target-pods; collected 2026-06-15T15:54:08.998050+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:prometheus:cpu_throttling:target-pods: prometheus/cpu_throttling on target-pods; collected 2026-06-15T15:54:08.998050+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:prometheus:request_latency:target-service: prometheus/request_latency on target-service; collected 2026-06-15T15:54:08.998050+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:prometheus:error_rate:target-service: prometheus/error_rate on target-service; collected 2026-06-15T15:54:08.998050+00:00; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- chamber-multi-service-dependency-001-20260615154932:k6-summary:multi-service-dependency-001-k6-summary.json: k6/traffic_summary on http://127.0.0.1:22036/dependency; collected from-file; artifact docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/multi-service-dependency-001-k6-summary.json

## Dependency Graph
- sample-service -> downstream-api (service/downstream-api-svc-8278f4e7:8081)

## Agent Analysis
- No live phase 07 agent analysis was attached to this runner output.
- Agent outputs must cite supplied evidence before report inclusion.

## Recovery Status
- Status: degraded_or_inconclusive.
- Recovery checkpoints: 1.
- Cleanup: cleanup completed.

## Reproduction Details
### Commands
- uv run ampule-chamber run --scenario scenarios/multi-service-dependency.yaml --output docs/internal/phases/phase-09-multi-service-chamber/artifacts/live-multi-service-report.md

### Artifacts
- docs/internal/phases/phase-09-multi-service-chamber/artifacts/live-multi-service-report.md
- docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/run-metadata.json
- docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/evidence.json
- docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/multi-service-dependency-001-k6-summary.json

## Recommendations
- chamber-multi-service-dependency-001-20260615154932:k6:retry-amplification: Review the recorded evidence and rerun the chamber scenario.

## Retest Plan
- Rerun multi-service-dependency-001 after remediation using the same kind context.
- Require no critical findings and verify cleanup status before promotion.

## Cleanup Notes
- Cleanup completed for chamber-owned resources.

## Known Limitations
- No phase-specific limitations recorded.
