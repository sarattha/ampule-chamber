# ExecPlan: Phase 13 One-Command Assessment

## Description

Implement the one-command shortcut and resumable report path requested in
GitHub issue #9.

## Task Checklist

- [x] Add `ampule-chamber assess --repo`.
- [x] Add `ampule-chamber assess --config --mode local`.
- [x] Add `ampule-chamber assess --resume`.
- [x] Preserve standard run-directory artifact names.
- [x] Record local cleanup status.
- [x] Refuse unsafe Kubernetes contexts when one is detectable.
- [x] Add one-command and resume tests.

## Evaluation Metrics

- One-command assessment writes `chamber.yaml`, `plan.json`,
  `run-metadata.json`, `adapted-manifests/`, `evidence/`, `findings.json`,
  `agent/`, and `report.md`.
- Resume regenerates `report.md` without requiring live Kubernetes execution.
- The report includes reproduction commands for resume and report rendering.
- `make check` passes.

## Acceptance Criteria

- `uv run ampule-chamber assess --repo ../target-service` produces the same
  core artifacts as the staged workflow.
- `uv run ampule-chamber assess --resume .chamber/runs/<run-id>` refreshes the
  report from existing artifacts.
- Unsafe Kubernetes context names containing production fragments are rejected.

## Progress

- [x] Implemented one-command and resume paths in `chamber.workflow`.
- [x] Added phase 13 tests in `tests/test_phase11_12_13_workflow.py`.
- [x] GitHub issue #13: extended `ChamberConfig` validation and `plan.json`
      metadata for provider-neutral Kubernetes runtime settings, including
      context, namespace intent, traffic access, cleanup, Prometheus URL, and
      explicit image replacement intent.
- [x] GitHub issue #12: added reusable generic Kubernetes kubectl preflight
      checks with explicit context validation, chamber-owned namespace
      validation, RBAC probes, server metadata capture, redaction, and
      JSON-safe evidence conversion.
- [x] GitHub issue #11: added config-driven
      `ampule-chamber assess --config chamber.yaml --mode kubernetes` support
      that plans a run directory, runs generic preflight before apply, deploys
      adapted manifests, waits for readiness, executes configured traffic,
      records Kubernetes command evidence, cleans up chamber-owned resources,
      and renders the standard report.
- [x] GitHub issue #14: added a generic Kubernetes assessment operator guide
      with mode comparison, prerequisites, optional AKS/EKS/GKE kubeconfig
      examples, safety model, minimal config, command flow, report
      regeneration, and troubleshooting.

## Acceptance Evidence

- `uv run python -m unittest tests/test_phase11_12_13_workflow.py` passed 27
  tests after the #13 config-contract change.
- `uv run python -m unittest tests/test_kubernetes_preflight.py` passed 4
  tests for generic Kubernetes preflight behavior.
- `uv run python -m unittest tests/test_phase11_12_13_workflow.py` passed 29
  tests after the #11 Kubernetes assessment path was added.
- `uv run python -m unittest tests/test_kubernetes_preflight.py` passed again
  after #11 integration.
- `uv run mkdocs build --strict` passed after the #14 documentation update.
- Real kind verification on 2026-06-23 passed after two bounded fixes. Evidence
  is recorded in
  `artifacts/real-kind-verification-20260623.md`; the final live run wrote
  `.chamber/runs/chamber-sample-service-20260623162533/report.md`, used
  `kind-ampule-chamber`, created namespace `chamber-kind-sample-3e07568c`,
  passed k6 with 1/1 checks and 0 failed HTTP requests, and cleaned up the
  namespace.
- After GitHub Actions Semgrep flagged the committed sample verification
  manifest for missing Kubernetes security context, hardened the manifest and
  reran live kind verification. The hardened run wrote
  `.chamber/runs/chamber-sample-service-20260623163054/report.md`, passed k6,
  and cleaned up namespace `chamber-kind-sample-2f6d3359`.
- 2026-06-25 release metadata update: bumped project metadata to `1.2.0`,
  refreshed `uv.lock`, added changelog/release notes for multi-journey
  Kubernetes traffic, agent exclusion, Prometheus memory evidence, translation
  memory scenarios, and report limitation cleanup, and updated public docs for
  the new controls.
- 2026-06-25 security CI fix: Semgrep flagged the Prometheus memory evidence
  query path for dynamic `urllib` use. Added explicit HTTP(S) URL construction
  and rejection of non-HTTP schemes before the audited request, added focused
  regression tests, confirmed `semgrep scan --config auto chamber/workflow.py`
  reports 0 findings, and reran `make check` successfully with 129 tests.

## Decision Log

- Chose not to run live Kubernetes from the new shortcut yet; the established
  live path remains `ampule-chamber run` until config-driven live execution has
  separate acceptance evidence.
- Chose to record only operator-declared image replacements in runtime plan
  metadata. Inferred replacements still adapt manifests, but local plan
  metadata does not expose original production image names.
- Chose a reviewed sample-service Kubernetes config artifact for live kind
  verification because the bundled sample service is Docker-first and does not
  carry source Kubernetes manifests.
