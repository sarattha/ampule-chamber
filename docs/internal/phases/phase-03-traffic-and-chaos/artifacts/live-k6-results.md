# Live k6 Results

Run date: 2026-06-12

## Tooling

- Installed k6 with Homebrew.
- Verified version: `k6 v2.0.0 (commit/devel, go1.26.3, darwin/arm64)`.
- Docker sample service was started with
  `docker compose -f examples/sample-service/docker-compose.yml up --build -d`.

## Health Smoke

- Script:
  `docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-smoke-k6.js`
- Summary:
  `docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-smoke-k6-summary.json`
- Target: `http://localhost:8080/healthz`
- Duration: 10 seconds
- Max VUs: 2
- Requests: 11,799
- HTTP failure rate: 0%
- Checks: 11,799 passed, 0 failed
- p95 latency: 1.223 ms

## Dependency Fault

- Script:
  `docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-dependency-fault-k6.js`
- Summary:
  `docs/internal/phases/phase-03-traffic-and-chaos/artifacts/live-dependency-fault-k6-summary.json`
- Target: `http://localhost:8080/dependency`
- Duration: 20 seconds
- Max VUs: 2
- Fault action: stopped `downstream-api` after 5 seconds and restarted it after
  another 8 seconds.
- Requests: 273
- HTTP failure rate: 47.985%
- Checks: 142 passed, 131 failed
- p95 latency: 16.7564 ms
- Recovery check after restart:
  `curl -fsS http://localhost:8080/dependency` returned
  `{"dependency": "ok", "status": 200}`.

## Cleanup

`docker compose -f examples/sample-service/docker-compose.yml down` removed the
sample containers and network after the live runs.
