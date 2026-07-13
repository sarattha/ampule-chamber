# Changelog

All notable changes to Ampule Chamber are documented in this file.

## 1.7.0 - 2026-07-13

### Added

- Added per-pod Prometheus run-window summaries for peak CPU, peak memory,
  restarts, and sample coverage in the assessment Evidence tab.
- Added post-traffic discovery and evidence for short-lived Relayna Job workers
  so worker metrics appear beside the selected API pods.
- Added bounded, content-safe Relayna event feeds grouped under each admission
  task ID, including concurrent multi-VU journeys.

### Changed

- Relayna worker-to-task correlation now distinguishes exact Kubernetes task
  labels from run-window, Job-owner, and service-label inference.
- Target namespace RBAC now grants read-only pod access through
  `metrics.k8s.io` for `kubectl top` evidence collection.

### Documentation

- Documented the runtime metrics cards, per-task event feeds, legacy artifact
  fallback, worker correlation limits, and real AKS validation evidence.

## 1.6.0 - 2026-07-13

### Added

- Added multipart Relayna lifecycle execution with multiple named required or
  optional files, deterministic scalar and explicit JSON form serialization,
  bounded upload sizes, parent task-ID extraction, and existing SSE terminal
  status semantics.
- Added safe upload metadata and SHA-256 digests to Relayna evidence and
  reports without persisting file bytes or paths in lifecycle summaries.
- Added multi-file Relayna controls and Review-step metadata to the control
  plane, backed by workspace-contained durable uploads.
- Added a reusable scenario catalog to the control-plane Exercise step with
  bundled and durable workspace scenarios, metadata previews, search, adapter
  and fault filters, editable loading, and custom scenario identity fields.
- Added server-validated YAML/JSON import for supported `Scenario` and
  `ChamberConfig` documents plus save-as-new and explicitly confirmed user
  scenario replacement.
- Recorded scenario ID, source, and content revision in generated configs and
  durable run metadata.

### Changed

- Relayna validation now accepts JSON or multipart requests while preserving
  existing JSON Relayna and HTTP multipart behavior.
- Multipart lifecycle failures now identify admission, task-ID extraction, SSE
  connection, timeout, or terminal-status stages.
- Selected and imported scenarios now populate the existing journey editor and
  safety review; configured faults remain disabled until explicitly selected.

### Documentation

- Added single-file OCR and multi-file extraction ChamberConfig examples,
  serialization rules, supported content types, limits, and evidence behavior.
- Documented supported scenario formats, catalog storage, import validation,
  persistence, provenance, and the reviewed control-plane workflow.

## 1.5.3 - 2026-07-13

### Fixed

- Prometheus verification now distinguishes reachable queries from queries that
  return usable time-series evidence. Required zero-series or failed queries
  keep the assessment inconclusive instead of awarding an unsupported score.
- Multi-pod PromQL selectors now preserve regex alternation and escape pod names
  safely, so evidence is collected for every selected workload pod.

### Documentation

- Documented the Prometheus query artifact schema, evidence sufficiency rules,
  failure diagnostics, and a real Prometheus validation run.

## 1.5.2 - 2026-07-12

### Added

- Added a repeatable traffic-journey editor to the control plane for multiple
  HTTP or Relayna journeys, editable expected response statuses, request
  bodies, load schedules, lifecycle settings, and follow-up checks.
- Added no-body, JSON, multipart file upload, URL-encoded form, and raw-text
  request encodings, including durable UI upload storage and k6 multipart
  generation for file-based services such as OCR APIs.

### Changed

- Control-plane plans now preserve the complete journey array in the generated
  ChamberConfig instead of reducing the exercise to one hard-coded journey.

## 1.5.1 - 2026-07-12

### Fixed

- Rebuilt the embedded kubectl and k6 executables from source with Go 1.26.5
  and patched dependencies, eliminating the fixed HIGH and CRITICAL findings
  that blocked the 1.5.0 release pipeline.
- Moved the release image vulnerability gate before registry publication so a
  failed scan cannot update the version, minor, or `latest` GHCR tags.

### Changed

- Added the same strict image vulnerability scan to pull-request CI so release
  image regressions are caught before a version tag is created.
- Added GitHub Pages configuration to the documentation deployment workflow.

### Documentation

- Documented the image security gate, failed-release recovery, and one-time
  GitHub Pages repository setup.

## 1.5.0 - 2026-07-12

### Added

- Added a non-root control-plane image with pinned kubectl and k6 binaries plus
  a GHCR release workflow with image SBOM, vulnerability scan, and provenance.
- Added a Helm chart and raw Kubernetes resources for the Deployment, Service,
  ServiceAccount, authentication Secret, in-cluster kubeconfig, target RBAC,
  probes, security contexts, and PVC-backed workspace.
- Added Relayna Gateway-style operator-token sign-in with an HttpOnly browser
  session and Bearer-token API authentication.
- Added declared-namespace discovery for existing Services, Deployments, and
  StatefulSets, including selector-based Service-to-workload matching.
- Added a reusable Relayna task-lifecycle traffic adapter that submits JSON,
  extracts the returned task ID, consumes the task SSE stream, waits for a
  configured terminal status, and records end-to-end lifecycle evidence.
- Added control-plane fields and Kubernetes configuration documentation for
  Relayna submission paths, request bodies, task ID response paths, event
  paths, concurrency, iterations, and completion timeouts.

### Changed

- Attach preflight now checks the Kubernetes API `/readyz` endpoint without
  requiring broad access to Services in `kube-system`.
- Remote UI binding now requires both explicit `--allow-remote` opt-in and a
  valid bootstrapped admin token.
- Control-plane runs, evidence, logs, reports, and the SQLite index persist on
  the configured Kubernetes PVC across pod replacement.

