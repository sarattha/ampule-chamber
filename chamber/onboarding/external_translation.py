"""External translation service onboarding planning.

The planner reads an external repository passed in by the operator and emits a
redacted, chamber-owned Kubernetes plan. It intentionally does not write or
expose secret values; live execution code must create secrets from process
environment at the last responsible moment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, cast

import yaml

from chamber.environment import EnvironmentAction, ReadinessCheck

DEFAULT_NAMESPACE_BASE = "chamber-external-translation"
DEFAULT_API_IMAGE = "ampule/external-translation-api:local"
DEFAULT_WORKER_IMAGE = "ampule/external-translation-worker:local"
DEFAULT_REDIS_PASSWORD_REF = "EXTERNAL_TRANSLATION_REDIS_PASSWORD"
DEFAULT_REPOSITORY_REF = "external-working-tree"
REQUIRED_SECRET_ENV = ("LLM_API_KEY",)
DEFAULT_LLM_ENV = {
    "LLM_BACKEND": "openai",
    "LLM_ENDPOINT": "https://api.openai.com/v1",
    "MODEL_NAME": "gpt-4.1-mini",
}


class OnboardingValidationError(ValueError):
    """Raised when a real-service onboarding plan cannot be built."""


@dataclass(frozen=True)
class ImageBuildPlan:
    """Local image build and kind-load intent for one service image."""

    name: str
    context: str
    dockerfile: str
    image: str
    build_command: tuple[str, ...]
    kind_load_command: tuple[str, ...]


@dataclass(frozen=True)
class WorkloadPlan:
    """Chamber-owned workload adapted from external dependency manifests."""

    name: str
    role: str
    kind: str
    source_path: str
    image: str | None
    ports: tuple[int, ...]


@dataclass(frozen=True)
class RedactedConfigEntry:
    """Config or secret summary safe to write into artifacts."""

    name: str
    source: str
    value: str
    secret: bool
    required: bool


@dataclass(frozen=True)
class ExternalDependencyPolicy:
    """Explicit opt-in external dependency policy for a real-service run."""

    name: str
    provider: str
    endpoint: str
    required_env: tuple[str, ...]
    timeout_seconds: int
    redaction: str


@dataclass(frozen=True)
class RealServiceOnboardingPlan:
    """Deterministic external-service onboarding plan."""

    run_id: str
    service_name: str
    repository_path: str
    repository_ref: str
    namespace: str
    labels: dict[str, str]
    image_builds: tuple[ImageBuildPlan, ...]
    workloads: tuple[WorkloadPlan, ...]
    manifests: tuple[dict[str, Any], ...]
    actions: tuple[EnvironmentAction, ...]
    readiness_checks: tuple[ReadinessCheck, ...]
    redacted_config: tuple[RedactedConfigEntry, ...]
    external_dependencies: tuple[ExternalDependencyPolicy, ...]
    traffic_journey: dict[str, Any]
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]


def validate_external_translation_environment(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return missing environment variables required for a live OpenAI run."""

    values = env or os.environ
    missing = [name for name in REQUIRED_SECRET_ENV if not values.get(name)]
    return tuple(missing)


