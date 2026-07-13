# Scenario Contracts

Scenario YAML files describe the chamber test target, environment, traffic,
faults, observability signals, success conditions, and safety limits.

## Control-plane scenario formats

The Exercise editor accepts both existing `Scenario` documents and complete
`ChamberConfig` documents with `apiVersion: chamber.ampule.dev/v1alpha1`.
Both formats are validated on the server and normalized into the same editable
journey fields before planning. `ChamberConfig` remains the canonical execution
format; the UI never executes an opaque catalog document directly.

A generated config records the stable identity and provenance while retaining
the backward-compatible top-level `scenarioId`:

```yaml
scenarioId: payments-baseline
scenario:
  id: payments-baseline
  name: Payments baseline
  description: Bounded baseline traffic and telemetry validation.
  tags: [payments, baseline]
  source: user
  revision: 2f71931d5ebcc9f0
  requiredSignals: [logs, request_latency, error_rate]
```

The revision is a content digest. It and the source are copied to
`run-metadata.json` and `run.json` so a run can be traced to the selected
definition. Configs without the new `scenario` mapping remain supported and
continue to derive `<service>-assessment` when `scenarioId` is absent.

`Scenario` imports map their traffic block to one HTTP journey. Full
`ChamberConfig` imports retain all HTTP or Relayna journeys, request encodings,
load settings, lifecycle settings, follow-up checks, agent mode, and required
signals. Mixed HTTP and Relayna journeys, invalid paths or status codes,
unsupported encodings, unsafe load values, invalid lifecycle values, and
unresolved `${PLACEHOLDER}` or `{{ placeholder }}` values are rejected with a
field-specific message. Imports are limited to 256 KiB.

Configured faults are shown as recommendations only. Loading a catalog or
imported scenario never changes **Observe only**; the operator must explicitly
select a supported attach-mode fault and review its rollback requirement.

## Catalog storage

Bundled examples remain read-only under `scenarios/`. User scenarios are full,
validated `ChamberConfig` documents stored under
`<workspace>/scenarios/<scenario-id>.yaml`. In Kubernetes this is the configured
workspace PVC, so definitions survive pod replacement. Writes use a temporary
file and atomic replacement. IDs are path-safe lowercase DNS-like identifiers.

**Save as new** refuses to overwrite an existing ID. **Replace existing user
scenario** requires an explicit confirmation. Authentication protects every
catalog endpoint, and create, replace, and import validation also require the
existing CSRF token.

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
  --output .chamber/reports/live-baseline-report.md \
  --prometheus-url http://127.0.0.1:9090
```
