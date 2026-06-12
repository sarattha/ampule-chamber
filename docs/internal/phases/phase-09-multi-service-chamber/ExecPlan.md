# ExecPlan: Phase 09 Multi-Service Chamber

## Description

Build the first multi-service chamber workflow for Ampule Chamber. This phase
adds dependency graph modeling, controlled dependency deployment,
service-to-service traffic, cross-service evidence collection, cascading failure
detection, and report sections that explain dependency-chain behavior.

## Task Checklist

- [ ] Review phase 06 live runner, phase 08 fault injection, and the cascading
      failure requirements in `docs/internal/PROJECT_DESIGN.md`.
- [ ] Extend scenario schema or add a compatible topology contract for
      multi-service dependency graphs.
- [ ] Define dependency graph fields for service identity, protocol, endpoint,
      criticality, resource limits, retry/timeout expectations, and fallback
      behavior.
- [ ] Add a controlled dependency fixture service for local chamber runs.
- [ ] Update environment planning to deploy target and dependency workloads in
      the same isolated namespace.
- [ ] Update readiness checks to validate target and dependency availability.
- [ ] Add traffic journeys that call the target service and force a downstream
      dependency path.
- [ ] Add or adapt fault injection to degrade the dependency service.
- [ ] Collect evidence from target and dependency pods, events, logs, metrics,
      and fault windows.
- [ ] Extend analysis to detect cascading failure, retry amplification, latency
      fan-out, error propagation, dependency saturation, and failed recovery.
- [ ] Extend report rendering with dependency graph and cascading failure
      sections.
- [ ] Add tests and fixtures for dependency graph validation, deployment
      planning, traffic journey generation, cross-service evidence correlation,
      and report output.
- [ ] Record live or dry-run multi-service acceptance evidence in this plan.

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

- [ ] Phase directory created.

## Surprises And Discoveries

- None yet.

## Decision Log

- Chose one target plus one controlled dependency as the first multi-service
  acceptance target to keep the phase small enough to verify live.
- Chose dependency graph modeling before arbitrary manifest ingestion because
  analysis and reporting need stable service identity and criticality metadata.

## Outcomes And Retrospective

- Pending.
