# ExecPlan: Phase 07 Agent-Assisted Analysis

## Description

Build the first agent-assisted reliability analysis layer for Ampule Chamber.
This phase introduces bounded agent roles that inspect service inputs and live
run artifacts, then produce test plans, evidence analysis briefs, root-cause
hypotheses, and improved report content.

## Task Checklist

- [x] Review `docs/internal/PROJECT_DESIGN.md`, phase 06 outputs, and existing
      `chamber/` contracts.
- [x] Define planner, observability analyst, and report writer agent contracts.
- [x] Add `agents/` role files with responsibilities, inputs, outputs, safety
      rules, and evidence rules.
- [x] Define machine-readable DTOs or schemas for agent inputs and outputs.
- [x] Implement fixture-backed planner behavior for scenario and service
      contract inspection.
- [x] Implement fixture-backed observability analysis behavior over phase 06
      run metadata, evidence, and findings.
- [x] Implement report-writer behavior that turns findings and analysis briefs
      into report sections.
- [x] Add tests for evidence citation, missing-data handling, and deterministic
      agent output shape.
- [x] Add sample agent briefs in `artifacts/`.
- [x] Document how agent outputs are reviewed before being treated as report
      evidence or hypotheses.
- [x] Record acceptance evidence and remaining autonomy gaps.

## Evaluation Metrics

- Agent contracts define explicit allowed inputs, outputs, and forbidden
  behaviors.
- Planner output references scenario/service inputs and produces test goals,
  risks, expected signals, and success/failure criteria.
- Observability analyst output separates observed facts, missing evidence,
  inferred hypotheses, confidence, and recommended follow-up checks.
- Report writer output uses only supplied evidence references and findings.
- Tests fail when an agent output cites evidence that is not present in the
  supplied artifact set.
- Agent outputs are deterministic for fixture-backed inputs.
- `make check` passes after implementation.

## Acceptance Criteria

- Given a phase 06 live run artifact set or representative fixture, the agent
  layer can produce:
  - a chamber test plan,
  - an evidence analysis brief,
  - root-cause hypotheses with confidence,
  - report-ready summary and recommendation sections.
- Agent outputs distinguish observed evidence from inference.
- Agent outputs do not imply live Prometheus, trace, or log evidence was present
  when the artifact set only contains diagnostics or missing-backend notices.
- The generated or revised report remains evidence-backed and deterministic.

## Progress

- [x] Phase directory created.
- [x] Added `chamber/agents/` dataclass contracts for agent context, test
      plans, analysis briefs, root-cause hypotheses, report narratives, and
      evidence citations.
- [x] Added `OpenAIAgentsSdkRunner` as a lazy OpenAI Agents SDK adapter with
      `OPENAI_API_KEY` required only for live agent calls.
- [x] Added planner, observability analyst, and report writer role files under
      `agents/`.
- [x] Added deterministic offline outputs and evidence-citation validation so
      repository checks do not require OpenAI credentials.
- [x] Added phase 07 tests in `tests/test_phase07_agents.py`.
- [x] Added `artifacts/sample-agent-briefs.md`.

## Surprises And Discoveries

- The OpenAI Agents SDK can be kept behind a small lazy adapter. This avoids
  importing SDK runtime modules or requiring credentials during normal unit
  tests.
- The existing report renderer needed optional sections so agent narratives can
  be attached without changing older fixtures.

## Decision Log

- Chose planner, observability analyst, and report writer as the first agent
  roles because the project design lists them in the agent-assisted roadmap
  before broader fault injection work.
- Chose bounded analysis over autonomous execution for this phase so the live
  safety model remains owned by phase 06.
- Chose `openai-agents` as the live agent runtime dependency and bumped the
  project version to `0.9.0` for the phase 07-09 implementation slice.
- Chose dataclass output contracts because the repository already uses
  dataclasses and the Agents SDK supports structured output types.

## Outcomes And Retrospective

- Agent role contracts, deterministic offline behavior, SDK runtime boundary,
  and evidence-citation validation are implemented.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase07_agents.py` passes.
  - Focused combined phase 07-09 tests pass as part of the implementation run.
