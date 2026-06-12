# Phase 06 Live Runner Workflow

## Prerequisites

- Current Kubernetes context is `kind-ampule-chamber`.
- The kind cluster already exists.
- `ampule/sample-service:local` is already loaded into the kind nodes.
- `kubectl`, `kind`, `docker`, and `k6` are installed on `PATH`.
- `PROMETHEUS_URL` points at a reachable Prometheus HTTP API endpoint.

The runner verifies these prerequisites before applying Kubernetes resources.
It does not create the kind cluster, build the sample image, load the image into
kind, or install Prometheus.

## Commands

Run one scenario:

```bash
uv run ampule-chamber run \
  --scenario scenarios/baseline-health.yaml \
  --output docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md
```

Run all live-supported scenarios one at a time:

```bash
uv run ampule-chamber run \
  --scenario scenarios/dependency-failure.yaml \
  --output-dir docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts
```

Use `--retain` to keep chamber resources after a run, or
`--retain-on-failure` to keep resources only when a scenario fails. The default
is to delete chamber-owned resources after every scenario.

## Outputs

Each scenario writes:

- a markdown reliability report,
- structured run metadata,
- generated k6 script and summary JSON,
- Kubernetes and Prometheus evidence JSON,
- command results and cleanup status.

## Fault Limitations

`dependency_unavailable` uses a chamber-scoped `NetworkPolicy` egress denial.

`dependency_errors` and `dependency_rate_limit` are not live-supported in phase
06. The runner refuses those scenarios before live Kubernetes actions because
the chamber does not yet provision a downstream dependency workload or fault
proxy for controlled 500/429 responses.

`memory_pressure` patches the sample-service deployment memory limit for the
scenario window and restores it afterward. It is not a general memory stress
sidecar.
