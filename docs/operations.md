# Operations

## Safety Model

Ampule Chamber refuses obviously unsafe Kubernetes contexts containing
production fragments such as `prod`, `production`, `prd`, or `live`.

Live runs require the expected local kind context:

```text
kind-ampule-chamber
```

## Secret Handling

Do not place secret values in `runtime.config`. Secret-like keys are rejected
before planning and must be modeled through redacted secret environment
configuration.

## Cleanup

Reports record cleanup status. Local guided assessments do not create live
Kubernetes resources. Live scenario runs clean chamber-owned resources unless
the run is retained for debugging.
