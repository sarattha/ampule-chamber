# Control Plane and kind Verification

Date: 2026-07-11

Host: Docker Desktop 29.5.3

Cluster: kind v0.32.0, `kind-ampule-chamber`

Clients: kubectl v1.36.2, k6 v2.1.0

## Automated validation

- 152 unit/integration tests passed.
- Branch-aware coverage met the repository's 90% threshold.
- Ruff format/lint and `ty` static checks passed before real-cluster execution.
- FastAPI route tests cover CSRF rejection, response security headers, plan and
  run APIs, SSE completion, safe evidence access, exports, comparison, custom
  workspace isolation, and background subprocess execution.

## Isolated deploy acceptance

Configuration: `examples/sample-service/chamber-kind.yaml`

Run: `chamber-sample-service-20260711094351531-071f7f78`

- Built `ampule/sample-service:kind` on Docker Desktop and loaded it into the
  `ampule-chamber` kind cluster.
- Kubernetes preflight passed for explicit context `kind-ampule-chamber`.
- The generated chamber namespace reached readiness.
- k6 traffic completed with exit status 0.
- Registered evidence: preflight, Kubernetes commands, k6 script, and k6
  summary; every entry has a SHA-256 digest bound to the run.
- Result: `ready`, conclusive, readiness 100, evidence coverage 100%, execution
  coverage 100%, no missing evidence.
- Chamber-owned namespace cleanup completed and the namespace was absent after
  the run.

## Existing deployment attach acceptance

Configuration: `examples/sample-service/chamber-attach.yaml`

Run: `chamber-sample-service-attach-20260711094621743-4370045c`

- Created `chamber-sample-attach` and deployed the sample Deployment and
  Service before assessment.
- Attach preflight verified the explicit context, namespace, target Deployment,
  Service, read access, and cluster metadata.
- k6 traffic completed with exit status 0.
- Result: `ready`, conclusive, readiness 100, evidence coverage 100%, execution
  coverage 100%, rollback verified, no missing evidence.
- Attach mode left the Deployment, Service, and namespace intact as required.
- The acceptance namespace was deleted explicitly after post-run verification.

## Browser verification

The in-app browser exercised setup progression, Kubernetes selection,
conditional fields, result evidence navigation, desktop 1440 × 1024 layout,
and mobile 390 × 844 layout. The mobile document had no horizontal overflow and
the browser console reported no warnings or errors.
