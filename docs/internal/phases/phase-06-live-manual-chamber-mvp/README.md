# Phase 06: Live Manual Chamber MVP

Build the first real end-to-end chamber workflow from the existing phase 01-05
contracts. This phase closes the gap between fixture-backed planning/reporting
and a runnable local Kubernetes validation path for one service.

The goal is a manual MVP runner that deploys a target service into an isolated
local Kubernetes chamber, runs baseline traffic, collects real runtime evidence,
detects reliability findings, renders a markdown report, and cleans up.

This phase should prioritize a narrow live path over generality. The accepted
target is the repository sample service running in `kind` with k6 traffic and
Kubernetes evidence. AKS, multi-service orchestration, and agent autonomy remain
future work.

## What Needs To Be Done

- Add a chamber run command that connects environment, load, observability,
  analysis, and report modules.
- Verify Kubernetes context safety before live actions.
- Create an isolated namespace for the run and deploy the sample service.
- Run readiness and baseline health checks against the deployed service.
- Run k6 traffic through a local port-forward.
- Collect Kubernetes pod status, events, and logs from the chamber namespace.
- Collect Prometheus metrics when `PROMETHEUS_URL` is configured, and degrade
  clearly when it is not.
- Detect findings from real collected evidence.
- Generate a markdown reliability report from the live run.
- Clean up live Kubernetes resources by default, with an option to retain them
  for debugging.
- Record live run evidence, command output, limitations, and acceptance notes in
  `artifacts/`.

## Agent Notes

- Keep the first runner conservative: local `kind`, one target service, one
  scenario, and explicit safety checks.
- Never run destructive actions against a production-like context. Deny unsafe
  Kubernetes contexts before creating resources.
- Treat missing Prometheus as a diagnostic limitation, not as successful metric
  collection.
- Preserve phase 01-05 contracts where possible instead of inventing parallel
  data shapes.
- Store generated reports, live command transcripts, run metadata, and cleanup
  notes in `artifacts/`.
