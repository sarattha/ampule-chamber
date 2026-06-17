# Phase 12: Full Agent Assessment Pipeline

Extend Ampule Chamber's evidence-bound agent layer from three report-adjacent
roles into the six bounded roles requested for the automatic workflow.

The core question for this phase is:

> Can agents assist onboarding, planning, supervision, traffic and chaos
> selection, evidence analysis, and report writing without citing unavailable
> evidence or bypassing chamber safety constraints?

## What Needs To Be Done

- Add structured outputs for onboarding, scenario planner, run supervisor,
  traffic and chaos, evidence analyst, and report writer roles.
- Provide deterministic offline outputs for CI and repeatable local workflows.
- Persist agent output under `.chamber/runs/<run-id>/agent/`.
- Validate every evidence citation before agent output affects reports.
- Support `agents.mode` values `off`, `offline`, and `live`.
- Require `OPENAI_API_KEY` before live agent execution.

## Agent Notes

- Agents may explain blockers and recommend bounded tests, but must not run
  arbitrary shell or Kubernetes commands.
- Report writer output is advisory unless evidence citations validate.
