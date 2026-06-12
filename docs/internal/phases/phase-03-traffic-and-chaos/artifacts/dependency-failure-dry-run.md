# Dependency Failure Dry-Run Plan

Generated from `scenarios/dependency-failure.yaml` with run id
`phase03-artifact`.

## Traffic

- Target URL:
  `http://sample-service-svc-ac334bed.chamber-local-dependency-failure-001-ac334bed.svc.cluster.local:8080/dependency`
- Command:
  `k6 run --summary-export docs/internal/phases/phase-03-traffic-and-chaos/artifacts/dependency-failure-001-k6-summary.json docs/internal/phases/phase-03-traffic-and-chaos/artifacts/dependency-failure-001-k6.js`
- Total duration: `420` seconds
- Result fields: `http_reqs`, `http_req_failed`, `http_req_duration.p95`,
  `http_req_duration.p99`

## Fault Actions

- At `120` seconds: apply a chamber-owned `NetworkPolicy` that denies egress
  from pods labeled with `chamber.ampule.dev/run-id=phase03-artifact`.
- At `180` seconds: delete the same `NetworkPolicy` with
  `--ignore-not-found=true` so cleanup is explicit and reversible.

## Timeline

| Offset Seconds | Event | Source |
| --- | --- | --- |
| 0 | `traffic_start` | `load` |
| 120 | `fault_start` | `chaos` |
| 180 | `fault_removed` | `chaos` |
| 420 | `traffic_stop` | `load` |
| 420 | `recovery_validate` | `orchestrator` |

## Live-Local Note

`kubectl`, `kind`, and `k6` are available on this machine after installing k6
with Homebrew. Live Docker-backed k6 evidence is recorded in
`live-k6-results.md`.
