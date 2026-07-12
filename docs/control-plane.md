# Control Plane UI

The control plane is a server-rendered interface over the same
application services and safety checks used by the CLI. It does not introduce a
second execution engine.

## Start locally

```bash
uv sync --extra ui
uv run ampule-chamber ui
```

The default address is `http://127.0.0.1:8765`. Use `--no-open` to suppress
browser launch or `--workspace <path>` to select another run store. Binding to a
non-loopback host is rejected unless `--allow-remote` is explicitly supplied
and `AMPULE_CHAMBER_ADMIN_TOKEN` contains a valid `op_live_` operator token.

## Deploy in Kubernetes

The supported in-cluster layout uses one control-plane replica, a ServiceAccount,
namespace-scoped RBAC, a ClusterIP Service, and a PVC-backed workspace. The UI
uses its pod identity and an in-cluster kubeconfig; it does not require a source
repository to discover and attach to existing Services.

Generate an operator token and install the Helm chart. Only declare reviewed,
non-production target namespaces:

```bash
export AMPULE_ADMIN_TOKEN="op_live_$(openssl rand -hex 24)"

helm upgrade --install ampule deploy/helm/ampule-chamber \
  --namespace ampule-system \
  --create-namespace \
  --set-string auth.adminToken="$AMPULE_ADMIN_TOKEN" \
  --set 'discovery.namespaces[0]=chamber-target'
```

For production GitOps, create the Secret separately and avoid putting the token
in Helm release values:

```bash
kubectl create namespace ampule-system
kubectl -n ampule-system create secret generic ampule-auth \
  --from-literal=admin-token="$AMPULE_ADMIN_TOKEN"

helm upgrade --install ampule deploy/helm/ampule-chamber \
  --namespace ampule-system \
  --set auth.existingSecret=ampule-auth \
  --set 'discovery.namespaces[0]=chamber-target'
```

The token must start with `op_live_` and contain at least 24 characters. The
server keeps only its SHA-256 digest and issues an HttpOnly session cookie after
sign-in. The same token is accepted as `Authorization: Bearer` for API clients.

![Admin-token sign-in](assets/screenshots/control-plane-kubernetes/01-admin-token-sign-in.jpg)

### Open the UI

For operator-only access, use port-forwarding:

```bash
kubectl -n ampule-system port-forward service/ampule-ampule-chamber 8765:8765
```

Then open `http://127.0.0.1:8765` and enter the operator token. For an Ingress,
set `ingress.enabled=true`, configure `ingress.className`, `ingress.host`, and
`ingress.tls`, and set `auth.secureCookies=true`. Terminate TLS at the Ingress
and restrict the route to trusted operator networks or an identity-aware proxy.

### Discover existing services

Every entry in `discovery.namespaces` creates read-focused Role and RoleBinding
resources in that existing namespace. The discovery API lists Services,
Deployments, and StatefulSets only there, and matches a Service to its backing
workload by selector. Operators may still type a Service or workload name when
automatic matching is not possible.

![Discover a Service and its backing workload](assets/screenshots/control-plane-kubernetes/02-namespace-service-discovery.jpg)

The default RBAC can read workloads, endpoints, events, logs, and create the pod
port-forward subresource needed by traffic execution. It cannot delete pods or
scale workloads. Set `rbac.allowFaults=true` only when controlled attach-mode
faults are intended and the target workloads carry the Chamber allow-list label
or annotation.

### Storage and upgrades

The PVC stores `runs.db`, plans, run metadata, logs, evidence, reports, and
exports under `/data/.chamber`. The Deployment deliberately uses one replica
and a `Recreate` strategy because the current job manager is in-memory and the
SQLite index is PVC-backed. Configure `persistence.storageClass`,
`persistence.size`, or `persistence.existingClaim` to fit the cluster. Database
support and horizontal replicas are deferred to a later phase. Helm keeps a
chart-created claim on uninstall by default; set `persistence.retain=false`
only when uninstalling should also delete the claim.

The raw alternative is `deploy/kubernetes/ampule-chamber.yaml`. Replace the
placeholder admin token and every `chamber-target` namespace before applying:

```bash
kubectl apply -f deploy/kubernetes/ampule-chamber.yaml
```

Prefer Helm when several namespaces, an existing Secret or PVC, an Ingress, or
custom security/resource settings are needed.

## Core journey

1. **Target:** choose a local repository, or select a running Kubernetes
   service and provide the Service plus its backing Deployment or StatefulSet
   without a repository. Service and workload names are kept separate.
2. **Environment:** choose a local artifact assessment, isolated Kubernetes
   deploy, or attach to an existing non-production namespace. Live runs require
   an explicit context.
3. **Exercise:** add one or more HTTP or Relayna traffic journeys. Each journey
   has its own method, path, expected HTTP status, body, and load model. HTTP
   journeys support reusable profiles, custom stages, or fixed iterations;
   Relayna journeys additionally configure task ID extraction, SSE terminal
   statuses, concurrency, iterations, and completion timeout. One assessment
   cannot mix HTTP and Relayna adapters. Optionally choose an attach-only,
   allow-listed fault.
4. **Review:** inspect the safety receipt and generated plan before any live
   action.
5. **Run:** observe state and evidence updates. Cancellation sends an interrupt
   to the workflow so rollback, cleanup, result persistence, and reporting can
   complete.
6. **Results:** review readiness, coverage, findings, timeline, registered
   evidence, resolved configuration, and agent output. Compare compatible runs
   or export HTML, Markdown, or JSON.

## Evidence and readiness

Every new run has a canonical `run.json`, append-only `events.jsonl`,
`result.json`, and `evidence/manifest.json`. Each evidence entry is bound to the
run and SHA-256 digest. Evidence download rejects unknown, cross-run, missing,
or modified entries.

Relayna lifecycle runs additionally persist `relayna-summary.json` with one
bounded record per task. The UI defaults each new Relayna journey to one
iteration because task execution may invoke costly downstream services.

Readiness is scored only after required live Kubernetes, traffic, and
mode-specific evidence is present. Local-only, partial, cancelled, and failed
runs remain explicit and do not receive a misleading readiness score.

## HTTP boundary

The local API is versioned under `/api/v1` and covers capability discovery,
repository inspection, plan creation, asynchronous run control, SSE run events,
result retrieval, evidence access, reports, and comparison. Mutating requests
require a same-site CSRF token. Responses include a strict content security
policy, anti-framing, no-sniff, and no-referrer headers.

## Real kind example

The in-cluster release path was exercised in Kind with a Helm-installed Chamber
pod attaching to an already deployed translation Service. Token sign-in,
namespace discovery, PVC persistence, Service-to-Deployment matching, the
Kubernetes preflight, port-forwarded k6 traffic, Prometheus collection, and
report generation all ran from the pod. The acceptance run recorded 3,722
passing checks, zero failed checks, and remained available after a Deployment
restart.

![Completed in-cluster Kind assessment](assets/screenshots/control-plane-kubernetes/03-kind-assessment-result.jpg)

See `examples/sample-service/chamber-kind.yaml` for isolated deploy mode and
`examples/sample-service/chamber-attach.yaml` for attach mode. The sample README
contains the image build and kind load commands.
