# Phase 10: Real Service Onboarding

Extend Ampule Chamber from controlled sample topologies into a practical
onboarding path for real repositories with existing Kubernetes manifests,
runtime dependencies, secrets, and non-trivial traffic journeys.

This phase should preserve the phase 06-09 safety model while making an
operator-provided external service repository plannable in a local chamber. The
goal is not to support every production deployment shape. The first supported
contract accepts raw Kubernetes YAML, explicit workload roles, local image build
plans, dependency policy, redacted runtime config, and POST traffic journeys.

The core question for this phase is:

> Can Ampule Chamber adapt a real service repository into a safe, isolated,
> evidence-backed chamber run without depending on production namespaces,
> private registries, or manually edited manifests?

## What Needs To Be Done

- Define a real-service onboarding contract that can reference an external
  repository, local image build contexts, manifests or overlays, dependency
  workloads, runtime configuration, secrets, and traffic journeys.
- Add a chamber-safe manifest adaptation path for existing Kubernetes YAML:
  namespace rewriting, image replacement, labels, resource limits, probes,
  service names, and cleanup ownership.
- Support ConfigMap, Secret, `env`, and `envFrom` inputs without committing
  secret values to source control or generated artifacts.
- Support local image build and `kind load docker-image` mapping for multiple
  service images.
- Support non-HTTP dependency readiness for Redis and RabbitMQ, including
  Kubernetes Service endpoint checks and application-level health checks where
  available.
- Add traffic journeys beyond simple GET, starting with k6 POST JSON requests
  and optional follow-up polling.
- Allow explicit external dependency policy for services that call APIs outside
  the chamber, such as OpenAI. External calls must be opt-in, named, and
  recorded as limitations or dependencies in the report.
- Create a generic onboarding contract plus an external text-translation preset
  that runs direct-text translation traffic while intentionally avoiding the
  document-service path.
- Collect and attribute evidence across API, worker, Redis, RabbitMQ, and any
  other chamber-owned workloads.
- Extend reports with onboarding assumptions, adapted manifests, external
  dependency policy, secret redaction notes, queue evidence, and real-service
  retest guidance.
- Record dry-run and, when prerequisites are available, live acceptance evidence
  in `artifacts/`.

## External Text-Translation Preset

The first real-service target is expected to model:

- `translation-api` as the externally exercised HTTP target.
- `translation-worker` as a queue-consuming chamber workload.
- Redis and RabbitMQ as chamber-owned dependencies.
- OpenAI as an explicit external dependency configured through a redacted
  secret.
- Direct-text `POST /translations` traffic so the document service is not
  required.
- Optional status, event, log, or queue-depth checks to prove work moved beyond
  API acceptance and into the translation pipeline.

## Generic Raw-YAML Scope

The reusable Phase 10 contract is not tied to a named external repository. An
operator supplies:

- external repository path and manifest paths
- workload names, kinds, roles, and readiness intent
- local Docker build contexts and image replacement rules
- required and secret environment variables
- ConfigMap overrides and Secret redaction expectations
- explicit external dependency policies
- traffic journey and follow-up evidence checks

Helm and Kustomize rendering are deferred. Operators can provide rendered YAML
to the generic planner in this phase.

## Agent Notes

- Never commit real API keys, registry tokens, or private endpoint credentials.
- Prefer generated overlays and redacted artifacts over mutating source
  manifests from the target repository.
- Keep external API usage bounded, explicit, and easy to disable.
- Treat missing external credentials as a blocker to live translation evidence,
  not as a successful run.
- Preserve chamber cleanup guarantees for every adapted workload.
- Start with a narrow external text-only workflow before supporting document
  ingestion, KEDA autoscaling parity, or production identity integrations.
