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
Future phases can replace or extend this contract with Kubernetes manifests.
