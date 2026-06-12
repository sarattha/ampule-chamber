# MVP Scenario Schema

Scenario files are YAML documents with:

```yaml
apiVersion: chamber.ampule.dev/v1alpha1
kind: Scenario
```

The executable parser and validation rules live in
`chamber/contracts/scenario.py`. The first fixtures live in `scenarios/`.

## Required Sections

| Section | Purpose |
| --- | --- |
| `metadata` | Stable id, display name, owner, description, and tags. |
| `target` | Target service identity, ports, health/readiness endpoints, and runtime contract. |
| `environment` | Runtime provider, namespace label, replica count, resources, and dependencies. |
| `baseline` | Startup, readiness, health, dependency, and telemetry checks that must pass before experiments. |
| `traffic` | Load tool and staged virtual user profile. |
| `faults` | Optional controlled fault injections. Use `type: none` when no fault is injected. |
| `observability` | Signals required to evaluate the scenario. |
| `failureConditions` | Structured conditions that make the service risky or the scenario fail. |
| `successConditions` | Structured conditions required for acceptable behavior. |
| `safety` | Maximum duration, load budget, and production-context guardrail. |

## MVP Vocabularies

Environment providers:

- `docker`
- `kind`
- `k3d`
- `aks`
- `existing_kubernetes`

Traffic tools:

- `none`
- `k6`
- `locust`
- `custom`

Observability signals:

- `pod_status`
- `kubernetes_events`
- `container_restarts`
- `memory_usage`
- `cpu_usage`
- `cpu_throttling`
- `request_latency`
- `error_rate`
- `logs`
- `traces`
- `queue_depth`
- `dependency_health`

Failure condition types:

- `oom_killed`
- `restart_count_increase`
- `restart_loop`
- `latency_above_ms`
- `error_rate_above_percent`
- `memory_growth_for`
- `cpu_throttling_above_percent`
- `dependency_unavailable`
- `dependency_error_rate_above_percent`
- `retry_amplification`
- `queue_backlog_not_draining`
- `recovery_time_above`
- `baseline_check_failed`

Success condition types:

- `no_oom_killed`
- `no_restart_loop`
- `latency_below_ms`
- `error_rate_below_percent`
- `memory_growth_below_percent`
- `cpu_throttling_below_percent`
- `dependency_recovers`
- `queue_backlog_drains_within`
- `recovery_time_below`
- `baseline_checks_pass`

## Validation Rules

- Required top-level sections must be present.
- `metadata.id` and `metadata.name` must be non-empty strings.
- Runtime provider must use an allowed provider value.
- Docker scenarios must point at a `composeFile`.
- Replica count must be a positive integer.
- Baseline checks, traffic stages, observability signals, failure conditions,
  and success conditions must be non-empty lists.
- Traffic stages must include `duration` and non-negative `targetVus`.
- Condition and signal types must come from the MVP vocabularies above.
- Safety must include `maxDuration` and `maxVirtualUsers`.
