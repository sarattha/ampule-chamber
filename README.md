# Ampule Chamber

Agent-driven reliability testing for Kubernetes services before production.

Ampule Chamber is a production-like testing chamber for validating services before deployment. It deploys a target service into an isolated Kubernetes environment, stresses it with realistic traffic, injects controlled failures, observes runtime behavior, and produces evidence-backed reliability reports.

The project is based on a zero-trust view of service readiness: do not assume a service is safe because unit tests pass, staging starts cleanly, or health checks are green. A service becomes safer only after it survives repeatable experiments that exercise load, dependency failure, resource pressure, recovery behavior, and observability.

## What It Tests

- Kubernetes OOMKilled events and restart loops
- CPU throttling, memory growth, and unsafe resource limits
- p95/p99 latency spikes under load
- Dependency timeouts, 429/500 responses, and service degradation
- Retry storms and cascading failure amplification
- Queue backlog growth and failure to recover
- Readiness checks passing while real user journeys fail
- Production incident patterns reproduced in a controlled chamber

## Core Workflow

1. Intake a service repository, deployment manifests, traffic profile, and dependency map.
2. Provision an isolated Kubernetes namespace or ephemeral cluster.
3. Run baseline startup, readiness, traffic, logs, metrics, and trace checks.
4. Escalate load with normal, peak, burst, and soak traffic.
5. Inject controlled faults such as pod kills, latency, dependency errors, DNS failure, CPU pressure, or memory pressure.
6. Validate recovery after the original fault is removed.
7. Generate a reliability report with evidence, root-cause hypotheses, severity, confidence, and remediation guidance.

## MVP Scope

The initial MVP focuses on one service deployed into AKS or local Kubernetes.

- Deploy target service into an isolated namespace.
- Run baseline health checks.
- Run load tests with k6 or Locust.
- Collect Kubernetes events, pod status, logs, and Prometheus metrics.
- Detect OOMKilled events, restart loops, high memory, high CPU, throttling, and high latency.
- Inject a simple dependency failure.
- Generate a markdown reliability report.

## Repository Layout

```text
ampule-chamber/
├── docs/
│   └── internal/
│       └── PROJECT_DESIGN.md
├── chamber/
│   ├── orchestrator/
│   ├── environment/
│   ├── load/
│   ├── chaos/
│   ├── observability/
│   ├── analysis/
│   └── report/
├── agents/
├── scenarios/
├── examples/
├── scripts/
└── tests/
```

## Design Document

The main project design is stored as an internal document:

- `docs/internal/PROJECT_DESIGN.md`

## Status

This repository is in the project design and MVP planning stage.
