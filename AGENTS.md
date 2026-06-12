# Agent Guide

This guide helps agents work in the Ampule Chamber repository. The project is
currently in design and MVP planning, so agents should preserve the planning
structure, keep implementation work phase-aligned, and record evidence as they
build.

## Project Goal

Ampule Chamber is an agent-driven reliability testing chamber for Kubernetes
services before production. It should deploy a target service into an isolated
Kubernetes environment, apply realistic load, inject controlled failures,
observe runtime behavior, and produce evidence-backed reliability reports.

The core question every phase should serve is:

> What can make this service fail in a real production-like environment, and
> what evidence supports that conclusion?

## Mandatory Planning Rules

- Use the phase directories under `docs/internal/phases/` as the source of truth
  for roadmap execution.
- Before implementing a phase, read that phase's `README.md` and `ExecPlan.md`.
- Keep each `ExecPlan.md` updated as work proceeds. Record completed tasks,
  decisions, surprises, evaluation results, and acceptance evidence.
- Put generated implementation artifacts for a phase in that phase's
  `artifacts/` directory unless the artifact clearly belongs in source,
  scenarios, reports, or tests.
- Do not skip evaluation metrics or acceptance criteria. If a criterion cannot
  be met yet, record the blocker in the active phase plan.

## Relayna Gateway Skill Guidance

When work needs patterns, operating assumptions, or compatibility guidance from
the local Relayna Gateway repository, use the `karpathy` skill from:

```text
/Users/jobz/Works/relayna-gateway/.codex/skills/karpathy
```

If a task or prompt refers to this as `karparthy`, treat that as the same local
skill unless a separate `karparthy` directory is later added. If the skill is
not present in the local Relayna Gateway checkout, note that explicitly in the
handoff and continue with the closest available Relayna Gateway repository
guidance. Do not modify the Relayna Gateway repository unless the user asks for
it.

## Phase Map

- `docs/internal/phases/phase-01-foundation/`: project contracts, scenario
  schema, chamber run model, and local development foundation.
- `docs/internal/phases/phase-02-environment-orchestration/`: isolated
  Kubernetes namespace or cluster provisioning and target service deployment.
- `docs/internal/phases/phase-03-traffic-and-chaos/`: load generation, traffic
  profiles, and controlled fault injection.
- `docs/internal/phases/phase-04-observability-analysis/`: runtime evidence
  collection, signal detection, and root-cause analysis.
- `docs/internal/phases/phase-05-reporting-mvp-hardening/`: markdown reports,
  MVP acceptance, fixtures, documentation, and release hardening.

## Repository Areas

- `chamber/orchestrator/`: run coordination and phase execution.
- `chamber/environment/`: Kubernetes namespace, deployment, dependency, and
  readiness operations.
- `chamber/load/`: k6, Locust, or custom traffic execution.
- `chamber/chaos/`: pod, dependency, network, CPU, and memory fault injection.
- `chamber/observability/`: Kubernetes events, logs, metrics, and trace
  collection.
- `chamber/analysis/`: signal detection, correlation, and root-cause
  hypotheses.
- `chamber/report/`: reliability report generation.
- `agents/`: agent role prompts, tools, and coordination helpers.
- `scenarios/`: chamber scenario definitions.
- `examples/`: sample services and manifests.
- `tests/`: automated tests.

## Working Practices

- Preserve user work and unrelated local changes.
- Keep changes scoped to the active phase unless the user asks for a broader
  refactor.
- Prefer structured scenario definitions and typed run metadata over ad hoc
  strings.
- Add tests when implementation changes behavior.
- Use real command output, Kubernetes events, logs, metrics, or fixture data as
  evidence for reports and acceptance notes.
- For docs-only changes, a structural check such as `find docs/internal/phases
  -maxdepth 3 -type f | sort` is sufficient unless the user requests more.
