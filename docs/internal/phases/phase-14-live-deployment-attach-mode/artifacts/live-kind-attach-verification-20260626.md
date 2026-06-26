# Live Kind Attach Verification - 2026-06-26

## Environment

- Docker kind cluster: `ampule-chamber`
- Kubernetes context: `kind-ampule-chamber`
- Namespace: `chamber-external-translation-prep`
- Target workload: `Deployment/translation-service`
- Target service: `Service/translation-service`, port `8887`
- Target image: `ampule/external-translation-api:local`
- Dependency images loaded into kind: `redis:7-alpine`,
  `rabbitmq:4.2.4-management`

## Setup Evidence

- Recreated stopped kind cluster `ampule-chamber`.
- Loaded local translation images:
  `ampule/external-translation-api:local`,
  `ampule/external-translation-worker:local`, and
  `ampule/external-translation-aggregator:local`.
- Loaded dependency images `redis:7-alpine` and
  `rabbitmq:4.2.4-management`.
- Applied `live-attach-manifests.yaml`.
- Verified rollouts for `redis-master`, `rabbitmq`, and
  `translation-service`.
- Verified direct service health through port-forward:
  `curl http://127.0.0.1:18891/health` returned `{"status":"ok"}`.

## Observe-Only Attach Run

Command:

```bash
uv run ampule-chamber assess \
  --config docs/internal/phases/phase-14-live-deployment-attach-mode/artifacts/live-attach-config.yaml \
  --mode kubernetes \
  --context kind-ampule-chamber
```

Run directory:

```text
.chamber/runs/chamber-translation-service-20260626014535/
```

Evidence:

- `run-metadata.json`: `stage=assessed`, `success=true`,
  `runtime_mode=attach`, `cleanup_performed=false`,
  `namespace=chamber-external-translation-prep`.
- `attach-discovery.json`: recorded `mode=attach`,
  `Deployment/translation-service`, `Service/translation-service`, and
  discovered target pods.
- `k6-summary.json`: 2,489 checks passed, 0 failed; 2,489 HTTP requests; HTTP
  failure rate `0`.
- `kubernetes-commands.json`: no `kubectl apply`, namespace delete, workload
  delete, service delete, or scale commands.
- `report.md`: included lifecycle `assessed (mode: attach)` and evidence
  references for `attach-discovery`, `pre-test-state`, `kubernetes-commands`,
  `k6-summary`, and `rollback`.

## Gated Pod-Kill Fault Run

Command:

```bash
uv run ampule-chamber assess \
  --config docs/internal/phases/phase-14-live-deployment-attach-mode/artifacts/live-attach-pod-kill-config.yaml \
  --mode kubernetes \
  --context kind-ampule-chamber
```

Run directory:

```text
.chamber/runs/chamber-translation-service-20260626014759/
```

Evidence:

- Namespace was allow-listed with
  `chamber.ampule.dev/allow-faults: "true"`.
- `run-metadata.json`: `stage=assessed`, `success=true`,
  `runtime_mode=attach`, `cleanup_performed=false`,
  `rollback.verified=true`, `traffic_result.success=true`.
- `rollback.json`: `faults_requested=true`, `verified=true`, with one
  `pod_kill` action and a restore snapshot.
- `k6-summary.json`: 2,452 checks passed, 0 failed; 2,452 HTTP requests; HTTP
  failure rate `0`; p95 duration `9.03945 ms`.
- `kubernetes-commands.json`: mutation commands were limited to:
  `kubectl --context kind-ampule-chamber -n chamber-external-translation-prep
  delete pod translation-service-56996d8b5d-kcqtl` and
  `kubectl --context kind-ampule-chamber -n chamber-external-translation-prep
  wait --for=condition=available deployment/translation-service --timeout=180s`.
- Post-run rollout check confirmed `Deployment/translation-service` was
  successfully rolled out and one replacement pod was `1/1 Running`.

## Result

Live kind verification passed for observe-only attach assessment and gated
pod-kill attach fault rollback against an already-live translation-service
deployment.

## Security CI Follow-Up

After opening PR #18, Semgrep flagged the Redis and RabbitMQ containers in
`live-attach-manifests.yaml` for missing non-root and privilege-escalation
security contexts.

Fix and verification:

- Added pod-level `runAsNonRoot`, `runAsUser`, `runAsGroup`, `fsGroup`, and
  `seccompProfile` settings for Redis and RabbitMQ.
- Added container-level `allowPrivilegeEscalation: false` and dropped all
  Linux capabilities for Redis and RabbitMQ.
- `semgrep scan --config auto
  docs/internal/phases/phase-14-live-deployment-attach-mode/artifacts/live-attach-manifests.yaml`
  completed with 0 findings.
- Reapplied the hardened manifest to `kind-ampule-chamber`; `redis-master`,
  `rabbitmq`, and `translation-service` all rolled out successfully.
- Direct health smoke still returned `{"status":"ok"}`.
- Post-hardening observe-only attach run
  `.chamber/runs/chamber-translation-service-20260626015405/` passed 2,446 k6
  checks with 0 failures, `runtime_mode=attach`, `cleanup_performed=false`, and
  no apply/delete/scale commands in runtime evidence.
