# Ampule Chamber Phases

This directory contains the implementation roadmap for Ampule Chamber. Each
phase is intentionally self-contained so an agent can read the phase folder,
understand what to build, execute the work, and leave evidence for the next
agent.

## How To Use These Phases

1. Read the repository `AGENTS.md`.
2. Choose the active phase.
3. Read the phase `README.md` for context and boundaries.
4. Follow and update the phase `ExecPlan.md`.
5. Store generated phase artifacts in the phase `artifacts/` directory.
6. Record acceptance evidence before marking the phase complete.

## Phase Order

1. `phase-01-foundation`
2. `phase-02-environment-orchestration`
3. `phase-03-traffic-and-chaos`
4. `phase-04-observability-analysis`
5. `phase-05-reporting-mvp-hardening`
6. `phase-06-live-manual-chamber-mvp`
7. `phase-07-agent-assisted-analysis`
8. `phase-08-expanded-fault-injection-recovery`
9. `phase-09-multi-service-chamber`

Phases may overlap during exploration, but acceptance criteria should be closed
in order so later work has stable contracts to build on.