def build_external_translation_onboarding_plan(
    *,
    repo_path: str | Path,
    run_id: str,
    env: Mapping[str, str] | None = None,
) -> RealServiceOnboardingPlan:
    """Build a redacted onboarding plan from external service repo files."""

    repository = Path(repo_path)
    _require_file(repository / "docker/Dockerfile.api")
    _require_file(repository / "docker/Dockerfile.worker")
    deployment = _load_yaml_documents(repository / "deployment/deployment.yaml")
    worker = _load_yaml_documents(repository / "k8s/keda/scaledjob.yaml")
    configmap = _first_document(repository / "k8s/translation-configmap.yaml", kind="ConfigMap")
    rabbitmq = _load_yaml_documents(repository / "deployment/rabbitmq-deployment.yaml")
    redis = _load_yaml_documents(repository / "deployment/redis-deployment.yaml")

    values = env or os.environ
    scenario_id = "external-text-translation-001"
    suffix = sha1(f"{scenario_id}:{run_id}".encode()).hexdigest()[:8]
    namespace = f"{DEFAULT_NAMESPACE_BASE}-{suffix}"
    labels = {
        "app.kubernetes.io/managed-by": "ampule-chamber",
        "app.kubernetes.io/part-of": "ampule-chamber",
        "chamber.ampule.dev/scenario-id": scenario_id,
        "chamber.ampule.dev/run-id": run_id,
    }
    config_data = dict(configmap.get("data") or {})
    llm_endpoint = values.get("LLM_ENDPOINT", DEFAULT_LLM_ENV["LLM_ENDPOINT"])
    model_name = values.get("MODEL_NAME", DEFAULT_LLM_ENV["MODEL_NAME"])
    llm_backend = values.get("LLM_BACKEND", DEFAULT_LLM_ENV["LLM_BACKEND"])
    adapted_config = {
        **config_data,
        "RABBITMQ_MANAGEMENT_URL": "http://rabbitmq:15672/",
        "REDIS_HOST": "redis-master",
        "REDIS_PORT": "6379",
        "DOCUMENT_SERVICE_BASE_URL": "http://document-service-disabled.chamber.local",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "",
        "OTEL_EXPORTER_OTLP_INSECURE": "true",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=ampule-chamber",
        "LOG_ENVIRONMENT": "test",
        "LLM_BACKEND": llm_backend,
        "LLM_ENDPOINT": llm_endpoint,
        "MODEL_NAME": model_name,
        "WORKER_IDLE_SHUTDOWN_SECONDS": values.get("WORKER_IDLE_SHUTDOWN_SECONDS", "60"),
        "WORKER_TASK_TIMEOUT_SECONDS": values.get("WORKER_TASK_TIMEOUT_SECONDS", "900"),
    }

    manifests = (
        _namespace_manifest(namespace, labels),
        _configmap_manifest(namespace, labels, adapted_config),
        _secret_placeholder(
            "external-translation-llm-secret",
            namespace,
            labels,
            ("LLM_API_KEY",),
        ),
        _secret_placeholder("redis-secret", namespace, labels, ("password",)),
        _secret_placeholder("rabbitmq-secret", namespace, labels, ("connection",)),
        *_adapt_dependency_manifests(rabbitmq, namespace=namespace, labels=labels),
        *_adapt_dependency_manifests(redis, namespace=namespace, labels=labels),
        *_adapt_api_manifests(deployment, namespace=namespace, labels=labels),
        _worker_deployment_from_scaledjob(worker, namespace=namespace, labels=labels),
    )
    workloads = _workloads(manifests, repository=repository)
    image_builds = (
        _image_build(
            name="translation-api",
            repository=repository,
            dockerfile="docker/Dockerfile.api",
            image=DEFAULT_API_IMAGE,
            kind_context="kind-ampule-chamber",
        ),
        _image_build(
            name="translation-worker",
            repository=repository,
            dockerfile="docker/Dockerfile.worker",
            image=DEFAULT_WORKER_IMAGE,
            kind_context="kind-ampule-chamber",
        ),
    )
    missing = validate_external_translation_environment(values)
    blockers = tuple(f"{name} is required for live OpenAI translation evidence" for name in missing)
    redacted_config = (
        RedactedConfigEntry(
            name="LLM_API_KEY",
            source="environment",
            value="<redacted-present>" if values.get("LLM_API_KEY") else "<missing>",
            secret=True,
            required=True,
        ),
        RedactedConfigEntry(
            name="LLM_BACKEND",
            source="environment/default",
            value=llm_backend,
            secret=False,
            required=True,
        ),
        RedactedConfigEntry(
            name="LLM_ENDPOINT",
            source="environment/default",
            value=llm_endpoint,
            secret=False,
            required=True,
        ),
        RedactedConfigEntry(
            name="MODEL_NAME",
            source="environment/default",
            value=model_name,
            secret=False,
            required=True,
        ),
        RedactedConfigEntry(
            name=DEFAULT_REDIS_PASSWORD_REF,
            source="environment/default",
            value=(
                "<redacted-present>"
                if values.get(DEFAULT_REDIS_PASSWORD_REF)
                else "<generated-at-live-run>"
            ),
            secret=True,
            required=False,
        ),
    )
    external = (
        ExternalDependencyPolicy(
            name="openai",
            provider="OpenAI-compatible chat completions",
            endpoint=llm_endpoint,
            required_env=("LLM_API_KEY",),
            timeout_seconds=120,
            redaction="Only key presence and endpoint/model metadata may be recorded.",
        ),
    )
    traffic = {
        "tool": "k6",
        "method": "POST",
        "entrypoint": "/translations",
        "expectedStatus": 202,
        "body": {
            "task_id": "ampule-phase10-text",
            "text": "Hello from Ampule Chamber.",
            "language_target": "Thai",
            "priority": 5,
        },
        "followUp": {
            "type": "http_status",
            "path": "/status/ampule-phase10-text",
            "terminalStatuses": ["completed", "failed"],
        },
    }
    return RealServiceOnboardingPlan(
        run_id=run_id,
        service_name="external-translation-service",
        repository_path=str(repository),
        repository_ref=DEFAULT_REPOSITORY_REF,
        namespace=namespace,
        labels=labels,
        image_builds=image_builds,
        workloads=workloads,
        manifests=manifests,
        actions=_actions(manifests),
        readiness_checks=_readiness_checks(namespace),
        redacted_config=redacted_config,
        external_dependencies=external,
        traffic_journey=traffic,
        blockers=blockers,
        limitations=(
            "Document-service path is intentionally ignored; direct text translation only.",
            "KEDA ScaledJob is adapted to a bounded worker Deployment for local kind.",
            "External repository is read-only and provided as operator input.",
        ),
    )


