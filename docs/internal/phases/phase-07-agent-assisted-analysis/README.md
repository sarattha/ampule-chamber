# Phase 07: Agent-Assisted Analysis

Add the first agent layer on top of the live manual chamber runner. This phase
should let Ampule Chamber inspect service inputs and live run evidence, produce
bounded test plans, explain observed behavior, and strengthen report narratives
without inventing unsupported conclusions.

The goal is not autonomous chaos execution. Agents in this phase should reason
over scenarios, manifests, service contracts, chamber metadata, telemetry
artifacts, findings, and reports produced by earlier phases. Execution remains
bounded by the phase 06 manual runner and safety guardrails.

## What Needs To Be Done

- Define agent role contracts for planner, observability analyst, and report
  writer agents.
- Add prompt or policy files under `agents/` for the first supported roles.
- Define structured agent inputs and outputs that can be validated in tests.
- Let the planner agent inspect scenario files, sample service contracts, and
  deployment metadata to produce a chamber test plan.
- Let the observability analyst agent summarize live evidence and distinguish
  observed facts from inferred hypotheses.
- Let the report writer agent improve the reliability report narrative using
  only supplied evidence and analysis.
- Add tests or fixtures that prove agents do not cite unavailable evidence.
- Document how agent outputs are reviewed and fed back into reports.
- Record agent-generated sample briefs and limitations in `artifacts/`.

## Agent Notes

- Keep agents evidence-bound. If a signal is absent, record it as missing or
  unavailable instead of implying it was observed.
- Prefer structured JSON or markdown sections with stable headings so reports
  and tests can consume outputs deterministically.
- Do not allow agents to bypass phase 06 safety checks or run arbitrary
  Kubernetes commands.
- Start with the smallest useful role set: planner, observability analyst, and
  report writer.
