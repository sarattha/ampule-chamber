# Ampule Chamber

Version: 1.4.1

Production-ready release for agent-driven reliability testing of Kubernetes
services before production.

Ampule Chamber deploys a target service into an isolated chamber environment,
applies realistic traffic, injects controlled failure modes, collects runtime
evidence, and produces evidence-backed reliability reports. It is built for
teams that need to answer:

> What can make this service fail in a real production-like environment, and
> what evidence supports that conclusion?

## Release Status

Ampule Chamber `1.4.1` lets the control-plane UI attach directly to an existing
Kubernetes Service and its backing Deployment or StatefulSet without requiring
a local repository. The CLI and UI share the same safety validation,
application services, and durable run store.

## What It Tests

- Kubernetes OOMKilled events and restart loops
- CPU throttling, memory growth, and unsafe resource limits
- p95/p99 latency spikes under load
- Dependency timeouts, 429/500 responses, and service degradation
- Retry storms and cascading failure amplification
- Queue backlog growth and failure to recover
- Readiness checks passing while real user journeys fail
- Production incident patterns reproduced in a controlled chamber

## Quick Start

```bash
uv sync --group dev
uv run ampule-chamber assess --repo ../target-service
```

The one-command assessment writes:

```text
.chamber/runs/<run-id>/
  chamber.yaml
  plan.json
  run-metadata.json
  adapted-manifests/
  evidence/
  findings.json
  agent/
  report.md
```

## Guided Workflow

Use the explicit staged workflow when generated assumptions need review before
assessment:

```bash
uv run ampule-chamber init
uv run ampule-chamber onboard --repo ../target-service --output chamber.yaml
uv run ampule-chamber plan --config chamber.yaml
uv run ampule-chamber assess --config chamber.yaml --mode local
uv run ampule-chamber report --run .chamber/runs/<run-id>
```

For a modern local UI over the same workflow, install the `ui` extra (the dev
group already includes it) and start the loopback-only control plane:

```bash
uv sync --extra ui
uv run ampule-chamber ui
```

Open `http://127.0.0.1:8765` if browser launch is disabled. Use the wizard to
inspect a repository, select local, isolated deploy, or attach mode, choose a
traffic profile and optional attach fault template, review the generated plan,
then explicitly start execution. Remote binding is rejected unless
`--allow-remote` is supplied.

For a reviewed config and a non-production Kubernetes context, generic
Kubernetes deploy mode uses `kubectl` and chamber-owned namespaces:

```bash
uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context> \
  --prometheus-url <prometheus-url>
```

Scenario-based live kind runs remain available:

```bash
uv run ampule-chamber run \
  --scenario scenarios/baseline-health.yaml \
  --output .chamber/reports/live-baseline-report.md \
  --prometheus-url http://127.0.0.1:9090
```

## Agent Modes

```yaml
agents:
  mode: offline
  exclude:
    - onboarding-agent
```

- `off`: skip agent output.
- `offline`: deterministic, CI-safe agent output.
- `live`: OpenAI Agents SDK execution with `OPENAI_API_KEY`.

All agent output must cite supplied evidence IDs. Unsupported citations fail
validation before they can be persisted or rendered into reports.
Use `agents.exclude` or `--agents-exclude <agent-name>` when a reviewed
Kubernetes config does not need a specific role, such as onboarding a service
that is already deployed and has no local source repository.

## Documentation

Public docs are built with MkDocs:

```bash
uv run mkdocs build --strict
```

Documentation entry points:

- `docs/index.md`
- `docs/getting-started.md`
- `docs/guided-workflow.md`
- `docs/control-plane.md`
- `docs/kubernetes-assessment.md`
- `docs/agent-pipeline.md`
- `docs/release-process.md`

## Development

```bash
make check
```

`make check` runs formatting, linting, static type checks, tests, coverage,
scenario validation, release metadata validation, docs build, and package build.

## Repository Layout

```text
ampule-chamber/
├── chamber/
│   ├── orchestrator/
│   ├── environment/
│   ├── load/
│   ├── chaos/
│   ├── observability/
│   ├── analysis/
│   ├── onboarding/
│   ├── agents/
│   ├── report/
│   ├── application/
│   ├── control_plane/
│   └── runs/
├── docs/
├── agents/
├── scenarios/
├── examples/
├── scripts/
└── tests/
```

## Release Notes

See `CHANGELOG.md` for release history. GitHub release notes are extracted from
the matching changelog section during tag releases.
