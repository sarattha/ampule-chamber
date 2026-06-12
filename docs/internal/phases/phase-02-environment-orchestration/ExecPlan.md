# ExecPlan: Phase 02 Environment Orchestration

## Description

Build the environment orchestration layer that prepares, validates, and tears
down isolated Kubernetes chambers for target services. This phase establishes
the bridge between scenario definitions and real runtime infrastructure.

## Task Checklist

- [x] Define environment provider interfaces for local Kubernetes and future
      AKS.
- [x] Implement namespace creation and labeling for chamber runs.
- [x] Implement manifest, Helm, or Kustomize application path.
- [x] Add target service readiness checks.
- [x] Collect baseline pod status, events, service endpoints, and startup logs.
- [x] Add cleanup and teardown safeguards.
- [x] Add fixtures or tests for dry-run environment planning.
- [x] Document required Kubernetes permissions.

## Evaluation Metrics

- A dry-run environment plan can be generated from a sample scenario.
- Namespace naming, labels, and run identifiers are deterministic and traceable.
- Readiness checks report specific failure reasons, not only pass/fail.
- Cleanup identifies all resources created by a chamber run.
- Tests cover successful planning and at least one deployment/readiness failure
  path.

## Acceptance Criteria

- A target service can be deployed into an isolated namespace in local
  Kubernetes or a documented dry-run path proves the generated actions.
- The environment layer produces metadata usable by observability and reporting
  phases.
- Cleanup behavior is documented and test-covered enough to avoid orphaned
  chamber resources in normal failures.

## Progress

- [x] Added `chamber/environment/` dry-run planning contracts and `kind`
      provider implementation.
- [x] Generate Kubernetes Namespace, Deployment, and Service manifests from the
      phase 01 scenario/sample-service contract without changing public
      scenario YAML.
- [x] Added deterministic run labels, Kubernetes-safe names, metadata, readiness
      checks, ordered actions, and cleanup selectors.
- [x] Added planning failure reasons for unsupported providers, missing images,
      missing ports, invalid replicas, invalid resources, and unavailable
      manifest data.
- [x] Added environment planning tests for successful dry-run generation,
      deterministic naming/labels, Deployment/Service manifest shape, cleanup,
      and failure paths.
- [x] Documented live Kubernetes permission expectations in
      `artifacts/kubernetes-permissions.md`.

## Surprises And Discoveries

- Phase 01 scenarios are Docker-first (`environment.provider: docker`), while
  phase 02 targets Kubernetes. The implementation treats the Docker-first
  scenario as the source service contract and uses `kind` as the chamber
  execution provider so the public YAML stays unchanged.
- The first `make check` run passed format, lint, typecheck, tests, scenario
  validation, and build up to coverage, but total coverage was 89% against the
  90% threshold. Additional targeted planning failure tests raised total
  coverage to 93%.
- Server-side dry-run against a real kind API caught an invalid top-level
  `chamberDryRun` field in the generated Service manifest. The deployment
  reference now lives in the Kubernetes-valid
  `chamber.ampule.dev/deployment-name` annotation.

## Decision Log

- Chose `kind` as the only implemented environment provider for phase 02.
  `aks` and `existing_kubernetes` are explicitly reserved behind the provider
  interface for later phases.
- Chose generated manifests as the MVP deployment input: Namespace,
  Deployment, and Service are rendered from scenario target/environment fields.
- Chose dry-run planning as the acceptance proof. Live cluster execution is not
  required for this phase, but ordered actions and permission notes are ready
  for future live apply/delete support.
- Chose deterministic Kubernetes names with a stable scenario/run hash suffix
  and traceability labels for cleanup and later evidence collection.

## Outcomes And Retrospective

- The environment layer can produce a dry-run `EnvironmentPlan` from
  `scenarios/baseline-health.yaml` with ordered provision, deploy, verify,
  collect, and cleanup actions.
- Generated manifests include a chamber-owned namespace, a Deployment with
  resource requests/limits and HTTP health/readiness probes, and a ClusterIP
  Service.
- Metadata includes run id, scenario id, provider, source provider, namespace,
  labels, rendered resource names, readiness checks, and cleanup selectors for
  future observability and reporting phases.
- Acceptance evidence:
  - `make test` passes 24 tests.
  - `make validate-scenarios` validates all five scenario files.
  - `make check` passes format, lint, typecheck, tests, coverage, scenario
    validation, and package build.
  - `kind create cluster --name ampule-chamber --wait 120s` creates a local
    Kubernetes v1.36.1 control plane.
  - `kubectl apply --context kind-ampule-chamber` creates the generated
    namespace, and `kubectl apply --dry-run=server` validates the generated
    Deployment and Service against the live API.
