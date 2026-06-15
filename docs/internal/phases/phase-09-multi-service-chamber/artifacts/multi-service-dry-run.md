# Phase 09 Multi-Service Dry Run

Run id used for planner inspection: `phase07-09-dry-run`.

## Scenario

`scenarios/multi-service-dependency.yaml` deploys:

- target: `sample-service`
- controlled dependency: `downstream-api`

The target receives `DOWNSTREAM_URL` pointing at the generated dependency
Service, and the dependency runs the sample image with:

```text
--mode downstream --port 8081
```

## Rendered Resources

- Namespace
- Target Deployment
- Target Service
- Dependency Deployment
- Dependency Service

Dry-run resource names:

- `sample-service-deployment-f341261a`
- `sample-service-svc-f341261a`
- `downstream-api-deployment-f341261a`
- `downstream-api-svc-f341261a`

## Fault And Timeline

The multi-service scenario patches `downstream-api` with
`FAULT_STATUS=500` at 60 seconds and restores normal behavior at 120 seconds.

Timeline:

- `environment_setup` at 0 seconds
- `traffic_start` at 0 seconds
- `fault_start` at 60 seconds
- `fault_removed` at 120 seconds
- `traffic_stop` at 240 seconds
- `observation_stop` at 240 seconds
- `recovery_validate` at 240 seconds
- `cleanup_planned` at 240 seconds

## Report Evidence Shape

Reports now include dependency graph, agent analysis placeholder, and recovery
status sections. Runtime evidence records also carry optional service identity
for target/dependency attribution.
