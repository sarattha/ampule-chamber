# Changelog

All notable changes to Ampule Chamber are documented in this file.

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
