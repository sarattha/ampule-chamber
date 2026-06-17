# ExecPlan: Phase 12 Full Agent Assessment Pipeline

## Description

Implement the six-agent assessment pipeline requested in GitHub issue #9 and
wire deterministic offline outputs into the guided workflow.

## Task Checklist

- [x] Add dataclass contracts for all six bounded roles.
- [x] Add deterministic offline implementations for every role.
- [x] Persist role outputs in each run directory.
- [x] Validate role outputs with the existing evidence-bound validator.
- [x] Fail live mode before execution when `OPENAI_API_KEY` is missing.
- [x] Include validated agent outputs in generated reports.

## Evaluation Metrics

- Offline mode requires no network or API key.
- Unsupported evidence citations are rejected.
- Agent mode `off` suppresses agent output.
- Live mode reports a clear missing-key error.
- `make check` passes.

## Acceptance Criteria

- All six role files are written under `.chamber/runs/<run-id>/agent/` when
  offline mode is enabled.
- Reports include validated agent sections.
- Citation validation rejects references outside the supplied evidence set.

## Progress

- [x] Extended `chamber.agents.contracts`.
- [x] Integrated offline and live-mode gates in `chamber.workflow`.
- [x] Added phase 12 tests in `tests/test_phase11_12_13_workflow.py`.

## Decision Log

- Chose deterministic offline mode as the default generated config value.
- Chose the existing OpenAI Agents SDK adapter as the live report-writer
  boundary and kept the other roles deterministic until live prompts are
  explicitly designed.
