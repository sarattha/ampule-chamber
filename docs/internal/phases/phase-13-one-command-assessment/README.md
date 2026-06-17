# Phase 13: One-Command Assessment

Add the high-level shortcut over the staged workflow so a user can assess a
supported local service repository with one command.

The core question for this phase is:

> Can `ampule-chamber assess --repo ../target-service` produce the same core
> artifacts as the explicit workflow while preserving resumability and safety?

## What Needs To Be Done

- Implement `assess --repo` as onboarding, planning, local assessment, agent
  output, findings, and report generation.
- Implement `assess --config --mode local` for reviewed configs.
- Implement `assess --resume .chamber/runs/<run-id>` to regenerate reports
  without rerunning Kubernetes work.
- Keep generated assumptions visible in config, plan, agent output, and report.
- Refuse obviously unsafe production Kubernetes contexts.
- Record cleanup status in metadata and report output.

## Agent Notes

- `assess --repo` must not silently opt into external dependencies.
- If inference cannot produce a supported config, fail with an actionable
  message and preserve any partial artifacts inside the run directory.
