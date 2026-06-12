# ExecPlan: Phase 03 Traffic And Chaos

## Description

Build the traffic and chaos layer that drives realistic request pressure and
controlled degradation against a deployed service. This phase should produce
repeatable experiment timelines that later analysis can correlate with
telemetry.

## Task Checklist

- [ ] Choose the MVP load runner and define its adapter interface.
- [ ] Map scenario traffic profiles to runner configuration.
- [ ] Implement normal, peak, burst, and soak traffic execution.
- [ ] Capture request counts, latency summaries, error rates, and runner exit
      status.
- [ ] Implement one simple dependency failure injection path.
- [ ] Define extensible fault injection interfaces for later pod, network, DNS,
      CPU, and memory faults.
- [ ] Coordinate traffic and fault windows in the orchestrator.
- [ ] Add tests or fixtures for traffic plan generation and fault timeline
      validation.

## Evaluation Metrics

- Traffic adapter can generate or execute a runner plan from a scenario.
- Experiment timeline records traffic start, traffic stop, fault start, fault
  removal, and recovery window.
- MVP dependency failure is reversible and scoped to the chamber namespace or
  controlled dependency.
- Load results include p95 or p99 latency, error rate, and request count where
  the runner supports them.
- Tests cover valid and invalid traffic/fault scenario definitions.

## Acceptance Criteria

- A sample scenario can run baseline traffic and one dependency failure path, or
  produce a complete dry-run plan with all commands and timings.
- Traffic and fault outputs are structured enough for analysis and report
  generation.
- Fault cleanup is explicit and documented.

## Progress

- [ ] Not started.

## Surprises And Discoveries

- None yet.

## Decision Log

- None yet.

## Outcomes And Retrospective

- Pending.

