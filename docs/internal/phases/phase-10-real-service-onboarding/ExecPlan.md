# ExecPlan: Phase 10 Real Service Onboarding

## Description

Build the first bring-your-own-service onboarding workflow for Ampule Chamber.
This phase converts an operator-provided external repository with existing
Dockerfiles, raw Kubernetes manifests, secrets, runtime dependencies, and
POST-based traffic into a safe local chamber plan.

The first preset target remains an external text-translation service shape, but
the core implementation is now a generic raw-Kubernetes onboarding contract.
Operators provide manifest paths, workload roles, image mappings, readiness
intent, traffic journeys, and external dependency policy instead of hard-coding
one repository name or source layout into Ampule Chamber.

## Task Checklist

- [x] Review phase 06 live runner, phase 09 multi-service implementation,
      `docs/internal/PROJECT_DESIGN.md`, and the external service repository
      shape.
- [x] Define a real-service onboarding contract for repository path, images,
      manifests, workloads, dependencies, secrets, config, traffic journeys,
      and external dependency policy.
- [x] Decide whether the contract is represented as a new scenario extension,
      a separate onboarding file, or a generated phase artifact.
- [x] Add validation for required onboarding fields and redaction rules.
- [x] Implement local image build and kind-load planning for multiple images.
- [x] Add chamber-safe Kubernetes manifest adaptation for Namespace,
      Deployment, Service, ConfigMap, Secret, labels, image references,
      resources, probes, and cleanup selectors.
- [x] Support chamber-owned Redis and RabbitMQ dependency workloads with
      service-specific readiness checks.
- [x] Support Secret and ConfigMap injection without writing secret values into
      generated artifacts, reports, logs, or metadata.
- [x] Add k6 POST JSON traffic journeys, including direct-text
      `POST /translations` support for an external translation service.
- [x] Add optional follow-up checks for task status, event feed, queue depth, or
      worker logs so runs can prove pipeline progress beyond API acceptance.
- [x] Add external dependency policy handling for OpenAI or similar providers:
      allowed endpoints, required environment variables, timeout budgets, and
      report limitations.
- [x] Extend evidence attribution for API, worker, Redis, RabbitMQ, queue
      state, application logs, and external dependency diagnostics.
- [x] Extend reports with onboarding summary, adapted workload list, redacted
      config summary, external dependencies, queue evidence, and real-service
      retest guidance.
- [x] Add focused tests for onboarding validation, manifest adaptation,
      redaction, image mapping, POST traffic generation, readiness checks, and
      report output.
- [x] Add an external-service dry-run artifact showing planned images, workloads,
      dependencies, traffic, evidence, and blockers.
- [x] Run a live external-service text-only chamber scenario when Redis,
      RabbitMQ, images, Prometheus, and OpenAI credentials are available.
- [x] Record acceptance evidence, limitations, and follow-up work in this plan.
- [x] Prepare the real external translation service in kind up to the LLM-key
      boundary: build/load API, worker, and aggregator images; run Redis and
      RabbitMQ in kind; apply API and aggregator; verify dependency and API
      readiness; record redacted artifacts.
- [x] Run an OpenAI-backed direct-text dry run through the prepared chamber path
      with RabbitMQ and Redis in kind, document service omitted, and
      `MODEL_NAME=gpt-5.4-mini`.

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
- [x] Generalized the implementation with `OnboardingSpec` and
      `build_onboarding_plan` so Phase 10 can onboard arbitrary raw Kubernetes
      YAML from an operator-provided external project.
- [x] Added redacted external-service manifest planning from an
      operator-provided working tree.
- [x] Added chamber-owned Redis and RabbitMQ dependency workload planning.
- [x] Added raw `ScaledJob` pod-template adaptation for labels, local image
      replacement, pull policy, resources, inline secret-env redaction, workload
      inventory, readiness planning, and evidence attribution.
