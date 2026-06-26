# Scenario Contracts

Scenario YAML files describe the chamber test target, environment, traffic,
faults, observability signals, success conditions, and safety limits.

Validate scenarios with:

```bash
make validate-scenarios
```

Current bundled scenarios:

- `baseline-health.yaml`
- `dependency-failure.yaml`
- `external-text-translation.yaml`
- `multi-service-dependency.yaml`
- `oom-stress.yaml`
- `retry-storm.yaml`
- `soak-test.yaml`
- `translation-memory-backpressure-read.yaml`
- `translation-memory-health-ramp.yaml`
- `translation-memory-large-text-admission.yaml`

The live runner remains available for scenario-based kind runs:

```bash
uv run ampule-chamber run \
  --scenario scenarios/baseline-health.yaml \
  --output docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md \
  --prometheus-url http://127.0.0.1:9090
```
