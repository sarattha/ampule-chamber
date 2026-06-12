# Phase 09: Multi-Service Chamber

Extend Ampule Chamber from a single target service into a chamber that can
model, deploy, observe, and analyze a target service plus controlled
dependencies. This phase should make cascading failure and service-to-service
resilience testing a first-class capability.

The goal is not a full production mesh simulator. The first multi-service
chamber should support one target service and at least one controlled dependency
inside the same chamber namespace, with traffic that exercises the dependency
path and evidence that shows whether the target contained or amplified the
failure.

## What Needs To Be Done

- Extend scenario contracts to represent a dependency graph.
- Model dependency metadata such as service name, type, protocol, endpoint,
  criticality, timeout expectations, retry expectations, and fallback behavior.
- Add a controlled dependency fixture service for local Kubernetes runs.
- Deploy the target service and dependency service into the chamber namespace.
- Generate traffic that exercises service-to-service behavior instead of only
  health endpoints.
- Inject dependency faults and observe target-service behavior.
- Collect Kubernetes events, pod status, logs, metrics, and timeline data across
  target and dependency resources.
- Detect cascading failure signals such as retry amplification, dependency error
  propagation, latency fan-out, queue buildup, and failed recovery.
- Extend reports with dependency graph, affected services, cascading failure
  analysis, and per-service evidence references.
- Record multi-service live or dry-run artifacts in `artifacts/`.

## Agent Notes

- Keep the first dependency graph small and inspectable: one target and one
  controlled dependency is enough for acceptance.
- Prefer deterministic local fixtures before supporting arbitrary external
  dependency stacks.
- Do not hide dependency evidence inside target-service summaries. Reports
  should identify which service produced each signal.
- Preserve phase 06-08 safety guardrails for every service and fault.
