# ExecPlan: Phase 11 Guided Assessment Workflow

## Description

Build the staged command workflow requested in GitHub issue #9. The workflow
introduces `chamber.yaml`, a standard run directory, and direct report rendering
from run artifacts.

## Task Checklist

- [x] Add the phase directory and planning files.
- [x] Add canonical `ampule-chamber` command dispatch for `init`, `onboard`,
      `plan`, `assess`, `report`, and existing `run`.
- [x] Add `chamber.yaml` load, validation, save, and inference helpers.
- [x] Convert `chamber.yaml` into the existing Phase 10 onboarding planner.
- [x] Persist `.chamber/runs/<run-id>/` with config, plan, metadata, adapted
      manifests, evidence, findings, agent output, and report paths.
- [x] Add report rendering from a run directory.
- [x] Add tests for config validation, CLI commands, run directory shape, and
      report rendering.

## Evaluation Metrics

- The explicit command sequence can create `chamber.yaml`, `plan.json`,
  adapted manifests, and `report.md`.
- Secret-like values are represented through redacted config summaries.
- The existing scenario-based `run` subcommand remains delegated to the live
  runner.
- `make check` passes.

## Acceptance Criteria

- A supported local fixture service can complete the staged workflow without
  manually assembled report fixtures.
- `report --run .chamber/runs/<run-id>` regenerates `report.md`.
- Generated assumptions are visible in `chamber.yaml` and reports.

## Progress

- [x] Implemented in `chamber.workflow`.
- [x] Canonical console script now points to `chamber.workflow:main`.
- [x] Added phase 11 tests in `tests/test_phase11_12_13_workflow.py`.

## Decision Log

- Chose to reuse Phase 10 `OnboardingSpec` and `build_onboarding_plan` as the
  single planner contract.
- Chose local deterministic assessment for the new guided workflow; live
  scenario execution remains available through `ampule-chamber run`.
