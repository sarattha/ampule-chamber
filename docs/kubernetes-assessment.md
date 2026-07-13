# Generic Kubernetes Assessment

Generic Kubernetes mode runs a reviewed `chamber.yaml` against any non-production
Kubernetes cluster reachable through `kubectl`. AKS, EKS, and GKE are supported
as ordinary Kubernetes targets when your local kubeconfig can reach them, but
Ampule Chamber does not require cloud-provider SDK integration for this mode.

## Mode Comparison

Use `assess --mode local` when you want deterministic local planning artifacts
without creating Kubernetes resources.

Use `ampule-chamber run --scenario ...` when you want the scenario-based live
runner for the local `kind-ampule-chamber` context.

Use `assess --mode kubernetes` with `runtime.mode: deploy` when you have a
reviewed `chamber.yaml`, images that the selected cluster can pull, and
permission to create and clean up a chamber-owned namespace in a real
Kubernetes environment.

Use `assess --mode kubernetes` with `runtime.mode: attach` when the service is
already deployed in a non-production namespace and you want Ampule Chamber to
run traffic, collect scoped evidence, and render a report without applying
manifests or deleting external resources.

## Prerequisites

- `kubectl`
- `k6`
- working Kubernetes credentials and kubeconfig context
- Prometheus endpoint access when metrics evidence is enabled
- target service images already pullable by the selected cluster

Provider credential setup is outside Ampule Chamber. These examples only update
your local kubeconfig:

```bash
az aks get-credentials --resource-group <resource-group> --name <cluster>
aws eks update-kubeconfig --region <region> --name <cluster>
gcloud container clusters get-credentials <cluster> --region <region>
```

## Safety Model

Generic Kubernetes mode requires an explicit context. It refuses context names
containing production-like fragments such as `prod`, `production`, `prd`, or
`live`.

Deploy-mode runs use a chamber-owned namespace whose name starts with
`chamber-`. Preflight checks run before any manifest is applied and verify
access to create the namespace, create common workload resources, read pods,
read events, read logs, and delete the chamber namespace. Cleanup deletes
chamber-labeled resources and the chamber-created namespace.

Attach-mode runs require an explicit `runtime.namespace`, verify that namespace
already exists, and refuse production-like context or namespace names containing
fragments such as `prod`, `production`, `prd`, or `live`. Attach mode verifies
configured workloads and services before traffic, discovers their selectors and
pods, and records a pre-test state snapshot. Cleanup never deletes the target
namespace or externally deployed resources.

Ampule Chamber does not mutate existing production namespaces in this mode. It
also does not build, push, or import images into cloud registries.

## Minimal Config

```yaml
apiVersion: chamber.ampule.dev/v1alpha1
kind: ChamberConfig
service:
  name: my-service
  repo: ../target-service
deployment:
  manifests:
    - manifests/app.yaml
  workloads:
    - name: my-service
      role: target
      kind: Deployment
  images:
    replacements:
      - source: my-service:local
        target: registry.example.com/my-service:2026-06-18
traffic:
  entrypoint: my-service
  journeys:
    - name: baseline
      method: GET
      path: /health
      expectedStatus: 200
      stages:
        - duration: 30s
          targetVus: 4
        - duration: 30s
          targetVus: 0
    - name: large-payload
      method: POST
      path: /translations
      expectedStatus: 202
      requestEncoding: json
      vus: 1
      iterations: 3
      textBytes: 32768
      body:
        language_target: th
runtime:
  provider: kubernetes
  mode: deploy
  kubernetesContext: <kube-context>
  namespaceBase: chamber-my-service
  cleanup: true
  prometheusUrl: http://prometheus.example
  trafficAccess:
    mode: port-forward
    service: my-service
    servicePort: 8080
agents:
  mode: offline
  exclude:
    - onboarding-agent
```

### Relayna task lifecycle journey

Services built on the Relayna SDK can use a stateful journey that submits a
task, extracts the response task ID, subscribes to its SSE stream, and waits for
a successful terminal status:

```yaml
traffic:
  entrypoint: translation-service
  journeys:
    - name: translation-lifecycle
      adapter: relayna
      method: POST
      path: /translations
      expectedStatus: 202
      requestEncoding: json
      vus: 1
      iterations: 1
      body:
        text: Hello from Ampule Chamber.
        language_target: Thai
        priority: 5
      relayna:
        taskIdPath: task_id
        eventsPath: /events/{task_id}
        terminalStatuses: [completed, failed]
        successStatuses: [completed]
        timeoutSeconds: 300
```

