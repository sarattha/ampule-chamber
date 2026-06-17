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

## Decision Log

- Chose not to run live Kubernetes from the new shortcut yet; the established
  live path remains `ampule-chamber run` until config-driven live execution has
  separate acceptance evidence.
