# ExecPlan: Phase 14 Live Deployment Attach Mode

## Description

Implement the attach/in-place Kubernetes assessment workflow requested in
GitHub issue #17 as one coherent phase and one PR.

## Task Checklist

- [x] Add the phase-14 planning folder.
- [x] Add `runtime.mode: deploy | attach` validation.
- [x] Allow attach configs to omit deploy manifests while requiring explicit
      services, workloads, context, namespace, and `cleanup: false`.
- [x] Add attach-mode Kubernetes preflight checks for namespace, RBAC,
      workload, and service existence.
- [x] Add attach planning without adapted manifests.
- [x] Discover configured workloads, services, endpoints, selectors, pods,
      rollout status, and ownership metadata.
- [x] Record `attach-discovery.json` and `pre-test-state.json`.
- [x] Reuse k6 port-forward traffic against the attached service.
- [x] Scope Kubernetes evidence to discovered pods.
- [x] Scope optional Prometheus evidence to namespace plus discovered pods.
- [x] Add observe-only default behavior with no apply/delete cleanup.
- [x] Add gated attach fault primitives for pod kill and Deployment scale.
- [x] Record rollback evidence and manual remediation details when restore
      fails.
- [x] Render attach-mode evidence and lifecycle metadata in reports.
- [x] Add focused unit tests for attach preflight, observe-only behavior,
      mutation gating, rollback, and deploy-mode compatibility.
- [x] Run real kind/docker verification against the live translation-service.
- [ ] Create draft PR, wait for CI, address reviews, and mark ready.

## Evaluation Metrics

- Attach configs can run through `ampule-chamber assess --config chamber.yaml
  --mode kubernetes` without manifest apply.
- Attach preflight fails before traffic or mutation when context, namespace,
  workload, service, or read permissions are invalid.
- Observe-only attach runs do not execute `kubectl apply`, namespace delete, or
  external resource cleanup commands.
- Attach reports cite attach discovery, pre-test state, k6, Kubernetes command,
  Prometheus when configured, and rollback evidence.
- Fault runs require `chamber.ampule.dev/allow-faults: "true"` on namespace or
  selected workload metadata.
- Fault rollback verification is recorded and failed rollback marks the run
  failed/not-ready.
- Existing deploy-mode Kubernetes tests continue to pass.
- `make check` passes.
- Live kind/docker validation demonstrates the feature against
  translation-service as an already-live service.

## Acceptance Criteria

- A reviewed attach config can assess an existing non-production namespace.
- Attach mode discovers and records selected Deployment/Service/pod state before
  traffic.
- Attach mode runs k6 traffic and writes standard run directory artifacts.
- Evidence collection is scoped to configured workloads/pods.
- Reports clearly distinguish attach mode from deploy mode.
- Observe-only attach mode performs no Kubernetes mutations except
  port-forward setup.
- Explicit fault injection requires an allow-list label or annotation and
  records rollback evidence.
- Cleanup never deletes the target namespace or externally deployed resources.
- Existing deploy-mode Kubernetes tests and behavior continue to pass.
- `make check` passes.

## Progress

- 2026-06-26: Added attach-mode validation, preflight, planning, discovery,
  scoped evidence, Prometheus pod scoping, gated pod kill and Deployment scale
  faults, rollback evidence, report references, and focused tests.
- 2026-06-26: Addressed PR review feedback by constraining explicit
  attach-mode fault targets to discovered pods/Deployments and preserving
  zero-replica Deployment rollback state.

## Acceptance Evidence

- 2026-06-26: `uv run ruff check chamber tests` passed.
- 2026-06-26: `uv run python -m unittest tests/test_kubernetes_preflight.py
  tests/test_phase11_12_13_workflow.py` passed 49 tests.
- 2026-06-26: Real kind attach verification passed against
  `translation-service` in namespace `chamber-external-translation-prep`.
  Evidence is recorded in
  `artifacts/live-kind-attach-verification-20260626.md`. Observe-only run
  `.chamber/runs/chamber-translation-service-20260626014535/` passed 2,489 k6
  checks with no mutations beyond port-forward and cleanup disabled. Gated
  pod-kill run `.chamber/runs/chamber-translation-service-20260626014759/`
  passed 2,452 k6 checks, recorded rollback evidence, verified Deployment
  availability, and left the target namespace/resources intact.
- 2026-06-26: `make check` passed after the implementation and live
  verification updates. The gate ran format, lint, typecheck, 138 tests,
  coverage at 90%, scenario validation, release metadata validation for
  `1.3.0`, strict MkDocs build, and package build.
- 2026-06-26: PR #18 security CI initially failed because Semgrep flagged
  Redis and RabbitMQ containers in the live attach manifest for missing
  non-root and privilege-escalation security contexts. Hardened those
  containers, verified the manifest with local Semgrep at 0 findings, reapplied
  it to kind, confirmed dependency and target rollouts, and reran observe-only
  attach as `.chamber/runs/chamber-translation-service-20260626015405/` with
  2,446/2,446 k6 checks passing.
- 2026-06-26: `uv run python -m unittest tests/test_phase11_12_13_workflow.py`
  passed 47 tests after adding regression coverage for undiscovered
  `pod_kill` targets, undiscovered `deployment_scale` targets, and
  zero-replica rollback preservation.
- 2026-06-26: `make check` passed after PR review fixes. The gate ran format,
  lint, typecheck, 141 tests, coverage at 90%, scenario validation, release
  metadata validation for `1.3.0`, strict MkDocs build, and package build.

## Decision Log

- Chose `runtime.mode: attach` under the existing `runtime.provider:
  kubernetes` contract instead of introducing a second CLI mode.
- Chose `deployment.workloads` and `deployment.services` as the attach target
  contract to stay aligned with the existing ChamberConfig shape.
- Chose `cleanup: false` as mandatory in attach mode because external
  namespaces and resources are not chamber-owned.
- Chose namespace/workload label or annotation key
  `chamber.ampule.dev/allow-faults: "true"` as the mutation gate.
- Chose pod kill and Deployment scale as the first reversible attach fault
  primitives.