Relayna journeys are stateful and run separately from plain k6 journeys. One
traffic execution cannot mix the two adapter types. For every iteration,
Chamber records the submission status and latency, extracted task ID, observed
status sequence, terminal status, stream duration, and total end-to-end task
duration in `evidence/relayna-summary.json`. A `failed` terminal status, missing
task ID, malformed response, stream error, or timeout fails the traffic stage.

`taskIdPath` is a dot-separated JSON response path such as `task_id` or
`data.task_id`. `eventsPath` must be an absolute path containing `{task_id}`.
The task ID is URL-encoded before the SSE request is sent. Request bodies are
kept in the resolved configuration but are not copied into Relayna execution
evidence.

Relayna journeys also accept multipart document and image submissions. Scalar
strings, integers, and booleans are serialized directly. Arrays and objects use
an explicit JSON descriptor so their wire representation is deterministic.

Single-file OCR-style example:

```yaml
traffic:
  entrypoint: document-service
  journeys:
    - name: document-processing-lifecycle
      adapter: relayna
      method: POST
      path: /tasks
      expectedStatus: 202
      requestEncoding: multipart
      multipart:
        maxFileBytes: 134217728
        maxTotalBytes: 268435456
        fields:
          task_id: chamber-document-smoke
          mode: layout
          priority: 5
          strict_mode: false
        files:
          - field: file
            path: /durable/chamber/workspace/uploads/document.png
            filename: document.png
            contentType: image/png
            required: true
      vus: 1
      iterations: 1
      relayna:
        taskIdPath: task_id
        eventsPath: /events/{task_id}
        terminalStatuses: [completed, failed]
        successStatuses: [completed]
        timeoutSeconds: 300
```

Multi-file extraction-style example with an optional ROI mask and JSON-encoded
array/object fields:

```yaml
traffic:
  entrypoint: extraction-service
  journeys:
    - name: field-extraction-lifecycle
      adapter: relayna
      method: POST
      path: /tasks
      expectedStatus: 202
      requestEncoding: multipart
      multipart:
        fields:
          task_id: chamber-extraction-smoke
          priority: 5
          strict_mode: false
          extraction_fields:
            encoding: json
            value:
              - name: document_number
                type: string
          processing_config:
            encoding: json
            value:
              engine: internal
              fallback: true
        files:
          - field: file
            path: /durable/chamber/workspace/uploads/document.png
            filename: document.png
            contentType: image/png
            required: true
          - field: roi
            path: /durable/chamber/workspace/uploads/roi.png
            filename: roi.png
            contentType: image/png
            required: false
      vus: 1
      iterations: 1
      relayna:
        taskIdPath: task_id
        eventsPath: /events/{task_id}
        terminalStatuses: [completed, failed]
        successStatuses: [completed]
        timeoutSeconds: 300
```

`task_id` multipart fields receive the same bounded per-iteration uniqueness
suffix as existing HTTP multipart journeys. Set `uniqueTaskIdField` to another
field name, or to an empty string to disable this behavior. Chamber follows only
the value at `relayna.taskIdPath`, even when the admission response also contains
child task IDs.

Multipart files must resolve inside the Chamber workspace and remain readable.
PDF, PNG, JPEG, TIFF, BMP, GIF, and WebP are accepted; empty files, missing or
unsupported content types, workspace escapes, and requests over the configured
limits are rejected. Hard limits are 128 MiB per file and 256 MiB per request.
Lifecycle evidence and reports contain field name, filename, content type, byte
size, and SHA-256 digest only. They never contain the file path or bytes.

Each Relayna task result includes `failure_stage` so admission,
`task_id_extraction`, `sse_connection`, `timeout`, and `terminal_failure`
outcomes remain distinguishable. Any unsuccessful lifecycle makes traffic fail
and therefore prevents a ready assessment result.

Store secrets as environment requirements, not plaintext runtime values:

```yaml
runtime:
  requiredEnv:
    - API_TOKEN
  secretEnv:
    - API_TOKEN
```

## Attach Mode Config

Attach mode uses the same CLI command, but the reviewed config points to
existing Kubernetes resources instead of deployable manifests:

```yaml
apiVersion: chamber.ampule.dev/v1alpha1
kind: ChamberConfig
service:
  name: my-service
  repo: ../target-service
deployment:
  manifests: []
  workloads:
    - name: my-service
      role: target
      kind: Deployment
  services:
    - name: my-service
      port: 8080
traffic:
  entrypoint: my-service
  journeys:
    - name: baseline-health
      method: GET
      path: /health
      expectedStatus: 200
      stages:
        - duration: 30s
          targetVus: 4
        - duration: 30s
          targetVus: 0
runtime:
  provider: kubernetes
  mode: attach
  kubernetesContext: <kube-context>
  namespace: my-service-test
  cleanup: false
  prometheusUrl: http://prometheus.example
  trafficAccess:
    mode: port-forward
    service: my-service
    servicePort: 8080
agents:
  mode: offline
```

