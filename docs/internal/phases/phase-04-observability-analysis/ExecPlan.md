# ExecPlan: Phase 04 Observability And Analysis

## Description

Build the evidence collection and analysis layer that turns chamber runs into
actionable reliability findings. This phase should collect runtime signals,
correlate them with experiment events, and classify likely failure modes.

## Task Checklist

- [ ] Define evidence artifact formats for Kubernetes events, logs, metrics,
      traces, and experiment timelines.
- [ ] Implement Kubernetes pod status and event collection.
- [ ] Implement log collection for target pods and selected dependencies.
- [ ] Implement Prometheus query configuration for MVP metrics.
- [ ] Detect OOMKilled, restart loops, high memory, high CPU, throttling, high
      latency, and dependency error bursts.
- [ ] Correlate detected signals with traffic and fault windows.
- [ ] Define severity and confidence scoring rules.
- [ ] Add fixture-based tests for signal detection and correlation.

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

- [ ] Not started.

## Surprises And Discoveries

- None yet.

## Decision Log

- None yet.

## Outcomes And Retrospective

- Pending.

