# ExecPlan: Phase 06 Live Manual Chamber MVP

## Description

Create the first live, manual end-to-end MVP workflow for Ampule Chamber. This
phase should convert the completed fixture-backed modules into a runnable local
Kubernetes chamber that validates the sample service, captures real evidence,
and emits an evidence-backed markdown report.

## Task Checklist

- [ ] Review `README.md`, `docs/internal/PROJECT_DESIGN.md`, and phase 01-05
      outcomes.
- [ ] Define the live run contract and command-line interface.
- [ ] Add Kubernetes context safety checks and production-context denylist
      behavior.
- [ ] Implement local `kind` chamber run orchestration for one scenario.
- [ ] Deploy the sample service into an isolated chamber namespace.
- [ ] Run readiness and baseline health checks against the Kubernetes service.
- [ ] Run k6 traffic through deterministic local port-forwarding.
- [ ] Collect Kubernetes pod status, events, and logs from the live namespace.
- [ ] Query Prometheus metrics when configured and record actionable diagnostics
      when unavailable.
- [ ] Convert collected live evidence into analysis findings.
- [ ] Render a markdown report from live run metadata, timeline, evidence, and
      findings.
- [ ] Add cleanup behavior with a retain-on-failure or retain-for-debug option.
- [ ] Add unit tests for runner planning, safety checks, report wiring, and
      cleanup decisions.
- [ ] Add an optional live smoke test document or script for maintainers with
      `kind`, `kubectl`, and `k6` installed.
- [ ] Record live acceptance evidence and known limitations in this plan.

## Evaluation Metrics

- A single command can run the sample service through the local chamber path.
- The runner refuses unsafe Kubernetes contexts before applying resources.
- Namespace naming, labels, timeline IDs, and report metadata are traceable to a
  chamber run id.
- The deployed service reaches readiness through Kubernetes before traffic runs.
- k6 results include request count, HTTP failure rate, p95 latency, and exit
  status.
- Kubernetes evidence includes pod status, events, logs, collection timestamps,
  resource identifiers, and run identifiers.
- Missing Prometheus produces an explicit diagnostic evidence artifact.
- The generated report is based on live run metadata and evidence, not only a
  static fixture.
- Cleanup removes chamber-owned resources in the normal success path.
- `make check` passes after implementation.

## Acceptance Criteria

- `uv run ampule-chamber run --scenario scenarios/baseline-health.yaml
  --output docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md`
  or an equivalent documented command performs a local chamber run against the
  sample service.
- The command creates an isolated namespace, deploys the target, waits for
  readiness, runs k6 traffic, collects Kubernetes evidence, analyzes findings,
  renders a markdown report, and cleans up.
- The live report includes service metadata, scenario metadata, run id,
  namespace, timeline, readiness score, findings or no-finding summary,
  evidence references, diagnostics, cleanup status, limitations, and retest
  guidance.
- At least one live artifact captures command output or structured run metadata
  proving that the report was generated from a live Kubernetes run.
- The phase documents any gap that prevents full design-MVP parity instead of
  implying completion.

## Progress

- [ ] Phase directory created.

## Surprises And Discoveries

- None yet.

## Decision Log

- Chose live local `kind` as the phase boundary because the project design
  allows AKS or local Kubernetes for the first MVP, and local Kubernetes keeps
  acceptance reproducible for agents and maintainers.
- Chose a manual runner before agent autonomy because the design roadmap places
  the manual chamber MVP before agent-assisted analysis.
- Chose the repository sample service as the first live target so phase 06 can
  validate existing contracts without requiring an external application.

## Outcomes And Retrospective

- Pending.
