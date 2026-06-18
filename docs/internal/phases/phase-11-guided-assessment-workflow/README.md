# Phase 11: Guided Assessment Workflow

Add the first user-facing automatic workflow on top of the Phase 10
real-service onboarding contract. This phase turns the internal planner into a
reviewable staged CLI experience with a stable `chamber.yaml` and predictable
run directory.

The core question for this phase is:

> Can a service owner initialize, onboard, plan, assess, and report without
> manually assembling internal fixtures or phase artifacts?

## What Needs To Be Done

- Expose `init`, `onboard`, `plan`, `assess`, `report`, and existing `run`
  through the canonical `ampule-chamber` command.
- Keep `ampule-chamber-report` available for existing fixture-based report
  rendering.
- Define and validate a user-facing `chamber.yaml` contract for service
  identity, manifests, images, workload roles, traffic, dependencies,
  redacted runtime config, and agent mode.
- Convert `chamber.yaml` into the Phase 10 `OnboardingSpec` instead of adding
  a parallel planning model.
- Write every staged workflow into `.chamber/runs/<run-id>/` with config, plan,
  metadata, adapted manifests, evidence, agent output, findings, and report
  locations.
- Let `report --run` render directly from a run directory.

## Agent Notes

- Do not mutate the target service repository during onboarding.
- Treat generated config values as assumptions that must remain visible.
- Keep local guided assessment deterministic unless a later phase explicitly
  opts into live Kubernetes execution.
