# ExecPlan: Phase 10 Real Service Onboarding

## Description

Build the first bring-your-own-service onboarding workflow for Ampule Chamber.
This phase converts a real repository with existing Dockerfiles, Kubernetes
manifests, secrets, runtime dependencies, and POST-based traffic into a safe
local chamber run.

The first target is Tara2 Translation Service. The phase should adapt enough of
that repository to run a text-only translation path in `kind` with
chamber-owned Redis and RabbitMQ, a worker process, explicit OpenAI external
dependency configuration, evidence collection, and a report.

## Task Checklist

- [ ] Review phase 06 live runner, phase 09 multi-service implementation,
      `docs/internal/PROJECT_DESIGN.md`, and the Tara2 repository shape.
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
      `POST /translations` support for Tara2.
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
- [ ] Add a Tara2 dry-run artifact showing planned images, workloads,
      dependencies, traffic, evidence, and blockers.
- [ ] Run a live Tara2 text-only chamber scenario when Redis, RabbitMQ, images,
      Prometheus, and OpenAI credentials are available.
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

- A Tara2 onboarding artifact or scenario can build or reference local images,
  deploy API, worker, Redis, and RabbitMQ into a local chamber namespace, and
  configure OpenAI through a redacted secret.
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

## Surprises And Discoveries

- Tara2 can avoid the document-service path when traffic submits direct `text`
  instead of `document_id`, but full translation still requires a worker and an
  LLM provider.
- Phase 09 is implemented, but it is intentionally optimized for controlled
  sample dependencies. Tara2 requires generic real-service onboarding features:
  manifest adaptation, multiple images, Redis/RabbitMQ readiness, redacted
  secrets, and POST traffic.

## Decision Log

- Chose a new phase instead of expanding phase 09 because phase 09 is already
  accepted and focused on controlled dependency graph behavior, while Tara2
  needs bring-your-own-service manifest and secret handling.
- Chose Tara2 Translation Service as the first acceptance target because it
  exercises realistic queue, worker, dependency, external API, and POST traffic
  requirements without requiring document ingestion for the first slice.
- Chose text-only translation as the first live journey so the document service
  remains out of scope until the chamber can model additional internal APIs.
- Chose OpenAI as an explicit external dependency rather than a chamber-owned
  dependency because the first goal is onboarding the service under realistic
  provider configuration, not simulating LLM quality or latency.

## Outcomes And Retrospective

- Pending.