def _require_file(path: Path) -> None:
    if not path.exists():
        raise OnboardingValidationError(f"required external service file is missing: {path}")


def _load_yaml_documents(path: Path) -> tuple[dict[str, Any], ...]:
    _require_file(path)
    with path.open(encoding="utf-8") as handle:
        docs = tuple(item for item in yaml.safe_load_all(handle) if item)
    if not all(isinstance(item, dict) for item in docs):
        raise OnboardingValidationError(f"{path}: all YAML documents must be mappings")
    return docs


def _first_document(path: Path, *, kind: str) -> dict[str, Any]:
    for document in _load_yaml_documents(path):
        if document.get("kind") == kind:
            return document
    raise OnboardingValidationError(f"{path}: no {kind} document found")


def _namespace_manifest(namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace, "labels": labels},
    }


def _configmap_manifest(
    namespace: str,
    labels: dict[str, str],
    data: dict[str, str],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "translation-service-config",
            "namespace": namespace,
            "labels": labels,
        },
        "data": {key: str(value) for key, value in sorted(data.items())},
    }


def _secret_placeholder(
    name: str,
    namespace: str,
    labels: dict[str, str],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {"chamber.ampule.dev/redacted": "true"},
        },
        "type": "Opaque",
        "stringData": {key: "<redacted>" for key in keys},
    }


def _adapt_dependency_manifests(
    documents: tuple[dict[str, Any], ...],
    *,
    namespace: str,
    labels: dict[str, str],
) -> tuple[dict[str, Any], ...]:
    adapted = []
    for document in documents:
        kind = str(document.get("kind", ""))
        if kind not in {"ConfigMap", "Deployment", "Service"}:
            continue
        item = _copy(document)
        _adapt_metadata(item, namespace=namespace, labels=labels)
        if kind == "Deployment":
            _adapt_pod_template(item, labels=labels)
        adapted.append(item)
    return tuple(adapted)


