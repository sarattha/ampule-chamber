# Ampule Chamber Reliability Report

## Service Metadata
- Service: sample-service
- Owner: platform-reliability
- Repository: /Users/jobz/Works/ampule-chamber
- Commit: 396ff01

## Run Metadata
- Run ID: phase06-baseline-health-001-20260613063240
- Test date: 2026-06-13
- Duration: 250 seconds
- Namespace: chamber-local-baseline-health-001-2cfb47c6
- Provider: kind
- Lifecycle state: completed

## Tested Scenario
- Scenario: baseline-health-001 (Baseline health and readiness)
- Path: scenarios/baseline-health.yaml
- Traffic tool: k6
- Max virtual users: 5
- Faults: none

## Readiness Score
- Score: 100/100
- Status: ready

## Key Findings
- No reliability findings detected in the supplied evidence.

## Evidence References
- phase06-baseline-health-001-20260613063240:kubernetes:pod_status:chamber-local-baseline-health-001-2cfb47c6: kubernetes/pod_status on chamber-local-baseline-health-001-2cfb47c6; collected 2026-06-13T06:36:08.428531+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:kubernetes:kubernetes_events:chamber-local-baseline-health-001-2cfb47c6: kubernetes/kubernetes_events on chamber-local-baseline-health-001-2cfb47c6; collected 2026-06-13T06:36:08.428531+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:kubernetes:logs:sample-service-deployment-2cfb47c6-7fb5954876-5vzf4: kubernetes/logs on sample-service-deployment-2cfb47c6-7fb5954876-5vzf4; collected 2026-06-13T06:36:08.428531+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:prometheus:memory_usage:target-pods: prometheus/memory_usage on target-pods; collected 2026-06-13T06:36:08.485197+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:prometheus:cpu_usage:target-pods: prometheus/cpu_usage on target-pods; collected 2026-06-13T06:36:08.485197+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:prometheus:cpu_throttling:target-pods: prometheus/cpu_throttling on target-pods; collected 2026-06-13T06:36:08.485197+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:prometheus:request_latency:target-service: prometheus/request_latency on target-service; collected 2026-06-13T06:36:08.485197+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:prometheus:error_rate:target-service: prometheus/error_rate on target-service; collected 2026-06-13T06:36:08.485197+00:00; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- phase06-baseline-health-001-20260613063240:k6-summary:baseline-health-001-k6-summary.json: k6/traffic_summary on http://127.0.0.1:23726/healthz; collected from-file; artifact docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/baseline-health-001-k6-summary.json

## Reproduction Details
### Commands
- uv run ampule-chamber run --scenario scenarios/baseline-health.yaml --output docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md

### Artifacts
- docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md
- docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/run-metadata.json
- docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json
- docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/baseline-health-001-k6-summary.json

## Recommendations
- No recommendations recorded.

## Retest Plan
- Rerun baseline-health-001 after remediation using the same kind context.
- Require no critical findings and verify cleanup status before promotion.

## Cleanup Notes
- Cleanup completed for chamber-owned resources.

## Known Limitations
- No phase-specific limitations recorded.