- [x] Added follow-up check and evidence-attribution contracts for task status,
      worker logs, queue depth, workload logs, pod status, and external
      dependency diagnostics.
- [x] Added live preflight blockers for missing required env vars, missing
      Prometheus evidence configuration, and missing local image build plans.
- [x] Addressed PR review feedback by separating traffic target URLs from
      port-forward readiness probe URLs so POST-only endpoints are not probed
      with GET before k6 runs.
- [x] Addressed PR review feedback by honoring explicit empty env maps during
      onboarding validation, redaction, and preflight instead of falling back to
      the developer shell environment.
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
- The external worker manifest may be a KEDA `ScaledJob`; the generic adapter
  can now safely rewrite its embedded pod template without copying source files
  or secrets.

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
- Chose a generic `OnboardingSpec` over a repository-specific contract so Phase
  10 can describe broad raw-YAML use cases while preserving the text-translation
  preset as only one operator-supplied shape.
- Chose to leave Helm and Kustomize rendering out of this pass. Operators can
  provide rendered raw YAML now; native overlay rendering should be a later
  phase once the raw-manifest contract is stable.

## Outcomes And Retrospective

- Initial implementation added deterministic dry-run planning, scenario
  validation, POST k6 generation, report sections, and focused tests.
- Current scope is generic for raw Kubernetes YAML supplied through
  `OnboardingSpec`: manifest paths, workload roles, image builds, image
  replacements, config overrides, required/secret env vars, dependency
  policies, POST traffic, follow-up checks, and evidence attribution.
- The external text-translation preset is now a compatibility layer on top of
  the generic onboarding planner, not the core repository contract.
- Broader real-service onboarding still needs persisted onboarding-file parsing,
  Helm/Kustomize rendering, richer readiness probe execution, and live runner
  integration beyond deterministic planning and preflight evidence.
- Live OpenAI-backed external-service evidence is now available for the
  direct-text path using `MODEL_NAME=gpt-5.4-mini`.
- Real experiment prep on 2026-06-16 created
  `artifacts/real-experiment-prep-20260616/` and prepared namespace
  `chamber-external-translation-prep`.
- Built three local service images from
  `/Users/jobz/Works/tara2_translation_service`:
  `ampule/external-translation-api:local`,
  `ampule/external-translation-worker:local`, and
  `ampule/external-translation-aggregator:local`.
- Loaded those three images into the `ampule-chamber` kind node and recorded
  image IDs in `docker-images.txt`, `kind-load-images.log`, and
  `kind-node-images.txt`.
- Created Redis and RabbitMQ Secrets directly in kind with generated local
  values. Redacted manifests document the Secret names and keys without
  recording values.
- Applied Redis and RabbitMQ workloads inside kind and verified:
  `rollout-redis.log`, `rollout-rabbitmq.log`, `redis-ping.log`,
  `rabbitmq-check-running.log`, and `rabbitmq-status-head.log`.
- Applied translation API and aggregator workloads with local images and
  chamber config. Verified API `/health` returned `{"status":"ok"}` and the
  aggregator started consuming status events from shard queues.
- Applied the worker Deployment after creating the real `LLM_API_KEY` Secret in
  kind. The worker uses a normal Kubernetes Deployment running
  `python -m app.worker.worker` because KEDA is not installed in the local kind
  cluster.
- Patched the target worker startup settings log to redact `LLM_API_KEY`; an
  earlier pre-traffic log artifact was redacted after revealing that DEBUG
  startup logs could print settings.
- Patched the target worker for this run so missing in-cluster
  `ai-llm-utils-service` token counts fall back to a conservative local count.
  This keeps the text-only path runnable while the document/tokenizer service is
  intentionally omitted.
- Patched the target worker so `gpt-5.4-mini` uses the OpenAI Responses API.
  The previous OpenAI Agents SDK chat-completions path sent `max_tokens`, which
  this model rejected.
