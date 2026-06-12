# Local Development Commands

## Environment

```bash
uv sync
```

## Validate Scenarios

```bash
make validate-scenarios
```

## Run Tests

```bash
make test
```

## Quality Checks

```bash
make format
make lint
make typecheck
make coverage
make check
```

## Run Sample Service With Docker

```bash
docker compose -f examples/sample-service/docker-compose.yml up --build
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/dependency
```

Use Docker first for local contract and sample-service validation. Kubernetes
namespace provisioning begins in phase 02.
