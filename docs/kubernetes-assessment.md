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

Use `assess --mode kubernetes` when you have a reviewed `chamber.yaml`, images
that the selected cluster can pull, and permission to create and clean up a
chamber-owned namespace in a real Kubernetes environment.

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

Runs use a chamber-owned namespace whose name starts with `chamber-`. Preflight
checks run before any manifest is applied and verify access to create the
namespace, create common workload resources, read pods, read events, read logs,
and delete the chamber namespace. Cleanup deletes chamber-labeled resources and
the chamber-created namespace.

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
runtime:
  provider: kubernetes
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
```

Store secrets as environment requirements, not plaintext runtime values:

```yaml
runtime:
  requiredEnv:
    - API_TOKEN
  secretEnv:
    - API_TOKEN
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
  findings.json
  agent/
  run-metadata.json
  report.md
```

Regenerate a report from an archived run directory with:

```bash
uv run ampule-chamber report --run .chamber/runs/<run-id>
```

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
