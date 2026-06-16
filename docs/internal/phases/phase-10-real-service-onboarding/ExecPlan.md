# ExecPlan: Phase 10 Real Service Onboarding

## Description

Build the first bring-your-own-service onboarding workflow for Ampule Chamber.
This phase converts a real repository with existing Dockerfiles, Kubernetes
manifests, secrets, runtime dependencies, and POST-based traffic into a safe
local chamber run.

The first target shape is an external translation service repository supplied
by the operator. The phase should adapt enough of that repository shape to run
a text-only translation path in `kind` with chamber-owned Redis and RabbitMQ, a
worker process, explicit OpenAI external dependency configuration, evidence
collection, and a report.

## Task Checklist

- [ ] Review phase 06 live runner, phase 09 multi-service implementation,
      `docs/internal/PROJECT_DESIGN.md`, and the external service repository
      shape.
- [ ] Define a real-service onboarding contract for repository path, images,
      manifests, workloads, dependencies, secrets, config, traffic journeys,
      and external dependency policy.
- [ ] Decide whether the contract is represented as a new scenario extension,
      a separate onboarding file, or a generated phase artifact.
- [ ] Add validation for required onboarding fields and redaction rules.
- [ ] Implement local image build and kind-load planning for multiple images.
- [ ] Add chamber-safe Kubernetes manifest adaptation for Namespace,
      Deployment, Service, ConfigMap, Secret, labels, image references,
      resources, probes, and cleanup selectors.
- [ ] Support chamber-owned Redis and RabbitMQ dependency workloads with
      service-specific readiness checks.
- [ ] Support Secret and ConfigMap injection without writing secret values into
      generated artifacts, reports, logs, or metadata.
- [ ] Add k6 POST JSON traffic journeys, including direct-text
      `POST /translations` support for an external translation service.
- [ ] Add optional follow-up checks for task status, event feed, queue depth, or
      worker logs so runs can prove pipeline progress beyond API acceptance.
- [ ] Add external dependency policy handling for OpenAI or similar providers:
      allowed endpoints, required environment variables, timeout budgets, and
      report limitations.
- [ ] Extend evidence attribution for API, worker, Redis, RabbitMQ, queue
      state, application logs, and external dependency diagnostics.
- [ ] Extend reports with onboarding summary, adapted workload list, redacted
      config summary, external dependencies, queue evidence, and real-service
      retest guidance.
- [ ] Add focused tests for onboarding validation, manifest adaptation,
      redaction, image mapping, POST traffic generation, readiness checks, and
      report output.
- [ ] Add an external-service dry-run artifact showing planned images, workloads,
      dependencies, traffic, evidence, and blockers.
- [ ] Run a live external-service text-only chamber scenario when Redis,
      RabbitMQ, images, Prometheus, and OpenAI credentials are available.
- [ ] Record acceptance evidence, limitations, and follow-up work in this plan.

## Evaluation Metrics

- A real-service onboarding file can describe at least one API workload, one
  worker workload, Redis, RabbitMQ, local image builds, runtime config, redacted
  secrets, and POST traffic.
- The chamber can render or adapt Kubernetes resources into an isolated
  namespace with chamber labels and cleanup selectors.
- No secret values appear in generated artifacts, reports, command transcripts,
  or run metadata.
- The runner refuses live execution if required external credentials or
  explicitly allowed external endpoints are missing.
- Redis and RabbitMQ readiness checks verify service endpoints and
  application-level availability before API traffic begins.
- k6 can send POST JSON traffic to the API target and export request count,
  failure rate, p95 latency, and status-check results.
- Evidence identifies which workload produced each signal: API, worker, Redis,
  RabbitMQ, or external dependency diagnostic.
- The report distinguishes chamber-owned dependencies from external providers.
- Cleanup removes all chamber-owned adapted resources in the success and
  failure paths.
- `make check` passes after implementation.

## Acceptance Criteria

- An external-service onboarding artifact or scenario can build or reference
  local images, deploy API, worker, Redis, and RabbitMQ into a local chamber
  namespace, and configure OpenAI through a redacted secret.
- The live or documented dry-run path uses direct-text `POST /translations`
  traffic and does not require the document service.
- The chamber verifies Redis and RabbitMQ availability before exercising the
  API.
- The run captures Kubernetes evidence and application evidence from API,
  worker, Redis, and RabbitMQ.
- If OpenAI credentials are available, the run proves translation pipeline
  progress through task status, worker logs, queue state, or result evidence.
- If OpenAI credentials are unavailable, the run stops before live translation
  traffic and records the blocker explicitly.
- The generated report includes adapted workload inventory, redacted
  configuration summary, external dependency policy, evidence references,
  cleanup status, limitations, and retest guidance.

