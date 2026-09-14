# Load suite priorities 1–7 acceptance

Implementation: unreleased 1.10.0, PR #42, 2026-09-14.

All seven priorities are implemented in the opt-in `traffic.load` contract.
Configuration and operational semantics are documented in
[scenarios](../../../../scenarios.md#arrival-capacity-and-soak-load-suites).

## Evidence

- `tests/test_load_suite.py`: real HTTP/SSE target, weighted arrival traffic,
  dropped accounting and bounded concurrency, capacity stopping/recovery, soak
  windows, per-journey and phase gates, client-observed lifecycle coverage,
  datasets, chained extraction/business assertions, auth-value omission,
  redirect rejection, response-size and continuous-stream deadline bounds,
  scoped/stale/ambiguous Prometheus responses, generator limits, saved-plan
  round trips, report rendering, tamper/missing-evidence gates and chamber budgets.
- Docker used `ampule-chamber:pr42-ci-fix` with current source mounted read-only.
  The FastAPI backend and served frontend ran at loopback port 18765. No host
  Kubernetes credentials or Docker socket were mounted.
- [Runtime verification script](load-suite-runtime/runtime_verify.py) exercised
  real HTTP/SSE traffic with simulated Kubernetes discovery and Chaos Mesh
  controller responses. It performed no live cluster fault injection.
- [Main load](load-suite-runtime/load-summary.json): 20 scheduled, 20 started,
  20 successful, zero dropped, p95 16.054 ms / p99 24.476 ms; 15 Relayna lifecycle
  samples and 5 HTTP samples. These tiny loopback figures validate execution,
  not service capacity or production performance.
- [Baseline](load-suite-runtime/baseline-load-summary.json) and
  [recovery](load-suite-runtime/recovery-load-summary.json): 5 scheduled and
  successful journeys each, kept separate from the fault phase.
- Chrome saved a capacity plan at 5/s then 10/s for 10 seconds each and 5 seconds
  recovery. The review receipt and saved configuration matched.
- Chrome saved a named-chamber mixed HTTP/Relayna soak plan at 10/s for 60 seconds
  plus 10 seconds recovery. Selecting suite mode switches to the full editor and
  hides the legacy VU/iteration schedule.
- Chrome checked desktop and 390px mobile reports: document scroll width matched
  viewport width; metric cards, per-phase thresholds, missing lifecycle values
  and diagnostics remained readable. Baseline/recovery details collapse by
  default and the main load window is expanded.

Screenshots: [desktop report](load-suite-screenshots/report-desktop.jpg),
[mobile report](load-suite-screenshots/report-mobile.jpg),
[mobile load details](load-suite-screenshots/load-mobile.jpg),
[mobile editor](load-suite-screenshots/editor-mobile.jpg).

## Boundaries

One local generator process; no distributed or browser-protocol load generation.
Rates count journeys (one request, task lifecycle or chain), not requests inside
multistep chains. Weights are probabilistic; missing low-weight coverage stays
inconclusive. Lifecycle queue/worker intervals are SSE client estimates and
exclude the admission-to-stream-connect gap. CPU/RSS reflect the generator
process; RSS is a high-water measurement. Soak growth is first/last observed
memory, not a leak diagnosis. Capacity is limited to passing tested rates.
No live Chaos Mesh acceptance or production workload benchmark is claimed.

## Release checks

Final `make check` passed: 297 tests, 90% aggregate coverage, scenario/deployment/
release validation, strict docs and package builds. The 20 load-suite tests also
passed inside Docker (30.229s). Chrome captured no console errors in the final
editor/report check. Latest-head CI and Codex review must pass before merge;
their outcome is recorded in the phase execution plan.

Codex review identified the effective Prometheus URL override issue; regression
tests verified its fix. Regression coverage also rejects cached timestamp growth claims and
requires post-drain queue observations. See the phase execution plan for review
and CI commit evidence.
