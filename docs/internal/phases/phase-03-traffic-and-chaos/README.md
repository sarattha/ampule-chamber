# Phase 03: Traffic And Chaos

Implement traffic execution and controlled failure injection. This phase should
exercise a deployed target service with realistic traffic and intentionally
degrade dependencies or infrastructure while preserving repeatability.

The goal is to find unsafe behavior, not just maximum throughput.

## What Needs To Be Done

- Integrate an MVP load runner such as k6 or Locust.
- Support normal, peak, burst, and soak traffic profiles from scenarios.
- Record traffic configuration, timing, request counts, latency, and error
  summaries.
- Inject simple dependency failure for the MVP.
- Add extensible hooks for pod kill, latency, DNS, CPU, and memory pressure.
- Coordinate fault windows with active traffic.
- Emit structured experiment events for observability and reports.

## Agent Notes

- Fault injection must be controlled, scoped, and reversible.
- Prefer explicit scenario timing over hidden sleeps.
- Store generated k6 scripts, Locust files, run summaries, and fault timelines
  in `artifacts/` unless promoted to source.

