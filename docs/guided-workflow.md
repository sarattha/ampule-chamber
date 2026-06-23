# Guided Workflow

Ampule Chamber supports an explicit staged workflow for teams that want to
review generated assumptions before running an assessment.

## Initialize

```bash
uv run ampule-chamber init
```

Creates local workspace defaults under `.chamber/`.

## Onboard

```bash
uv run ampule-chamber onboard --repo ../target-service --output chamber.yaml
```

The onboarding step inspects the target repository read-only and drafts a
reviewable `chamber.yaml`. It does not mutate the target repository.

## Plan

```bash
uv run ampule-chamber plan --config chamber.yaml
```

Planning validates the config, adapts manifests into chamber-owned resources,
and writes `plan.json` plus `adapted-manifests/`.

## Assess

```bash
uv run ampule-chamber assess --config chamber.yaml --mode local
```

Local assessment writes evidence, findings, agent output, metadata, and a
report from the standard run directory.

For reviewed configs whose images are already pullable by a non-production
cluster, run generic Kubernetes assessment with an explicit context:

```bash
uv run ampule-chamber assess \
  --config chamber.yaml \
  --mode kubernetes \
  --context <kube-context> \
  --prometheus-url <prometheus-url>
```

Generic Kubernetes mode uses `kubectl`, starts with preflight checks, and keeps
all applied resources inside a chamber-owned namespace. See
[Generic Kubernetes Assessment](kubernetes-assessment.md).

## Report

```bash
uv run ampule-chamber report --run .chamber/runs/<run-id>
```

Reports can be regenerated from archived run directories without the original
source repository.
