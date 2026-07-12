# Releases

## 1.5.2 - 2026-07-12

Ampule Chamber `1.5.2` expands the control-plane exercise editor to match the
multi-journey ChamberConfig contract.

Highlights:

- Add, remove, and configure multiple HTTP or Relayna traffic journeys.
- Edit each journey's method, path, expected response status, request body,
  load schedule, and lifecycle settings.
- Preserve the complete journey array in generated YAML and validate it before
  plan creation.
- Verify multi-journey execution against an existing Kind service.

## 1.5.1 - 2026-07-12

Ampule Chamber `1.5.1` repairs and hardens the container release pipeline.

Highlights:

- Build kubectl and k6 from source with a patched Go toolchain and dependencies.
- Pass the strict Trivy HIGH/CRITICAL image gate without suppressions.
- Scan the release candidate before publishing any GHCR tags.
- Run the container vulnerability gate in pull-request CI.
- Deploy the MkDocs site through GitHub Pages after its one-time repository
  configuration.

## 1.5.0 - 2026-07-12

Ampule Chamber `1.5.0` makes the control-plane UI deployable and operable from
inside an existing Kubernetes cluster.

Highlights:

- Publish a non-root control-plane image with pinned kubectl and k6 binaries.
- Install with Helm or raw Kubernetes resources, including ServiceAccount,
  read-focused namespace RBAC, Service, and PVC-backed workspace.
- Sign in with a bootstrapped `op_live_` admin token and an HttpOnly session.
- Discover Services and backing Deployments or StatefulSets in explicitly
  declared target namespaces.
- Submit Relayna tasks, extract task IDs from HTTP 202 responses, follow their
  SSE event streams, and record terminal lifecycle evidence.
- Exercise the full in-cluster attach flow against a real Kind environment.

## 1.4.1 - 2026-07-11

Ampule Chamber `1.4.1` makes existing-cluster onboarding independent of local
source availability.

Highlights:

- Start an attach plan from an existing Kubernetes Service without a repository.
- Record the backing Deployment or StatefulSet separately from the Service so
  differently named resources pass preflight and discovery.
- Keep repository validation for local and isolated deploy workflows.

## 1.4.0 - 2026-07-11

Ampule Chamber `1.4.0` adds a local control plane without splitting the CLI and
UI reliability engines.

Highlights:

- Guided setup for local, isolated Kubernetes deploy, and existing-deployment
  attach cases.
- Live run state, cancel-and-cleanup, timelines, findings, evidence, exports,
  and compatible-run comparison.
- Collision-safe durable run records, append-only events, evidence digests, and
  a rebuildable local index.
- Evidence-gated readiness: missing required signals produce an inconclusive
  result instead of an invented score.
- Docker Desktop kind acceptance coverage for deploy and attach workflows.

## 1.3.0 - 2026-06-26

Ampule Chamber `1.3.0` adds Kubernetes attach mode for already-live
non-production deployments.

Highlights:

- `runtime.mode: attach` for assessing existing Deployments and Services
  without applying manifests.
- Attach preflight verifies explicit context, namespace safety, existing
  workloads/services, and read permissions.
- Attach discovery records selectors, endpoints, pods, rollout state, and
  pre-test snapshots.
- Kubernetes and Prometheus evidence are scoped to discovered target pods.
- Optional attach faults require an allow-list label or annotation and record
  rollback evidence.

## 1.2.0 - 2026-06-25

Ampule Chamber `1.2.0` improves Kubernetes assessment runs for already-deployed
services and memory-focused reliability checks.

Highlights:

- Multi-journey k6 execution for Kubernetes assessments.
- Optional `agents.exclude` and `--agents-exclude` controls for reviewed
  configs.
- Prometheus memory, CPU, and restart evidence captured in run artifacts.
- Translation-service memory scenarios for health ramp, large-text admission,
  and backpressure reads.
- Deduplicated report limitations with conditional Helm/Kustomize wording.

## 1.1.0 - 2026-06-23

Ampule Chamber `1.1.0` adds generic Kubernetes assessment mode for reviewed
configs and non-production contexts reachable through `kubectl`.

Highlights:

- Provider-neutral Kubernetes runtime config.
- Kubectl preflight and safety evidence before apply.
- `assess --mode kubernetes` live execution with standard run artifacts.
- Generic Kubernetes operator guide with provider credential examples.

## 1.0.0 - 2026-06-18

Ampule Chamber `1.0.0` is the first production-ready release line.

Highlights:

- Guided and one-command assessment workflows.
- Standard run directory artifacts.
- Six bounded agent roles with offline and live modes.
- Evidence-backed markdown report generation.
- MkDocs documentation and GitHub Actions CI/CD automation.

See the repository `CHANGELOG.md` for full release notes.
