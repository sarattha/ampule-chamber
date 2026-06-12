# ExecPlan: Phase 01 Foundation

## Description

Build the MVP foundation for Ampule Chamber by defining the run lifecycle,
scenario schema, repository conventions, and first validation path. This phase
converts the product design into concrete interfaces that later phases can
implement.

## Task Checklist

- [ ] Review `README.md` and `docs/internal/PROJECT_DESIGN.md`.
- [ ] Define the chamber run lifecycle states.
- [ ] Define MVP scenario schema fields and validation rules.
- [ ] Add example scenarios for baseline health, OOM stress, dependency
      failure, retry storm, and soak testing.
- [ ] Choose initial implementation stack and dependency conventions.
- [ ] Establish package/module ownership for orchestrator, environment, load,
      chaos, observability, analysis, and reporting.
- [ ] Add schema parsing or validation tests.
- [ ] Document local development commands.
- [ ] Record unresolved design decisions in this plan.

## Evaluation Metrics

- Scenario schema covers at least baseline, traffic, observability, failure
  conditions, and success conditions.
- At least three scenario examples validate successfully.
- Run lifecycle states are documented with allowed transitions.
- Package ownership is documented clearly enough for another agent to add code
  without asking for architecture clarification.
- Validation can be run from a documented command.

## Acceptance Criteria

- A new agent can read this phase folder and implement a scenario parser without
  needing hidden context.
- Example scenarios are present and structurally valid.
- The chosen development/test commands are documented.
- Open questions are recorded in the decision log instead of left implicit.

## Progress

- [ ] Not started.

## Surprises And Discoveries

- None yet.

## Decision Log

- None yet.

## Outcomes And Retrospective

- Pending.

