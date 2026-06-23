# Changelog

All notable changes to Ampule Chamber are documented in this file.

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
