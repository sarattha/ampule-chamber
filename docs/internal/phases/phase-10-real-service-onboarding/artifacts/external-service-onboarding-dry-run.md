# Phase 10 External Service Onboarding Dry Run

Run id used for planner inspection: `phase10-dry-run`.

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
- Worker: `Deployment/translation-worker`, metrics port `8001`
- RabbitMQ: `Deployment/rabbitmq`, `Service/rabbitmq`, ports `5672` and `15672`
- Redis: `Deployment/redis-master`, `Service/redis-master`, port `6379`

A KEDA `ScaledJob` worker manifest is adapted to a bounded chamber-owned worker
`Deployment` for local `kind`; installing KEDA is not required for this first
onboarding slice.

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

## Readiness Checks

- RabbitMQ service endpoints for AMQP and management ports.
- Redis authenticated `PING`.
- Translation API `/health`.
- Translation worker startup logs proving queue consumer startup.

## Blockers

- `LLM_API_KEY` is required for live OpenAI translation evidence.

## Limitations

- Document-service path is intentionally ignored; direct text translation only.
- External repository is read-only and provided by the operator at run time.
- This artifact records dry-run planning evidence. A live run should be
  recorded after images are built, loaded into `kind-ampule-chamber`, and
  `LLM_API_KEY` is available in the local environment.