## Progress

- [x] Phase directory created.
- [x] Reviewed phase 06 live runner, phase 09 multi-service implementation,
      `docs/internal/PROJECT_DESIGN.md`, and an external translation service
      repository shape.
- [x] Defined Phase 10 implementation branch as
      `codex/phase-10-real-service-onboarding`.
- [x] Added `chamber/onboarding/` real-service onboarding contracts for external
      repository path, image builds, adapted workloads, redacted config,
      external dependency policy, readiness checks, traffic journey, blockers,
      and limitations.
- [x] Added redacted external-service manifest planning from an
      operator-provided working tree.
- [x] Added chamber-owned Redis and RabbitMQ dependency workload planning.
- [x] Adapted an external worker KEDA `ScaledJob` into a bounded local worker
      `Deployment` for the first `kind` onboarding slice.
- [x] Added `scenarios/external-text-translation.yaml` for direct-text
      `POST /translations` traffic.
- [x] Extended k6 traffic planning to support POST JSON bodies and expected
      status checks while preserving existing GET behavior.
- [x] Extended report rendering with optional onboarding summary, adapted
      workload, redacted config, and external dependency sections.
- [x] Added focused Phase 10 tests for onboarding validation, secret redaction,
      env preflight, image/kind-load planning, manifest adaptation, POST k6
      generation, readiness checks, and report output.
- [x] Added dry-run evidence in
      `artifacts/external-service-onboarding-dry-run.md`.

## Surprises And Discoveries

- Translation services with a direct-text path can avoid the document-service
  path when traffic submits `text` instead of `document_id`, but full
  translation still requires a worker and an LLM provider.
- Phase 09 is implemented, but it is intentionally optimized for controlled
  sample dependencies. External repositories require generic real-service
  onboarding features: manifest adaptation, multiple images, Redis/RabbitMQ
  readiness, redacted secrets, and POST traffic.
- The external repository is treated as operator-provided input; Ampule Chamber
  records a generic working-tree source reference instead of naming that
  repository in tracked files.
- The external worker manifest may be a KEDA `ScaledJob`; the first local chamber slice
  adapts it into a single worker `Deployment` so KEDA installation is not a
  prerequisite.

## Decision Log

- Chose a new phase instead of expanding phase 09 because phase 09 is already
  accepted and focused on controlled dependency graph behavior, while real
  external services need bring-your-own-service manifest and secret handling.
- Chose a generic external translation-service shape as the first acceptance
  target because it exercises realistic queue, worker, dependency, external
  API, and POST traffic requirements without requiring document ingestion for
  the first slice.
- Chose text-only translation as the first live journey so the document service
  remains out of scope until the chamber can model additional internal APIs.
- Chose OpenAI as an explicit external dependency rather than a chamber-owned
  dependency because the first goal is onboarding the service under realistic
  provider configuration, not simulating LLM quality or latency.
- Chose a new `chamber/onboarding/` package rather than extending the existing
  generated sample-service environment planner because Phase 10 needs
  real-repository manifest adaptation, image build planning, and redacted
  external dependency configuration.
- Chose environment-sourced `LLM_API_KEY` as the required live OpenAI secret.
  Artifacts record only presence or absence, never the key value.
- Chose to keep the document-service path out of the scenario because direct
  text translation exercises the normal API, RabbitMQ, Redis, worker, and LLM
  path without requiring document ingestion.

## Outcomes And Retrospective

- Initial implementation added deterministic dry-run planning, scenario
  validation, POST k6 generation, report sections, and focused tests.
- Live OpenAI-backed external-service evidence remains blocked until
  `LLM_API_KEY` is available and external service images are built and loaded
  into `kind-ampule-chamber`.
- Live prerequisite check on this machine after implementation:
  - `docker`, `kind`, `kubectl`, and `k6` are present.
  - Current Kubernetes context is `kind-ampule-chamber`.
  - `kind get clusters` includes `ampule-chamber`.
  - `LLM_API_KEY` is missing, so OpenAI-backed translation evidence cannot run.
  - `PROMETHEUS_URL` is missing, so the current live evidence collection path
    cannot collect required Prometheus metrics.
- Acceptance evidence recorded so far:
  - `uv run python -m unittest tests.test_phase10_onboarding` passed.
  - `uv run python scripts/validate_scenarios.py` passed for 7 scenarios.
  - `uv run ruff format --check chamber tests scripts` passed.
  - `uv run ruff check chamber tests scripts` passed.
  - `uv run mypy` passed.
  - `make check` passed, including format, lint, typecheck, 83 tests,
    coverage at the 90% threshold, scenario validation, and package build.
