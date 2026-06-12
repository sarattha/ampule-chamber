# ExecPlan: Phase 03 Traffic And Chaos

## Description

Build the traffic and chaos layer that drives realistic request pressure and
controlled degradation against a deployed service. This phase should produce
repeatable experiment timelines that later analysis can correlate with
telemetry.

## Task Checklist

- [x] Choose the MVP load runner and define its adapter interface.
- [x] Map scenario traffic profiles to runner configuration.
- [x] Implement normal, peak, burst, and soak traffic execution.
- [x] Capture request counts, latency summaries, error rates, and runner exit
      status.
- [x] Implement one simple dependency failure injection path.
- [x] Define extensible fault injection interfaces for later pod, network, DNS,
      CPU, and memory faults.
- [x] Coordinate traffic and fault windows in the orchestrator.
- [x] Add tests or fixtures for traffic plan generation and fault timeline
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

- [x] Added `chamber/load/` with a k6 adapter that generates deterministic
      staged VU plans, target URLs, k6 scripts, runner commands, summary paths,
      expected result fields, and live execution preflight behavior.
- [x] Added `chamber/chaos/` with a reversible Kubernetes
      `dependency_unavailable` plan implemented as a chamber-scoped
      `NetworkPolicy` apply/delete pair.
- [x] Added `chamber/orchestrator/` timeline contracts that combine traffic
      start/stop, fault start/removal, and recovery validation events.
- [x] Added phase 03 tests for k6 plan generation, invalid traffic definitions,
      missing k6 live execution, dependency fault planning, reserved fault
      reporting, and timeline ordering.
- [x] Added `artifacts/dependency-failure-dry-run.md` with a representative
      dependency-failure traffic/fault/timeline plan.
- [x] Installed k6 locally and captured real Docker-backed k6 smoke and
      dependency-fault evidence in `artifacts/live-k6-results.md`.

## Surprises And Discoveries

- `kubectl` and `kind` were already installed locally. `k6` was not initially on
  `PATH`, so it was installed with Homebrew before live traffic runs.
- Homebrew reported untrusted taps for `dart-lang/dart` and
  `romkatv/powerlevel10k` during k6 installation, but the `k6` formula
  installed successfully from Homebrew core.
- Phase 02 deploys only the target service, not dependency workloads. The MVP
  dependency-unavailable fault is therefore represented as an egress-denying
  `NetworkPolicy` scoped to chamber run labels, which is reversible and does
  not require dependency manifests.

## Decision Log

- Chose `k6` as the only implemented phase 03 traffic runner because all
  current scenarios use `traffic.tool: k6`.
- Kept scenario YAML unchanged and mapped existing `traffic.entrypoint`,
  `traffic.stages`, `faults[*].startAfter`, and `faults[*].duration` fields
  into internal typed contracts.
- Chose dry-run artifact generation and live-local preflight as the execution
  boundary for this phase. Live k6 traffic can run after k6 is installed, but
  acceptance does not require it.
- Marked non-MVP fault types such as memory pressure, dependency errors, and
  rate limits as reserved for later phases when passed to fault planning.

## Outcomes And Retrospective

- Traffic plans now expose staged VU configuration, target URL, generated k6
  script text, runner command, summary path, total duration, and expected
  result metrics including request count, error rate, p95, and p99 latency.
- The dependency-failure scenario produces a reversible fault plan with
  explicit inject/remove commands and timeline events at 120 and 180 seconds.
- Experiment timelines are structured enough for phase 04 observability and
  phase 05 reporting to correlate runtime evidence against traffic and fault
  windows.
- Live k6 evidence against the Docker sample service confirms real request
  execution:
  - Health smoke: 11,799 requests, 0% HTTP failures, p95 latency 1.223 ms.
  - Dependency fault: 273 requests, 47.985% HTTP failures while downstream was
    stopped, followed by a successful dependency recovery check.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase03_traffic_and_chaos.py` passes
    7 phase 03 tests.
  - `make format` passes.
  - `make lint` passes.
  - `make typecheck` passes.
  - `make test` passes 33 tests.
  - `make validate-scenarios` validates all five scenario files.
  - `make check` passes format, lint, typecheck, tests, 90% coverage,
    scenario validation, and package build.
