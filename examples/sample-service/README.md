# Sample Service Contract

This sample service is the phase 01 target contract used by scenario fixtures.
It is intentionally Docker-first so local validation can start before the
Kubernetes environment phase is implemented.

## Runtime

- HTTP port: `8080`
- Health endpoint: `GET /healthz`
- Readiness endpoint: `GET /readyz`
- Work endpoint: `GET /work`
- Dependency endpoint: `GET /dependency`

## Local Commands

```bash
docker compose -f examples/sample-service/docker-compose.yml up --build
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

The service returns JSON responses and uses only the Python standard library.
## Local kind acceptance

The checked-in chamber configuration exercises the same deploy, traffic,
evidence, report, and cleanup path used by the control plane:

```bash
docker build -t ampule/sample-service:kind examples/sample-service
kind load docker-image ampule/sample-service:kind --name ampule-chamber
uv run ampule-chamber assess \
  --config examples/sample-service/chamber-kind.yaml \
  --mode kubernetes \
  --context kind-ampule-chamber
```

The run uses a chamber-owned namespace and removes it after evidence collection.
For an existing non-production deployment, `chamber-attach.yaml` provides a
read-focused attach example. Attach mode leaves the namespace and workload in
place; the operator remains responsible for their lifecycle.
