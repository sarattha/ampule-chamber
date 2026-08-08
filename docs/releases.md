# Releases

## 1.9.0 - 2026-08-08

Ampule Chamber `1.9.0` makes the control plane an accessible operational
reliability workspace instead of a collection of run-detail screens.

Highlights:

- Find active, failed, regressed, inconclusive, and cleanup-attention runs with
  saved views, URL-backed filters, service history, tags, and reversible archive
  controls.
- Read one decision-oriented Overview and structured HTML report with tested
  scope, evidence limits, cited findings, remediation, retest, and safety
  verification while retaining Markdown and JSON exports.
- Compare only compatible earlier baselines, including evidence-safe signal,
  finding, score, scenario, and environment deltas.
- Start from six bounded goal proposals, edit the essential traffic and optional
  fault controls, and move to the full Advanced journey configuration without
  losing values.
- Navigate the main workflows by keyboard with restrained live announcements,
  visible focus, 44-pixel targets, responsive high-zoom reflow, and print-safe
  reports.

## 1.8.0 - 2026-08-08

Ampule Chamber `1.8.0` connects scenario design, operational decisions, and
evidence investigation into one reviewable workflow.

Highlights:

- Start from one of six reliability goals and review bounded traffic, recovery,
  assumptions, missing inputs, safety limits, and generated configuration.
- Switch between Basic and Advanced editing without losing imported journey or
  agent settings; faults remain an explicit operator choice.
- Read a plain-language verdict with required, present, and missing evidence,
  configuration links, prioritized actions, and a safe setup-rerun plan.
- Distinguish ready, inconclusive, failed, cancelled, local-only, and
  preflight-failed outcomes without inventing readiness scores.
- Investigate traffic, faults, Kubernetes events, metrics, safe logs, and
  Relayna task events on one filterable, paginated timeline.
- Open findings at their cited time window, distinguish exact from run-window
  or inferred correlation, and download only digest-verified raw artifacts.
- Ship fixed Python dependencies and source-built k6 and kubectl clients that
  pass the release HIGH/CRITICAL repository and container vulnerability gates.

## 1.7.0 - 2026-07-13

Ampule Chamber `1.7.0` turns Relayna runtime artifacts into a digestible
assessment view.

Highlights:

- Show API and short-lived Relayna worker pod metrics side by side.
- Derive peak CPU, peak memory, restarts, and sample counts across the complete
  traffic window.
- Keep every concurrent SSE feed grouped by the task ID returned from its own
  admission response.
- Retain only bounded operational event fields, excluding arbitrary messages
  and OCR content.
- Mark worker-to-task links as exact only when a Kubernetes task label matches;
  otherwise show honest run-level correlation.
- Allow namespace-scoped read access to pod metrics for optional `kubectl top`
  evidence.

## 1.6.0 - 2026-07-13

Ampule Chamber `1.6.0` adds reusable, editable control-plane scenarios and
brings file and image submissions into the Relayna task-lifecycle adapter.

Highlights:

- Submit multiple required or optional files alongside deterministic scalar
  and explicit JSON multipart fields.
- Follow the configured parent task ID through the existing SSE lifecycle and
  readiness path.
- Bound, validate, and workspace-contain uploads before planning or execution.
- Record only filenames, content types, sizes, field names, and SHA-256 digests
  in evidence and reports.
- Configure and review multi-file Relayna journeys in the control-plane UI.
- Choose bundled or durable workspace scenarios with metadata, compatibility
  warnings, search, and adapter or fault filtering.
- Import validated `Scenario` or `ChamberConfig` YAML/JSON and resolve it into
  the existing editable exercise fields before planning.
- Create custom scenario identities and safely save or explicitly replace user
  scenarios on the configured workspace/PVC.
- Preserve scenario ID, source, and content revision in run metadata while
  keeping all imported faults disabled until an operator selects them.

## 1.5.3 - 2026-07-13

Ampule Chamber `1.5.3` makes Prometheus-backed readiness scoring evidence-safe.

Highlights:

- Require non-empty series from every required Prometheus query before metrics
  can contribute to a readiness score.
- Keep failed and empty queries available as diagnostic artifacts while marking
  the related evidence inconclusive.
- Query every selected workload pod with a safely escaped PromQL alternation.
- Show query text, labels, values, timestamps, and artifact references in the
  generated reliability report.

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
