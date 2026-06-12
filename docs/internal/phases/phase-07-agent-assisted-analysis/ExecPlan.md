# ExecPlan: Phase 07 Agent-Assisted Analysis

## Description

Build the first agent-assisted reliability analysis layer for Ampule Chamber.
This phase introduces bounded agent roles that inspect service inputs and live
run artifacts, then produce test plans, evidence analysis briefs, root-cause
hypotheses, and improved report content.

## Task Checklist

- [ ] Review `docs/internal/PROJECT_DESIGN.md`, phase 06 outputs, and existing
      `chamber/` contracts.
- [ ] Define planner, observability analyst, and report writer agent contracts.
- [ ] Add `agents/` role files with responsibilities, inputs, outputs, safety
      rules, and evidence rules.
- [ ] Define machine-readable DTOs or schemas for agent inputs and outputs.
- [ ] Implement fixture-backed planner behavior for scenario and service
      contract inspection.
- [ ] Implement fixture-backed observability analysis behavior over phase 06
      run metadata, evidence, and findings.
- [ ] Implement report-writer behavior that turns findings and analysis briefs
      into report sections.
- [ ] Add tests for evidence citation, missing-data handling, and deterministic
      agent output shape.
- [ ] Add sample agent briefs in `artifacts/`.
- [ ] Document how agent outputs are reviewed before being treated as report
      evidence or hypotheses.
- [ ] Record acceptance evidence and remaining autonomy gaps.

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

- [ ] Phase directory created.

## Surprises And Discoveries

- None yet.

## Decision Log

- Chose planner, observability analyst, and report writer as the first agent
  roles because the project design lists them in the agent-assisted roadmap
  before broader fault injection work.
- Chose bounded analysis over autonomous execution for this phase so the live
  safety model remains owned by phase 06.

## Outcomes And Retrospective

- Pending.
