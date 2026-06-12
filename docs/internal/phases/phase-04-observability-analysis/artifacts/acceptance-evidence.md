# Phase 04 Acceptance Evidence

This file records the implemented evidence path and verification commands for
phase 04.

## Implemented Evidence Path

- Kubernetes collector builds scoped `kubectl` commands from phase 02
  `EnvironmentMetadata` labels and namespace.
- Prometheus collector requires `PROMETHEUS_URL` or an explicit
  `prometheus_url` argument.
- Analysis consumes Kubernetes evidence, Prometheus snapshots, phase 03 k6
  summaries, and phase 03 experiment timelines.
- Findings preserve evidence references and separate observed facts from
  suspected causes.

## Fixture Coverage

`tests/test_phase04_observability_analysis.py` covers:

- OOMKilled detection.
- Restart loop detection.
- High memory and CPU throttling detection.
- High latency and error-rate detection from k6 summaries.
- Error-rate correlation to traffic and fault windows.
- Required Prometheus backend diagnostics.
- Kubernetes collector command construction and JSON parsing.

## Verification

Verification command results are recorded in the phase 04 `ExecPlan.md`.
