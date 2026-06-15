# ExecPlan: Phase 09 Multi-Service Chamber

## Description

Build the first multi-service chamber workflow for Ampule Chamber. This phase
adds dependency graph modeling, controlled dependency deployment,
service-to-service traffic, cross-service evidence collection, cascading failure
detection, and report sections that explain dependency-chain behavior.

## Task Checklist

- [x] Review phase 06 live runner, phase 08 fault injection, and the cascading
      failure requirements in `docs/internal/PROJECT_DESIGN.md`.
- [x] Extend scenario schema or add a compatible topology contract for
      multi-service dependency graphs.
- [x] Define dependency graph fields for service identity, protocol, endpoint,
      criticality, resource limits, retry/timeout expectations, and fallback
      behavior.
- [x] Add a controlled dependency fixture service for local chamber runs.
- [x] Update environment planning to deploy target and dependency workloads in
      the same isolated namespace.
- [x] Update readiness checks to validate target and dependency availability.
- [x] Add traffic journeys that call the target service and force a downstream
      dependency path.
- [x] Add or adapt fault injection to degrade the dependency service.
- [x] Collect evidence from target and dependency pods, events, logs, metrics,
      and fault windows.
- [x] Extend analysis to detect cascading failure, retry amplification, latency
      fan-out, error propagation, dependency saturation, and failed recovery.
- [x] Extend report rendering with dependency graph and cascading failure
      sections.
- [x] Add tests and fixtures for dependency graph validation, deployment
      planning, traffic journey generation, cross-service evidence correlation,
      and report output.
- [x] Record live or dry-run multi-service acceptance evidence in this plan.

## Evaluation Metrics

- A scenario can describe at least one target service and one controlled
  dependency.
- The environment plan creates traceable Kubernetes resources for both target
  and dependency services.
- Readiness checks report service-specific success or failure.
- Traffic results prove the dependency path was exercised.
- Fault timeline events identify which dependency was degraded and when.
- Evidence artifacts identify the producing service and Kubernetes resource.
- Analysis distinguishes target failure from dependency failure and records
  whether the target contained or amplified the dependency fault.
- Reports include dependency graph, affected services, cascading failure
  analysis, evidence references, and retest guidance.
- `make check` passes after implementation.

## Acceptance Criteria

- A multi-service scenario can deploy a target service plus at least one
  controlled dependency into a local chamber namespace.
- Traffic reaches the target service and causes a real request to the dependency
  service.
- A dependency fault can be injected, observed, removed, and followed by
  recovery validation.
- Evidence is collected from both target and dependency resources.
- The report explains the dependency chain, the observed impact, whether failure
  was amplified or contained, and what evidence supports that conclusion.

## Progress

- [x] Phase directory created.
- [x] Added optional topology validation and richer dependency metadata fields
      while preserving `v1alpha1` compatibility.
- [x] Added `scenarios/multi-service-dependency.yaml`.
- [x] Extended environment planning to render target and controlled dependency
      Deployments/Services in the same namespace.
- [x] Added `ServiceResource` metadata for target/dependency attribution.
- [x] Updated the sample service to run in downstream mode with configurable
      fault status and latency.
- [x] Extended reports with dependency graph and agent/recovery sections.
- [x] Added dry-run evidence in `artifacts/multi-service-dry-run.md`.

## Surprises And Discoveries

- The sample service already had a downstream mode, but target Kubernetes
  deployments needed `DOWNSTREAM_URL` injected to call the generated dependency
  Service.
- Kubernetes `args` replace the image `CMD` when no `command` is set. The
  controlled dependency Deployment now sets `command: ["python",
  "/app/server.py"]` with downstream-mode args.
- Existing Kubernetes evidence selection by chamber labels naturally includes
  target and dependency pods once both workloads share the same run labels.

## Decision Log

- Chose one target plus one controlled dependency as the first multi-service
  acceptance target to keep the phase small enough to verify live.
- Chose dependency graph modeling before arbitrary manifest ingestion because
  analysis and reporting need stable service identity and criticality metadata.
- Chose one target plus one controlled dependency as the live MVP topology and
  kept arbitrary multi-hop graphs out of scope for this phase.
- Chose optional scenario `topology` metadata so existing scenarios remain
  valid while phase 09 scenarios can describe dependency edges explicitly.

## Outcomes And Retrospective

- Multi-service planning, controlled dependency deployment, dependency fault
  planning, evidence attribution fields, and report sections are implemented.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase08_09_live_faults_multiservice.py`
    passes.
  - `uv run python` dry-run inspection confirms target/dependency resources,
    fault actions, and expanded timeline for `multi-service-dependency-001`.
  - Live command:
    `uv run ampule-chamber run --scenario scenarios/multi-service-dependency.yaml --output docs/internal/phases/phase-09-multi-service-chamber/artifacts/live-multi-service-report.md --prometheus-url http://127.0.0.1:9090`.
  - Live run id:
    `chamber-multi-service-dependency-001-20260615154932`.
  - Live namespace:
    `chamber-local-multi-service-dependency-001-8278f4e7`.
  - Live report:
    `docs/internal/phases/phase-09-multi-service-chamber/artifacts/live-multi-service-report.md`.
  - Live artifacts:
    `docs/internal/phases/phase-09-multi-service-chamber/artifacts/chamber-multi-service-dependency-001-20260615154932/`.
  - The live report recorded target-to-dependency graph, dependency fault
    timeline correlation, Kubernetes and Prometheus evidence, k6 summary
    evidence, readiness score `90/100`, lifecycle state `completed`, and
    cleanup status `completed`.
  - Cleanup verification after the live run:
    `kubectl get ns -l app.kubernetes.io/part-of=ampule-chamber` returned
    `No resources found`.
