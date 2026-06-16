# Phase 10 External Service Onboarding Dry Run

Run id used for planner inspection: `phase10-dry-run`.

This artifact records one preset built on the generic Phase 10 raw-Kubernetes
onboarding contract. The reusable contract accepts operator-provided manifest
paths, workload roles, image mappings, readiness intent, redacted config,
external dependency policies, traffic journeys, and follow-up evidence checks.

## Source

- External repository: operator-provided working tree outside this repository
- Source snapshot: external working tree
- API Dockerfile: `docker/Dockerfile.api`
- Worker Dockerfile: `docker/Dockerfile.worker`
- API manifest source: `deployment/deployment.yaml`
- Worker manifest source: `k8s/keda/scaledjob.yaml`
- Config source: `k8s/translation-configmap.yaml`
- Dependency sources:
  - `deployment/rabbitmq-deployment.yaml`
  - `deployment/redis-deployment.yaml`

## Planned Namespace

`chamber-external-translation-<run-suffix>`

All adapted resources carry Ampule Chamber run labels and cleanup selectors.

## Image Builds

- `translation-api`: `ampule/external-translation-api:local`
  - build: `docker build -f <external-repo>/docker/Dockerfile.api -t ampule/external-translation-api:local <external-repo>`
  - kind load: `kind load docker-image ampule/external-translation-api:local --name ampule-chamber`
- `translation-worker`: `ampule/external-translation-worker:local`
  - build: `docker build -f <external-repo>/docker/Dockerfile.worker -t ampule/external-translation-worker:local <external-repo>`
  - kind load: `kind load docker-image ampule/external-translation-worker:local --name ampule-chamber`

## Adapted Workloads

- API: `Deployment/translation-service`, `Service/translation-service`, port `8887`
- Worker: `ScaledJob/translation-worker`, metrics port `8001`
- RabbitMQ: `Deployment/rabbitmq`, `Service/rabbitmq`, ports `5672` and `15672`
- Redis: `Deployment/redis-master`, `Service/redis-master`, port `6379`

The KEDA `ScaledJob` pod template is rewritten in-place for chamber labels,
local image replacement, `IfNotPresent` pull policy, default resource bounds,
and inline secret-env redaction. Operators can also provide plain
`Deployment`, `Job`, or `CronJob` worker manifests through the generic planner.

## Redacted Configuration

- `LLM_API_KEY`: `<missing>` in this dry run, required for live OpenAI evidence.
- `LLM_BACKEND`: `openai`
- `LLM_ENDPOINT`: `https://api.openai.com/v1`
- `MODEL_NAME`: `gpt-4.1-mini`
- `EXTERNAL_TRANSLATION_REDIS_PASSWORD`: `<generated-at-live-run>`

No secret values are written to this artifact, generated manifests, reports, or
run metadata.

## External Dependency Policy

- `openai`
  - endpoint: `https://api.openai.com/v1`
  - required environment: `LLM_API_KEY`
  - redaction: only key presence and endpoint/model metadata may be recorded

## Traffic Journey

- Method: `POST`
- Path: `/translations`
- Expected status: `202`
- Body:

```json
{
  "task_id": "ampule-phase10-text",
  "text": "Hello from Ampule Chamber.",
  "language_target": "Thai",
  "priority": 5
}
```

Follow-up evidence should check `/status/ampule-phase10-text`, worker logs,
RabbitMQ queue state, Redis status data, or service events for translation
progress.

## Follow-Up And Evidence Attribution

- `translation-status`: HTTP status/result check at
  `/status/ampule-phase10-text`
- `worker-logs`: Kubernetes logs from the worker resource
- `queue-state`: RabbitMQ queue depth or drain evidence
- Workload evidence: pod status and logs attributed to API, worker, Redis, and
  RabbitMQ resources
- External dependency evidence: OpenAI endpoint/model metadata and redacted key
  presence only

## Readiness Checks

- RabbitMQ service endpoints for AMQP and management ports.
- Redis authenticated `PING`.
- Translation API `/health`.
- Translation worker startup logs proving queue consumer startup.

## Blockers

- `LLM_API_KEY` is required for live onboarding evidence.
- `PROMETHEUS_URL` is required for the current live Prometheus evidence path.

## Limitations

- Document-service path is intentionally ignored; direct text translation only.
- External repository is read-only and provided by the operator at run time.
- Raw Kubernetes YAML is supported in this pass; Helm and Kustomize must be
  rendered before onboarding.
- This artifact records dry-run planning evidence. A live run should be
  recorded after images are built, loaded into `kind-ampule-chamber`, and
  `LLM_API_KEY` and `PROMETHEUS_URL` are available in the local environment.