Run it with:

```bash
uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context>
```

Attach mode writes the standard run directory plus attach-specific evidence:

```text
evidence/
  preflight.json
  attach-discovery.json
  pre-test-state.json
  kubernetes-commands.json
  k6-summary.json
  prometheus-memory.json
  rollback.json
```

## Command Flow

```bash
kubectl config current-context
kubectl get ns

uv run ampule-chamber onboard \
  --repo ../target-service \
  --output chamber.yaml

uv run ampule-chamber plan --config chamber.yaml

uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context> \
  --prometheus-url <prometheus-url>

uv run ampule-chamber report --run .chamber/runs/<run-id>
```

The run directory keeps the standard artifact contract:

```text
.chamber/runs/<run-id>/
  chamber.yaml
  plan.json
  adapted-manifests/
  evidence/
    preflight.json
    kubernetes-commands.json
    k6-summary.json
    prometheus-memory.json
  findings.json
  agent/
  run-metadata.json
  report.md
```

Regenerate a report from an archived run directory with:

```bash
uv run ampule-chamber report --run .chamber/runs/<run-id>
```

## Traffic And Evidence

Kubernetes assessments execute every configured `traffic.journeys` item as a
named k6 scenario. Use `stages` for ramping VU traffic, or `vus` plus
`iterations` for bounded workloads such as large payload admission tests. POST
journeys can include a JSON `body`; `textBytes` expands the configured text
payload to a bounded size for memory-oriented service tests.

When `runtime.prometheusUrl` or `--prometheus-url` is set, deploy mode captures
`evidence/prometheus-memory.json` with container memory working set, container
CPU usage, and restart counter queries for the chamber namespace. This evidence
is supplied to agents and reports alongside k6 and Kubernetes command output.

Prometheus evidence satisfies the required metrics gate only when all three
queries complete with `ok: true` and each returns at least one target series.
A successful query with zero series proves that Prometheus was reachable, but
does not prove that target telemetry was collected; the assessment is therefore
inconclusive, cannot be `ready`, and reports less than 100% evidence coverage.
Query failures and zero-series responses remain registered as diagnostic
artifacts, with their distinct reasons rendered in offline Markdown and HTML
reports.

Attach mode uses the same evidence file, but narrows Prometheus queries to the
configured namespace and the exact discovered target pod names. Prometheus
metrics are not Kubernetes logs: logs and events are collected separately in
`evidence/kubernetes-commands.json` for discovered pods only, not the whole
namespace.

## Attach Faults

Attach mode is observe-only by default. It will not mutate existing resources
unless `runtime.faults` is configured and either the target namespace or a
selected workload has this label or annotation:

```yaml
chamber.ampule.dev/allow-faults: "true"
```

Supported first-slice attach faults are:

```yaml
runtime:
  faults:
    - type: pod_kill
    - type: deployment_scale
      workload: my-service
      replicas: 0
```

Every mutation records rollback evidence in `evidence/rollback.json`.
Deployment scale faults restore the original replica count in a `finally` path.
If rollback cannot be verified, the run is marked failed and rollback evidence
includes manual remediation commands.

## Agent Selection

Reviewed configs can skip selected agent roles:

```yaml
agents:
  mode: live
  exclude:
    - onboarding-agent
```

The same override is available from the CLI:

```bash
uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context> \
  --agents-exclude onboarding-agent
```

This is useful for services that are already deployed into the target cluster
or when the local source repository is unavailable. Excluded roles are recorded
in `run-metadata.json`.

## Troubleshooting

Context mismatch: pass `--context <kube-context>` and confirm it with
`kubectl config get-contexts`. Generic Kubernetes mode does not silently rely
on the current context.

RBAC failure: review `.chamber/runs/<run-id>/evidence/preflight.json` for the
failed `kubectl auth can-i` check, then grant the missing namespace-scoped
permission or choose another non-production context.

Missing Prometheus: pass `--prometheus-url` or set `runtime.prometheusUrl`.
Prometheus is optional only when the assessment does not need metrics evidence.

Image pull errors: update `deployment.images.replacements` so adapted manifests
reference images already pullable by the target cluster.

Readiness failures: inspect `evidence/kubernetes-commands.json` for rollout,
endpoint, pod, event, and log command output.

Cleanup failure: rerun cleanup manually using the namespace in
`run-metadata.json`. Only delete resources in the chamber-owned namespace or
resources carrying the recorded chamber labels.
