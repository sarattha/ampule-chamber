# Phase 02: Environment Orchestration

Implement the chamber environment layer. This phase should make Ampule Chamber
able to create an isolated Kubernetes execution space, deploy a target service,
verify readiness, and clean up after a run.

The phase should support local Kubernetes first, while keeping the interface
compatible with future AKS execution.

## What Needs To Be Done

- Provision a dedicated namespace or ephemeral local cluster target.
- Apply manifests, Helm charts, or Kustomize overlays for a target service.
- Support required config maps, secrets shape, service accounts, and dependency
  descriptors.
- Verify pod startup, readiness probes, liveness stability, service endpoints,
  and initial logs.
- Capture environment metadata for later report generation.
- Implement cleanup and failure-safe teardown.

## Agent Notes

- Do not assume production credentials are available.
- Keep AKS-specific concerns behind interfaces so local development remains
  testable.
- Store generated manifests, dry-run output, and environment snapshots in
  `artifacts/` when they are not source fixtures.