def _adapt_api_manifests(
    documents: tuple[dict[str, Any], ...],
    *,
    namespace: str,
    labels: dict[str, str],
) -> tuple[dict[str, Any], ...]:
    adapted = []
    for document in documents:
        kind = str(document.get("kind", ""))
        if kind not in {"Deployment", "Service"}:
            continue
        item = _copy(document)
        _adapt_metadata(item, namespace=namespace, labels=labels)
        if kind == "Deployment":
            _adapt_pod_template(item, labels=labels)
            container = item["spec"]["template"]["spec"]["containers"][0]
            container["image"] = DEFAULT_API_IMAGE
            container["imagePullPolicy"] = "IfNotPresent"
            _ensure_env_secret(
                container,
                "LLM_API_KEY",
                "external-translation-llm-secret",
                "LLM_API_KEY",
            )
            _ensure_resources(container)
        adapted.append(item)
    return tuple(adapted)


def _worker_deployment_from_scaledjob(
    documents: tuple[dict[str, Any], ...],
    *,
    namespace: str,
    labels: dict[str, str],
) -> dict[str, Any]:
    scaled_job = next((item for item in documents if item.get("kind") == "ScaledJob"), None)
    if scaled_job is None:
        raise OnboardingValidationError("external worker ScaledJob manifest was not found")
    template = _copy(scaled_job["spec"]["jobTargetRef"]["template"])
    pod_labels = {**template.get("metadata", {}).get("labels", {}), **labels}
    template.setdefault("metadata", {})["labels"] = pod_labels
    container = template["spec"]["containers"][0]
    container["image"] = DEFAULT_WORKER_IMAGE
    container["imagePullPolicy"] = "IfNotPresent"
    _ensure_env_secret(
        container,
        "LLM_API_KEY",
        "external-translation-llm-secret",
        "LLM_API_KEY",
    )
    _ensure_resources(container)
    selector = {
        "app": "translation-worker",
        "chamber.ampule.dev/run-id": labels["chamber.ampule.dev/run-id"],
    }
    template["metadata"]["labels"].update(selector)
    template["spec"]["restartPolicy"] = "Always"
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "translation-worker",
            "namespace": namespace,
            "labels": {**labels, "app": "translation-worker"},
            "annotations": {"chamber.ampule.dev/adapted-from": "keda-scaledjob"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": selector},
            "template": template,
        },
    }


def _adapt_metadata(
    manifest: dict[str, Any],
    *,
    namespace: str,
    labels: dict[str, str],
) -> None:
    metadata = manifest.setdefault("metadata", {})
    metadata["namespace"] = namespace
    metadata["labels"] = {**metadata.get("labels", {}), **labels}


def _adapt_pod_template(manifest: dict[str, Any], *, labels: dict[str, str]) -> None:
    template = manifest["spec"]["template"]
    metadata = template.setdefault("metadata", {})
    metadata["labels"] = {**metadata.get("labels", {}), **labels}
    for container in template["spec"].get("containers", []):
        if container.get("imagePullPolicy") == "Always":
            container["imagePullPolicy"] = "IfNotPresent"
        _ensure_resources(container)


def _ensure_env_secret(
    container: dict[str, Any],
    env_name: str,
    secret_name: str,
    secret_key: str,
) -> None:
    env = list(container.get("env", []))
    env = [item for item in env if item.get("name") != env_name]
    env.append(
        {
            "name": env_name,
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": secret_key}},
        }
    )
    container["env"] = env


def _ensure_resources(container: dict[str, Any]) -> None:
    container.setdefault(
        "resources",
        {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "768Mi"},
        },
    )


