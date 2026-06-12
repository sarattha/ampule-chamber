# Ampule Chamber Reliability Report

## Service Metadata
- Service: sample-service
- Owner: platform-reliability
- Repository: local workspace /Users/jobz/Works/ampule-chamber
- Commit: fixture-phase-05

## Run Metadata
- Run ID: phase05-sample
- Test date: 2026-06-12
- Duration: 420 seconds
- Namespace: chamber-local-dependency-failure-001-ac334bed
- Provider: kind dry-run with Docker-backed k6 evidence
- Lifecycle state: completed

## Tested Scenario
- Scenario: dependency-failure-001 (Downstream dependency unavailable)
- Path: scenarios/dependency-failure.yaml
- Traffic tool: k6
- Max virtual users: 25
- Faults: downstream-api unavailable for 60 seconds after 2 minutes of traffic

## Readiness Score
- Score: 25/100
- Status: not_ready

## Key Findings
### 1. error_rate on sample-service /dependency
- Severity: high
- Confidence: high
- Evidence: phase05-sample:k6:traffic_summary:live-dependency-fault-k6-summary.json, phase05-sample:phase03:live-k6-results
- Related timeline events: traffic-start, downstream-api-unavailable-start, downstream-api-unavailable-removed, traffic-stop
- Observed facts:
  - k6 recorded 273 dependency-path requests during the Docker-backed dependency fault run.
  - HTTP failure rate was 47.985% while downstream-api was stopped and then restored.
  - The post-restart recovery check returned {"dependency": "ok", "status": 200}.
- Suspected cause: The service returned elevated errors while the required downstream dependency was unavailable.
- Recommended actions:
  - Add or verify graceful dependency-degradation behavior for the /dependency path.
  - Retest after adding retry budgets, fallback responses, or clearer dependency health gating.
  - Keep recovery validation explicit so restored dependencies are proven before promotion.

### 2. memory_usage on target-pods
- Severity: medium
- Confidence: medium
- Evidence: phase05-sample:phase04:fixture-evidence-snapshot
- Related timeline events: traffic-start, traffic-stop
- Observed facts:
  - Phase 04 fixture evidence records memory usage at 94%.
  - The same fixture records CPU throttling at 22%.
- Suspected cause: Target pods may be close to resource limits under stress, which can precede OOM kills or throttling-related latency.
- Recommended actions:
  - Collect live Prometheus memory and CPU throttling metrics for the next service run.
  - Review request and limit sizing before running longer soak or peak scenarios.

### 3. restart_loop on sample-service-abc
- Severity: critical
- Confidence: high
- Evidence: phase05-sample:phase04:fixture-evidence-snapshot
- Related timeline events: traffic-start, traffic-stop
- Observed facts:
  - Phase 04 fixture evidence records lastState.terminated.reason=OOMKilled.
  - Phase 04 fixture evidence records state.waiting.reason=CrashLoopBackOff and restartCount=4.
- Suspected cause: The fixture models a service that repeatedly fails after exceeding memory limits.
- Recommended actions:
  - Treat OOMKilled plus CrashLoopBackOff as a promotion blocker until reproduced or ruled out with live evidence.
  - Capture pod status, Kubernetes events, and memory metrics during the retest.


## Evidence References
- phase05-sample:k6:traffic_summary:live-dependency-fault-k6-summary.json: k6/traffic_summary on sample-service /dependency; collected from-file; artifact docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-dependency-fault-k6-summary.json
- phase05-sample:phase03:live-k6-results: phase-artifact/traffic_summary on sample-service; collected 2026-06-12; artifact docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-k6-results.md
- phase05-sample:phase04:fixture-evidence-snapshot: phase-artifact/evidence_snapshot on phase04-test; collected fixture; artifact docs/internal/phases/phase-04-observability-analysis/artifacts/fixture-evidence-snapshot.json
- phase05-sample:phase03:dependency-failure-dry-run: phase-artifact/experiment_timeline on dependency-failure-001; collected fixture; artifact docs/internal/phases/phase-03-traffic-and-chaos/artifacts/dependency-failure-dry-run.md

## Reproduction Details
### Commands
- uv run ampule-chamber-report --fixture docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-report-input.json --output docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-reliability-report.md
- uv run python -m unittest tests/test_phase05_reporting.py
- make check

### Artifacts
- docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-report-input.json
- docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-reliability-report.md
- docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-dependency-fault-k6-summary.json
- docs/internal/phases/phase-04-observability-analysis/artifacts/fixture-evidence-snapshot.json

## Recommendations
- phase05-sample:k6:error-rate: Add or verify graceful dependency-degradation behavior for the /dependency path.
- phase05-sample:k6:error-rate: Retest after adding retry budgets, fallback responses, or clearer dependency health gating.
- phase05-sample:k6:error-rate: Keep recovery validation explicit so restored dependencies are proven before promotion.
- phase05-sample:fixture:memory-pressure: Collect live Prometheus memory and CPU throttling metrics for the next service run.
- phase05-sample:fixture:memory-pressure: Review request and limit sizing before running longer soak or peak scenarios.
- phase05-sample:fixture:oom-restart-loop: Treat OOMKilled plus CrashLoopBackOff as a promotion blocker until reproduced or ruled out with live evidence.
- phase05-sample:fixture:oom-restart-loop: Capture pod status, Kubernetes events, and memory metrics during the retest.

## Retest Plan
- Deploy the target service into a chamber-owned local Kubernetes namespace.
- Run the dependency-failure scenario with k6 traffic and the reversible dependency fault window.
- Collect Kubernetes pod status, events, logs, k6 summaries, and Prometheus metrics when the backend is available.
- Regenerate the report and require no critical findings plus a readiness status of ready or conditional before promotion.

## Cleanup Notes
- Delete chamber-owned resources by app.kubernetes.io/managed-by=ampule-chamber and chamber.ampule.dev/run-id selectors.
- For the Docker-backed sample evidence path, run docker compose -f examples/sample-service/docker-compose.yml down.
- Keep phase artifact files under docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts for acceptance review.

## Known Limitations
- This sample report is fixture-backed and does not prove a new live Kubernetes run.
- Live Prometheus collection remains required for real CPU, memory, throttling, latency, and error-rate metric evidence.
- The report CLI renders supplied fixture data; it does not yet orchestrate environment, traffic, chaos, or observability phases.
- The readiness score is an MVP rule and should be recalibrated after more real service evaluations.
