# Named chambers and executable families — 2026-09-14

This extends draft PR #42 on its existing branch and unreleased 1.10.0 version.
The user explicitly requested both features after the foundation PR. The earlier
foundation-only scope is superseded by this implementation.

## Implemented contract

- Immutable named context/namespace/service/workload profiles, clone-to-revise,
  configured capabilities, history, admission occupancy, and saved budgets.
- Plan and job configuration snapshots retain the authoritative chamber profile.
  Starts revalidate profile budgets and reject context overrides.
- Single-host, namespace-level admission serialization covers overlapping
  profiles and custom control-plane jobs. Retry identity is checked before
  occupancy. Interrupted jobs retain a cleanup block; an explicit operator
  acknowledgement releases admission and preserves the failed report.
- Chaos Mesh NetworkChaos dependency latency/outage and StressChaos CPU/memory
  pressure; exact opted-in pods, bounded parameters/duration, pre-mutation
  recovery identity, injection confirmation, after-load confirmation, pause,
  AllRecovered confirmation, finalizer-aware deletion, and separate recovery
  traffic artifacts. Failed restoration retains manual remediation information.
- Scoped Prometheus queue depth/oldest-age observations across baseline, load,
  and recovery. Missing identity, stale/nonfinite/multiple-series data, or
  unexercised backpressure cannot pass. Queue budgets and final drain are
  explicit assertions, not inferred from task completion.
- Family-specific wizard controls and review, immutable configuration, report
  assertion tables, mobile assertion cards, and comparison identity gates.

## Verification

- `make check`: 277 tests, 90% combined branch/line coverage, formatting,
  lint, types, ten scenario files, deployment/release validation, strict docs,
  wheel and source build for 1.10.0. Thresholds remain unchanged.
- Docker: current repository mounted read-only into the patched PR42 image;
  the 11 new acceptance tests passed under runtime Python. No host Kubernetes
  credentials, Docker socket, or live API keys were mounted.
- Full workflow fixtures exercised all four controller fault families through
  reports, with separate tests for failed injection, failed/ambiguous create,
  denied opt-in, failed restoration, missing data, budget rejection, admission
  exclusion, idempotency, profile persistence and cleanup acknowledgement.
- A real loopback HTTP Prometheus fixture inside Docker returned scoped fresh
  queue series. The collector observed baseline 0, load 5 then 10, and recovery
  0 then 0; all four queue assertions passed.
- Chrome created a chamber and dependency-latency plan through the UI, verified
  saved target defaults and immutable configuration, family-specific controls,
  review and fixture report assertions. At 390x844, document width and scroll
  width were both 390 pixels. Screenshots are in `chamber-family-screenshots/`.
- Chrome caught and verified the fix for a saved chamber leaving repository
  mode selected. Review now includes experiment recovery and unverified setup;
  mobile report assertions use labeled cards to avoid clipped result columns.
  The static asset cache key was updated so an existing browser receives the new UI.

## Boundaries

Fault injection/controller observations and traffic in the workflow acceptance
fixture are simulated, explicitly marked in its configuration. No live
Kubernetes/Chaos Mesh fault was performed; actual controller, kernel support,
RBAC and restoration must still be verified on the intended non-production
cluster. Configured capability is not readiness. CPU pressure does not establish
throttling; memory pressure does not establish OOM recovery; network loss is not
HTTP 429/5xx or retry-amplification verification. Existing required telemetry
still gates results. Queue polling can miss spikes between observations.

Profiles and admission are single-host control-plane capabilities, not distributed
runner scheduling or Studio identity federation. Independent CLI runs do not
participate in control-plane admission locks. These limits are documented in the
public scenario guide, rather than represented as implemented features.