def _workloads(
    manifests: tuple[dict[str, Any], ...],
    *,
    repository: Path,
) -> tuple[WorkloadPlan, ...]:
    workloads = []
    source_paths = {
        "translation-service": "deployment/deployment.yaml",
        "translation-worker": "k8s/keda/scaledjob.yaml",
        "rabbitmq": "deployment/rabbitmq-deployment.yaml",
        "redis-master": "deployment/redis-deployment.yaml",
    }
    for manifest in manifests:
        if manifest.get("kind") not in {"Deployment", "Service"}:
            continue
        name = str(manifest["metadata"]["name"])
        pod_spec = manifest.get("spec", {}).get("template", {}).get("spec", {})
        containers = pod_spec.get("containers", [])
        image = str(containers[0]["image"]) if containers and containers[0].get("image") else None
        manifest_ports = manifest.get("spec", {}).get("ports", [])
        port_source = containers[0].get("ports", []) if containers else manifest_ports
        ports = tuple(
            int(port.get("containerPort") or port.get("port"))
            for port in port_source
            if port.get("containerPort") or port.get("port")
        )
        role = _role(name)
        workloads.append(
            WorkloadPlan(
                name=name,
                role=role,
                kind=str(manifest["kind"]),
                source_path=str(repository / source_paths.get(name, "derived")),
                image=image,
                ports=ports,
            )
        )
    return tuple(workloads)


def _role(name: str) -> str:
    if name == "translation-service":
        return "api"
    if name == "translation-worker":
        return "worker"
    if name in {"rabbitmq", "redis-master"}:
        return "dependency"
    return "supporting"


def _image_build(
    *,
    name: str,
    repository: Path,
    dockerfile: str,
    image: str,
    kind_context: str,
) -> ImageBuildPlan:
    return ImageBuildPlan(
        name=name,
        context=str(repository),
        dockerfile=str(repository / dockerfile),
        image=image,
        build_command=(
            "docker",
            "build",
            "-f",
            str(repository / dockerfile),
            "-t",
            image,
            str(repository),
        ),
        kind_load_command=(
            "kind",
            "load",
            "docker-image",
            image,
            "--name",
            kind_context.removeprefix("kind-"),
        ),
    )


def _actions(manifests: tuple[dict[str, Any], ...]) -> tuple[EnvironmentAction, ...]:
    actions = []
    for manifest in manifests:
        kind = str(manifest["kind"])
        name = str(manifest["metadata"]["name"])
        action_type = "provision" if kind == "Namespace" else "deploy"
        actions.append(
            EnvironmentAction(
                action_type=action_type,
                name=f"apply-{kind.lower()}-{name}",
                description=f"Apply adapted external-service {kind} {name}.",
                manifest=manifest,
            )
        )
    actions.append(
        EnvironmentAction(
            action_type="cleanup",
            name="delete-phase10-managed-resources",
            description=(
                "Delete all external-service chamber resources by run labels and namespace."
            ),
        )
    )
    return tuple(actions)


def _readiness_checks(namespace: str) -> tuple[ReadinessCheck, ...]:
    return (
        ReadinessCheck(
            name="rabbitmq-endpoints",
            description="RabbitMQ AMQP and management service endpoints are ready.",
            target=f"service/rabbitmq:5672,15672 in namespace {namespace}",
        ),
        ReadinessCheck(
            name="redis-ping",
            description="Redis service endpoint accepts authenticated PING.",
            target=f"service/redis-master:6379 in namespace {namespace}",
        ),
        ReadinessCheck(
            name="translation-api-health",
            description="Translation API /health returns HTTP 200 after dependencies are ready.",
            target="service/translation-service:8887/health",
        ),
        ReadinessCheck(
            name="translation-worker-logs",
            description="Worker pod starts and records queue consumer startup logs.",
            target="deployment/translation-worker",
        ),
    )


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    copied = yaml.safe_load(yaml.safe_dump(value))
    if not isinstance(copied, dict):
        raise OnboardingValidationError("internal manifest copy did not produce a mapping")
    return cast(dict[str, Any], copied)
