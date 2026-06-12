# Phase 04 Prometheus Query Contract

Live phase 04 metric collection requires a local Prometheus endpoint. The
collector reads the endpoint from the explicit `prometheus_url` argument or
from `PROMETHEUS_URL`.

Queries use the Prometheus HTTP API:

```text
GET /api/v1/query?query=<promql>
```

## MVP Signals

| Signal | Default Query Intent |
| --- | --- |
| `memory_usage` | Percent of container memory limit consumed by target pods. |
| `cpu_usage` | CPU usage percentage over a five-minute rate window. |
| `cpu_throttling` | Percent throttled CFS periods over a five-minute rate window. |
| `request_latency` | p95 request latency in milliseconds. |
| `error_rate` | Percent 5xx responses over all HTTP responses. |

## Degradation Rules

- If `PROMETHEUS_URL` is missing, collection fails with a required-backend
  diagnostic.
- If the endpoint is unreachable or returns invalid JSON, collection fails with
  the endpoint and parse failure.
- If Prometheus returns a non-success status, collection fails for that signal.
- Fixture tests mock the HTTP API so repository checks do not require a live
  Prometheus instance.
