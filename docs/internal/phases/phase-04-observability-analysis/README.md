# Phase 04: Observability And Analysis

Implement runtime evidence collection and first-pass reliability analysis. This
phase should collect Kubernetes and telemetry signals, detect failure patterns,
and produce root-cause hypotheses grounded in evidence.

The analysis should be careful about confidence. It should separate observed
facts from inferred causes.

## What Needs To Be Done

- Collect Kubernetes pod status, restart counts, container exit reasons, and
  events.
- Collect logs from target service and relevant dependencies.
- Query Prometheus metrics for CPU, memory, throttling, request latency, and
  error rate where available.
- Prepare trace hooks for future OpenTelemetry backends.
- Detect MVP signals: OOMKilled, restart loop, high memory, high CPU,
  throttling, high latency, and dependency error bursts.
- Correlate signals with traffic and fault timeline events.
- Generate root-cause hypotheses with severity and confidence.

## Agent Notes

- Preserve raw evidence where practical and avoid overwriting it.
- Do not claim a root cause when the evidence only supports a symptom.
- Store metric snapshots, log excerpts, event dumps, and analysis scratch files
  in `artifacts/`.

