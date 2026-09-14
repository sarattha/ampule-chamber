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
  source: derived
  revision: 2f71931d5ebcc9f0
  origin:
    source: bundled
    revision: 4c60ecfa591b9429
  requiredSignals: [logs, request_latency, error_rate]
```

The top-level source and revision describe the fully resolved configuration
that was planned: custom exercises use `custom`, catalog/imported exercises use
`derived`, and saved definitions use `user`. The revision is recomputed from
the resolved content after edits. `origin` preserves the selected/imported
source and revision separately. Resolved provenance is copied to
`run-metadata.json` and `run.json`. Configs without `apiVersion` or the new
`scenario` mapping remain supported for execution and continue to derive
`<service>-assessment` when `scenarioId` is absent; catalog/import APIs still
require the current `apiVersion` explicitly.

`Scenario` imports map their traffic block to one HTTP journey. Full
`ChamberConfig` imports retain all HTTP or Relayna journeys, request encodings,
load settings, lifecycle settings, follow-up checks, agent mode, and required
signals. Mixed HTTP and Relayna journeys, invalid paths or status codes,
unsupported encodings, unsafe load values, invalid lifecycle values, and
unresolved `${PLACEHOLDER}` or `{{ placeholder }}` values are rejected with a
field-specific message. A `Scenario` import is also rejected when its projected
peak VUs exceed `safety.maxVirtualUsers` or its total traffic-stage duration
exceeds `safety.maxDuration`. Imports are limited to 256 KiB.

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

## Named chambers and executable experiments

Open **Chambers** to save a Kubernetes context, namespace, Service, workload,
Prometheus URL, VU and traffic/recovery duration budgets. Profiles are immutable;
**Clone settings** creates a new profile. **Plan assessment** prefills Kubernetes
attach mode and captures the profile in the plan. This is a reusable environment,
not a provisioned cluster or a Studio tenant boundary. The workspace and runner
remain single-host. Capability settings describe operator configuration; they do
not claim a successful cluster connection.

Only one control-plane job may occupy a context/namespace at a time, even through
different profiles or custom plans. Conflicting starts return HTTP 409. An
interrupted job blocks new admission until an operator verifies cleanup and
records that verification on the Chambers page. This releases admission without
changing the failed report. CLI runs outside the control plane do not participate
in these job admission locks. Chamber identity and experiment settings also gate
run comparison.

In **New assessment → Exercise → Executable experiment**, choose one family:

| Family | Execution and evidence | Required setup |
| --- | --- | --- |
| Dependency latency | Delay traffic from one target pod to one dependency pod; verify injection, traffic contract, restoration, recovery traffic | Chaos Mesh NetworkChaos, explicit opted-in pods in the same namespace |
| Dependency outage | 100% packet loss on that same directed pod-to-pod path | Same as latency |
| CPU pressure | One stress worker with an explicit load percentage in one container | Chaos Mesh StressChaos, opted-in target pod and explicit container |
| Memory pressure | One stress worker with a bounded allocation up to 512 MB | Same as CPU pressure |
| Queue backpressure and drain | Fresh baseline/load/recovery queue depth and oldest-task-age observations, bounded peaks, and two final empty observations | Prometheus queries returning exactly one series with namespace and configured queue identity labels |

Faults require `chamber.ampule.dev/allow-faults=true` on **every explicitly
selected pod**. Install Chaos Mesh separately using its official deployment
instructions. With Helm, `rbac.allowFaults=true` grants the required namespace
NetworkChaos/StressChaos create/get/list/watch/patch/delete permissions. Raw
manifest installations must add equivalent namespace Roles explicitly. Chamber
never installs the controller or broadens its own runtime permissions.

Fault duration must cover the traffic exercise. If the controller no longer
reports `AllInjected` when traffic finishes, the result is inconclusive; increase
the duration or shorten the load. Recovery pauses the experiment, requires
`AllRecovered`, deletes its resource with finalizer processing, then runs a
separate bounded recovery journey. Interrupted cleanup retains the resource name
and manual remediation guidance in `evidence/experiment.json`. It never removes
controller finalizers to manufacture recovery. Controller outages can prevent
restoration even with a configured duration; verify targets before releasing
admission.

CPU pressure is a workload stress test, not proof of CPU throttling. Memory
pressure is not proof of OOM recovery. Network outage does not simulate specific
HTTP 429/5xx responses or prove retry amplification bounds. Existing required
signals still gate the result; selecting an experiment does not remove missing
telemetry requirements. Inspect the report's **Experiment assertions** to see
exactly what passed, failed, or remains missing.

Queue PromQL must retain the namespace and queue labels, for example
`queue_depth{namespace="payments-test",queue="tasks"}` and
`queue_oldest_age_seconds{namespace="payments-test",queue="tasks"}` with identity
`{"queue":"tasks"}`. Missing, stale (over 15 seconds), nonfinite, multiple-series,
or wrong-identity responses cannot pass. At least two load samples must show
that backpressure was exercised; traffic that never grows the queue is
inconclusive. Polling occurs every two seconds plus query latency. Very short
spikes between polls are outside this sampled evidence boundary. Admissions stop
before a separate recovery observation window begins. A zero-depth final queue
must also report zero oldest-task age.

API clients create profiles with `POST /api/v1/chambers`, list profiles and
occupancy with `GET /api/v1/chambers`, and select a profile in a plan using
`config.chamber.id`. Plan submission resolves the authoritative stored profile.
Existing bearer authentication and browser CSRF rules apply. Experiment settings
are stored in `config.experiment`; use the UI to generate a validated example.
Markdown, HTML and JSON reports include the same persisted assertions and
registered evidence reference.