- Completed OpenAI-backed task
  `ampule-dryrun-gpt54-mini-responses-20260616140847` through
  `POST /translations`. API status returned `completed` with Thai text
  `สวัสดีจากการทดสอบ Ampule Chamber แบบ dry run.`.
- RabbitMQ queue evidence after the completed run showed zero ready and
  unacknowledged messages in task, retry, and aggregation queues. The
  `translation-tasks.queue.dlq` count of 1 is from an earlier pre-fix failed
  task in the same namespace.
- Evidence for the completed run is in
  `artifacts/real-experiment-prep-20260616/dryrun-gpt5-responses-status-latest.txt`,
  `translation-worker-logs-after-gpt5-responses-dryrun.txt`,
  `translation-api-logs-after-gpt5-responses-dryrun.txt`,
  `translation-aggregator-logs-after-gpt5-responses-dryrun.txt`, and
  `rabbitmq-queues-after-gpt5-responses-dryrun.txt`.
- The API route `/execution-graph/{task_id}` returned HTTP 404 for the
  completed dry run, so status, workload logs, and queue state are the accepted
  evidence for this manual chamber run.
- Real experiment `experiments/experiment-001/` ran 100 random contexts from
  `dataset/thaigov-v2-corpus-22032023-context.jsonl` through the live
  direct-text translation path to English.
- Experiment 001 used local kind port-forwards
  `API_URL=http://127.0.0.1:18887` and
  `PROMETHEUS_URL=http://127.0.0.1:19090`; Prometheus in namespace
  `monitoring` returned ready HTTP 200.
- Experiment 001 completed 100/100 submitted tasks with 100 HTTP 202 accepts,
  zero terminal failures, and zero timed-out or non-terminal tasks. End-to-end
  task latency was p50 5.23505s, p95 10.485115s, and max 57.5322s.
- Experiment 001 RabbitMQ evidence after the run showed zero ready and
  unacknowledged messages in task, retry, aggregation, and aggregation-retry
  queues. The existing `translation-tasks.queue.dlq` count of 1 still predates
  the experiment.
- Experiment 001 Prometheus limitation: Prometheus is deployed and reachable
  inside kind, but the current scrape configuration only proved Prometheus
  itself was up; the container CPU query returned an empty vector.
- Verified existing cluster Prometheus in namespace `monitoring` is ready
  through a temporary port-forward. Live runs still need `PROMETHEUS_URL`
  exported while that port-forward is active.
- The target repository's Git metadata is broken (`fatal: bad object HEAD`), so
  Docker builds succeeded but could not capture commit metadata.
- Live prerequisite check on this machine after implementation:
  - `docker`, `kind`, `kubectl`, and `k6` are present.
  - Current Kubernetes context is `kind-ampule-chamber`.
  - `kind get clusters` includes `ampule-chamber`.
  - `LLM_API_KEY` was provided for this dry run through a Kubernetes Secret and
    was not recorded in phase artifacts.
  - `PROMETHEUS_URL` is missing from the developer shell, so the manual dry run
    did not collect Prometheus metrics through the automated live evidence path.
- Acceptance evidence recorded so far:
  - `uv run python -m unittest tests.test_phase10_onboarding` passed.
  - `uv run ruff check chamber/onboarding tests/test_phase10_onboarding.py`
    passed.
  - `uv run python -m unittest tests.test_phase03_traffic_and_chaos
    tests.test_phase06_live_runner tests.test_phase10_onboarding` passed.
  - `uv run ty check chamber scripts tests` passed.
  - `uv run python scripts/validate_scenarios.py` passed for 7 scenarios.
  - `uv run ruff format --check chamber tests scripts` passed.
  - `uv run ruff check chamber tests scripts` passed.
  - `uv run ty check chamber scripts tests` passed.
  - `make check` passed, including format, lint, typecheck, 86 tests,
    coverage at the 90% threshold, scenario validation, and package build.
