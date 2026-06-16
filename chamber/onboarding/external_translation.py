"""Preset onboarding plan for an external text-translation service shape."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from chamber.onboarding.generic import (
    ExternalDependencyPolicy,
    FollowUpCheck,
    ImageBuildSpec,
    ImageReplacement,
    OnboardingSpec,
    RealServiceOnboardingPlan,
    TrafficJourney,
    WorkloadSpec,
    build_onboarding_plan,
    validate_onboarding_environment,
)

DEFAULT_API_IMAGE = "ampule/external-translation-api:local"
DEFAULT_WORKER_IMAGE = "ampule/external-translation-worker:local"
DEFAULT_REDIS_PASSWORD_REF = "EXTERNAL_TRANSLATION_REDIS_PASSWORD"
REQUIRED_SECRET_ENV = ("LLM_API_KEY",)
DEFAULT_LLM_ENV = {
    "LLM_BACKEND": "openai",
    "LLM_ENDPOINT": "https://api.openai.com/v1",
    "MODEL_NAME": "gpt-4.1-mini",
}


def validate_external_translation_environment(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return missing environment variables required for a live OpenAI run."""

    return validate_onboarding_environment(REQUIRED_SECRET_ENV, env)


def build_external_translation_onboarding_plan(
    *,
    repo_path: str | Path,
    run_id: str,
    env: Mapping[str, str] | None = None,
) -> RealServiceOnboardingPlan:
    """Build the translation-service-shaped preset on the generic planner."""

    values = env or os.environ
    api_source = _manifest_image(Path(repo_path) / "deployment/deployment.yaml")
    worker_source = _manifest_image(Path(repo_path) / "k8s/keda/scaledjob.yaml")
    spec = OnboardingSpec(
        service_name="external-translation-service",
        repo_path=repo_path,
        scenario_id="external-text-translation-001",
        namespace_base="chamber-external-translation",
        manifest_paths=(
            "k8s/translation-configmap.yaml",
            "deployment/rabbitmq-deployment.yaml",
            "deployment/redis-deployment.yaml",
            "deployment/deployment.yaml",
            "k8s/keda/scaledjob.yaml",
        ),
        workload_roles=(
            WorkloadSpec("translation-service", "api", readiness=("health",)),
            WorkloadSpec("translation-worker", "worker", kind="ScaledJob", readiness=("logs",)),
            WorkloadSpec("rabbitmq", "dependency", readiness=("endpoints",)),
            WorkloadSpec("redis-master", "dependency", readiness=("ping",)),
        ),
        image_builds=(
            ImageBuildSpec(
                name="translation-api",
                dockerfile="docker/Dockerfile.api",
                image=DEFAULT_API_IMAGE,
            ),
            ImageBuildSpec(
                name="translation-worker",
                dockerfile="docker/Dockerfile.worker",
                image=DEFAULT_WORKER_IMAGE,
            ),
        ),
        image_replacements=(
            ImageReplacement(api_source, DEFAULT_API_IMAGE),
            ImageReplacement(worker_source, DEFAULT_WORKER_IMAGE),
        ),
        required_env=REQUIRED_SECRET_ENV,
        secret_env=(DEFAULT_REDIS_PASSWORD_REF, *REQUIRED_SECRET_ENV),
        config_overrides={
            "RABBITMQ_MANAGEMENT_URL": "http://rabbitmq:15672/",
            "REDIS_HOST": "redis-master",
            "REDIS_PORT": "6379",
            "DOCUMENT_SERVICE_BASE_URL": "http://document-service-disabled.chamber.local",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "",
            "OTEL_EXPORTER_OTLP_INSECURE": "true",
            "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=ampule-chamber",
            "LOG_ENVIRONMENT": "test",
            "LLM_BACKEND": values.get("LLM_BACKEND", DEFAULT_LLM_ENV["LLM_BACKEND"]),
            "LLM_ENDPOINT": values.get("LLM_ENDPOINT", DEFAULT_LLM_ENV["LLM_ENDPOINT"]),
            "MODEL_NAME": values.get("MODEL_NAME", DEFAULT_LLM_ENV["MODEL_NAME"]),
            "WORKER_IDLE_SHUTDOWN_SECONDS": values.get("WORKER_IDLE_SHUTDOWN_SECONDS", "60"),
            "WORKER_TASK_TIMEOUT_SECONDS": values.get("WORKER_TASK_TIMEOUT_SECONDS", "900"),
        },
        external_dependencies=(
            ExternalDependencyPolicy(
                name="openai",
                provider="OpenAI-compatible chat completions",
                endpoint=values.get("LLM_ENDPOINT", DEFAULT_LLM_ENV["LLM_ENDPOINT"]),
                required_env=REQUIRED_SECRET_ENV,
                timeout_seconds=120,
                redaction="Only key presence and endpoint/model metadata may be recorded.",
            ),
        ),
        traffic=TrafficJourney(
            tool="k6",
            method="POST",
            entrypoint="/translations",
            expected_status=202,
            body={
                "task_id": "ampule-phase10-text",
                "text": "Hello from Ampule Chamber.",
                "language_target": "Thai",
                "priority": 5,
            },
        ),
        follow_up_checks=(
            FollowUpCheck(
                name="translation-status",
                check_type="http_status",
                target="/status/ampule-phase10-text",
                expected="completed or failed terminal status",
                service_name="translation-service",
            ),
            FollowUpCheck(
                name="worker-logs",
                check_type="logs",
                target="deployment/translation-worker",
                expected="worker consumed or attempted the submitted task",
                service_name="translation-worker",
            ),
            FollowUpCheck(
                name="queue-state",
                check_type="queue_depth",
                target="rabbitmq/translation-tasks.queue",
                expected="queue depth drains or blocker is recorded",
                service_name="rabbitmq",
            ),
        ),
    )
    return build_onboarding_plan(spec, run_id=run_id, env=values)


def _manifest_image(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image:"):
            return stripped.removeprefix("image:").strip().strip('"')
    return ""
