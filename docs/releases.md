# Releases

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
