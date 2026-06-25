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
- [x] Route all six roles through the OpenAI Agents SDK in live mode.
- [x] Include validated agent outputs in generated reports.
- [x] Compare every role against GitHub issue #9 capabilities and record the
      validation matrix.

## Evaluation Metrics

- Offline mode requires no network or API key.
- Unsupported evidence citations are rejected.
- Agent mode `off` suppresses agent output.
- Live mode reports a clear missing-key error.
- Live mode writes six structured role outputs from `gpt-5.4-mini`.
- Reviewed configs can skip selected roles with `agents.exclude`, for example
  `onboarding-agent` when there is no local source repository to inspect.
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
- [x] Updated live mode so onboarding, scenario planner, run supervisor,
      traffic and chaos, evidence analyst, and report writer all call the
      OpenAI Agents SDK instead of only the report writer.
- [x] Ran live validation with `OPENAI_API_KEY` supplied through process
      environment only; no key or raw model output was written to tracked
      files. The live run produced all six expected files:
      `onboarding-agent.json`, `scenario-planner-agent.json`,
      `run-supervisor-agent.json`, `traffic-chaos-agent.json`,
      `evidence-analyst-agent.json`, and `report-writer-agent.json`.
- [x] Added role-level agent exclusion in config and CLI overrides. This keeps
      full-agent mode as the default while allowing already-deployed
      Kubernetes service assessments to skip `onboarding-agent`.

## Decision Log

- Chose deterministic offline mode as the default generated config value.
- Chose the existing OpenAI Agents SDK adapter for every live Phase 12 role so
  `agents.mode: live` exercises the full agent-assisted workflow, not only
  final report writing.
- Chose an explicit exclusion list instead of changing full-agent defaults so
  historical all-six-role acceptance evidence remains valid.
