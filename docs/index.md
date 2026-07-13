# Ampule Chamber

Version: 1.6.0

Ampule Chamber is a production-ready reliability testing chamber for Kubernetes
services before production. It deploys services into isolated chamber
environments, applies realistic traffic, injects controlled failure modes,
collects runtime evidence, and produces evidence-backed readiness reports.

## Production-Ready Release

The `1.6.0` release adds reusable, editable control-plane scenarios and Relayna
multipart lifecycle journeys for asynchronous file and image processors.
Scenario ID, source, and revision remain durable while multiple required or
optional uploads use explicit scalar/JSON fields, safe digest evidence, and the
existing parent-task SSE completion semantics. Imported faults remain disabled
until explicitly selected.

## Core Commands

```bash
uv run ampule-chamber init
uv run ampule-chamber onboard --repo ../target-service --output chamber.yaml
uv run ampule-chamber plan --config chamber.yaml
uv run ampule-chamber assess --config chamber.yaml --mode local
uv run ampule-chamber report --run .chamber/runs/<run-id>
uv run ampule-chamber ui
```

For a supported local service repository, the shortcut is:

```bash
uv run ampule-chamber assess --repo ../target-service
```

For a reviewed config and any non-production Kubernetes context reachable
through `kubectl`, use generic Kubernetes deploy or attach mode:

```bash
uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context> \
  --prometheus-url <prometheus-url>
```

See [Generic Kubernetes Assessment](kubernetes-assessment.md) for the safety
model, minimal config, provider credential examples, and troubleshooting.
See [Control Plane UI](control-plane.md) for local setup, screens, API boundary,
and security behavior.

## Evidence Model

Every assessment writes a run directory with:

- `chamber.yaml`
- `plan.json`
- `run-metadata.json`
- `adapted-manifests/`
- `evidence/`
- `findings.json`
- `agent/`
- `report.md`

Reports are generated from those persisted artifacts so runs can be archived,
reviewed, and regenerated.