### Documentation

- Added tested Helm, raw-manifest, port-forward, Ingress/TLS, RBAC, discovery,
  authentication, and persistence instructions with screenshots from Kind.

## 1.4.1 - 2026-07-11

### Added

- Added a repository-free control-plane target path for attaching to an
  existing Kubernetes Service.
- Added explicit backing workload name and kind fields so Services backed by a
  differently named Deployment or StatefulSet are discovered correctly.

### Changed

- Attach configs now use a stable `kubernetes://` target reference when no
  source repository is available.
- Repository validation remains required for local and isolated deploy modes
  but is no longer required for Kubernetes attach mode.

## 1.4.0 - 2026-07-11

### Added

- Added a FastAPI and server-rendered local control-plane UI with a guided
  repository, environment, exercise, safety-review, and explicit-run workflow.
- Added live subprocess execution with SSE status updates and cancel-to-cleanup
  behavior, plus run detail tabs for timeline, findings, evidence,
  configuration, and agent outputs.
- Added evidence-backed readiness results, HTML/Markdown/JSON report export,
  compatible-run comparison, capability discovery, and a versioned local API.
- Added collision-safe run directories, canonical `run.json`, append-only
  `events.jsonl`, SHA-256 evidence manifests, and a rebuildable SQLite index.
- Added checked-in deploy and attach examples validated on Docker Desktop kind.

### Changed

- CLI and UI now share an application facade and assessment-result builder.
- Reports no longer invent readiness or findings when required evidence is
  missing; incomplete and local-only runs are explicitly inconclusive.
- Cancellation persists a cancelled result after Kubernetes rollback and
  cleanup instead of bypassing final evidence and report generation.

### Security

- The UI binds to loopback by default, rejects remote binding without explicit
  opt-in, uses strict CSRF validation, applies CSP and anti-framing headers, and
  serves only digest-validated evidence registered to the requested run.

## 1.3.0 - 2026-06-26

### Added

- Added Kubernetes attach mode for assessing existing non-production
  deployments without applying manifests or deleting externally owned
  namespaces/resources.
- Added attach-mode preflight checks for explicit context and namespace safety,
  existing namespace/workload/service verification, and read-focused RBAC.
- Added attach discovery and pre-test state evidence for workloads, services,
  endpoints, selectors, rollout status, pods, and ownership metadata.
- Added scoped attach evidence collection for discovered pods, including pod
  logs, pod events, pod status, and optional Prometheus pod-scoped metrics.
- Added gated attach fault primitives for pod restart and Deployment scale with
  allow-list enforcement and rollback evidence.
- Added phase 14 planning docs for live deployment attach mode.

### Changed

- Kubernetes report generation now includes attach-specific evidence references,
  lifecycle mode, cleanup notes, and rollback status when attach mode is used.
- Public Kubernetes docs now cover deploy mode and attach mode separately,
  including attach config, safety warnings, evidence files, and fault gating.

## 1.2.0 - 2026-06-25

### Added

- Added Kubernetes assessment support for running all configured traffic
  journeys as named k6 scenarios in one pass, including staged ramps and
  bounded iteration workloads.
- Added optional agent-role exclusion through `agents.exclude` and
  `--agents-exclude`, allowing reviewed Kubernetes configs to skip roles such
  as `onboarding-agent` when no local source repository is available.
- Added Prometheus memory evidence collection for container working set, CPU
  usage, and restart counters during Kubernetes assessments.
- Added three memory-focused translation-service scenarios for health ramp,
  large-text admission, and runtime backpressure reads.

### Changed

- Normalized k6 evidence summaries so agent assessments focus on observed
  request, check, latency, and journey results instead of raw threshold
  bookkeeping.
- Deduplicated report limitations and made Helm/Kustomize limitation wording
  conditional on manifest inputs that actually look like Helm or Kustomize.

## 1.1.0 - 2026-06-23

### Added

- Added provider-neutral `runtime.provider: kubernetes` ChamberConfig support
  with explicit context, namespace, cleanup, Prometheus, traffic access, and
  image replacement metadata in `plan.json`.
- Added generic Kubernetes kubectl preflight checks with explicit context
  safety validation, chamber-owned namespace validation, RBAC probes, server
  metadata capture, redaction, and reportable evidence.
- Added `ampule-chamber assess --config chamber.yaml --mode kubernetes` for
  live assessments against non-production Kubernetes contexts reachable through
  `kubectl`.
- Added a generic Kubernetes assessment guide covering prerequisites,
  optional AKS/EKS/GKE kubeconfig examples, safety model, minimal config,
  command flow, report regeneration, and troubleshooting.

## 1.0.0 - 2026-06-18

### Added

- Promoted Ampule Chamber to the production-ready `1.0.0` release line.
- Added the guided workflow CLI: `init`, `onboard`, `plan`, `assess`, `report`,
  and the existing live `run` command.
- Added standard `.chamber/runs/<run-id>/` artifacts with config, plan,
  metadata, adapted manifests, evidence, findings, agent outputs, and reports.
- Added six bounded agent roles for onboarding, scenario planning, run
  supervision, traffic and chaos recommendation, evidence analysis, and report
  writing.
- Added public MkDocs documentation and GitHub Actions workflows for CI, docs,
  and tag releases.

### Changed

- Updated package metadata, documentation, and release validation for a stable
  production release.
- Expanded `make check` to validate release metadata and build the MkDocs site.

### Security

- Runtime config now rejects secret-like keys such as `API_KEY`, `PASSWORD`,
  `TOKEN`, `SECRET`, or `KEY` unless they are modeled through redacted secret
  environment configuration.
- Live agent mode requires `OPENAI_API_KEY` and validates all cited evidence
  before persisting agent output.
