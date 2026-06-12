# ExecPlan: Phase 04 Observability And Analysis

## Description

Build the evidence collection and analysis layer that turns chamber runs into
actionable reliability findings. This phase should collect runtime signals,
correlate them with experiment events, and classify likely failure modes.

## Task Checklist

- [x] Define evidence artifact formats for Kubernetes events, logs, metrics,
      traces, and experiment timelines.
- [x] Implement Kubernetes pod status and event collection.
- [x] Implement log collection for target pods and selected dependencies.
- [x] Implement Prometheus query configuration for MVP metrics.
- [x] Detect OOMKilled, restart loops, high memory, high CPU, throttling, high
      latency, and dependency error bursts.
- [x] Correlate detected signals with traffic and fault windows.
- [x] Define severity and confidence scoring rules.
- [x] Add fixture-based tests for signal detection and correlation.

## Evaluation Metrics

- Evidence artifacts include source, timestamp range, target resource, and run
  identifier.
- Signal detectors identify known fixture failures with deterministic output.
- Analysis distinguishes observed evidence from inferred hypotheses.
- Severity and confidence scores are explainable from recorded evidence.
- Correlation output references traffic and fault timeline IDs.

## Acceptance Criteria

- A fixture chamber run can produce at least three detected findings from
  evidence inputs.
- Each finding includes evidence references, suspected cause, severity,
  confidence, and affected resource.
- Missing observability backends degrade clearly with actionable diagnostics
  rather than silent empty results.

## Progress

- [x] Added `chamber/observability/` evidence contracts, scoped Kubernetes
      collection, log collection, required Prometheus querying, and actionable
      collection failures.
- [x] Added `chamber/analysis/` finding contracts and detectors for pod status,
      Kubernetes events, Prometheus snapshots, and k6 summaries.
- [x] Added timeline correlation from findings to phase 03 traffic and fault
      event names.
- [x] Added fixture-backed phase 04 tests for collection, detection,
      correlation, k6 summaries, and Prometheus diagnostics.
- [x] Added phase artifacts for evidence formats, Prometheus query contracts,
      fixture evidence snapshots, and acceptance evidence.

## Surprises And Discoveries

- Live Prometheus is intentionally required by the implementation boundary, but
  deterministic tests must still mock the HTTP API so repository checks do not
  depend on a local cluster or telemetry stack.
- Adding phase 04 modules initially dropped total coverage below the 90%
  project threshold. Focused collector and detector branch tests raised total
  coverage to 91% without changing the threshold.
- Phase 03 k6 summary artifacts already contain enough evidence to validate
  dependency error burst detection in phase 04.

## Decision Log

- Keep scenario YAML unchanged for phase 04. Thresholds are derived from
  existing failure conditions when they are stricter than MVP defaults.
- Use `PROMETHEUS_URL` as the default live metrics endpoint source, with an
  explicit function argument override for tests and future orchestration.
- Treat Prometheus values for metric signals as already normalized to percent
  or milliseconds by the query contract.
- Keep collectors and analysis as internal Python APIs under
  `chamber/observability/` and `chamber/analysis/`; no phase 04 CLI is added.
- Findings must include observed facts and suspected causes separately so phase
  05 reporting can avoid overstating root cause confidence.

## Outcomes And Retrospective

- Evidence artifacts now include run id, scenario id, source, signal type,
  resource, collection time, raw payload, and optional diagnostics.
- Kubernetes collection captures pod status, events, and per-pod logs scoped by
  chamber run selectors from `EnvironmentMetadata`.
- Prometheus collection queries MVP metrics and fails clearly when the required
  backend is missing, unreachable, malformed, or returns a non-success status.
- Analysis can produce at least three findings from fixture evidence, including
  OOMKilled, restart loop, memory usage, CPU throttling, request latency, and
  error-rate findings.
- Findings include evidence references, suspected cause, severity, confidence,
  affected resource, and related traffic or fault timeline event names.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase04_observability_analysis.py`
    passes 11 focused phase 04 tests.
  - `make format` passes.
  - `make lint` passes.
  - `make typecheck` passes.
  - `make test` passes 49 tests.
  - `make coverage` passes with 91% total coverage.
  - `make validate-scenarios` validates all five scenario files.
