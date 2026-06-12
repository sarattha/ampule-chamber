"""Dry-run Kubernetes environment planning for chamber runs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Protocol

from chamber.contracts.scenario import Scenario

KIND_PROVIDER = "kind"
SUPPORTED_DRY_RUN_PROVIDERS = frozenset({KIND_PROVIDER})
FUTURE_PROVIDER_NAMES = frozenset({"aks", "existing_kubernetes"})
RESOURCE_KEYS = frozenset({"cpuRequest", "cpuLimit", "memoryRequest", "memoryLimit"})


class EnvironmentPlanningError(ValueError):
    """Raised when an environment plan cannot be created."""


@dataclass(frozen=True)
class PlanningFailure:
    """Specific reason an environment plan cannot be generated."""

    reason: str
    message: str
    path: str


@dataclass(frozen=True)
class ReadinessCheck:
    """Readiness check the chamber will evaluate after manifest application."""

    name: str
    description: str
    target: str


@dataclass(frozen=True)
class EnvironmentAction:
    """Ordered dry-run action for preparing, validating, or cleaning a chamber."""

    action_type: str
    name: str
    description: str
    manifest: dict[str, Any] | None = None


@dataclass(frozen=True)
class CleanupPlan:
    """Selectors and ordered actions used to remove chamber-owned resources."""

    namespace: str
    selectors: dict[str, str]
    actions: tuple[EnvironmentAction, ...]


@dataclass(frozen=True)
class EnvironmentMetadata:
    """Metadata produced by environment planning for later evidence collectors."""

    run_id: str
    scenario_id: str
    provider: str
    source_provider: str
    namespace: str
    labels: dict[str, str]
    resource_names: dict[str, str]
    readiness_checks: tuple[ReadinessCheck, ...]
    cleanup_selectors: dict[str, str]


@dataclass(frozen=True)
class EnvironmentPlan:
    """Deterministic dry-run plan for a Kubernetes chamber environment."""

    run_id: str
    scenario_id: str
    provider: str
    namespace: str
    actions: tuple[EnvironmentAction, ...]
    manifests: tuple[dict[str, Any], ...]
    metadata: EnvironmentMetadata
    cleanup: CleanupPlan


class EnvironmentProvider(Protocol):
    """Builds an environment plan for one execution provider."""

    provider_name: str

    def build_plan(self, scenario: Scenario, *, run_id: str) -> EnvironmentPlan:
        """Build a dry-run environment plan."""


@dataclass(frozen=True)
class TargetServiceContract:
    """Normalized target service fields needed for Kubernetes manifests."""

    source_name: str
    name: str
    image: str
    port_name: str
    port: int
    replicas: int
    resources: dict[str, str]
    health_endpoint: str
    readiness_endpoint: str


class KindEnvironmentProvider:
    """Dry-run provider for kind-backed local Kubernetes chambers."""

    provider_name = KIND_PROVIDER

    def build_plan(self, scenario: Scenario, *, run_id: str) -> EnvironmentPlan:
        failures = validate_environment_contract(scenario, provider_name=self.provider_name)
        if failures:
            details = "; ".join(f"{failure.path}: {failure.message}" for failure in failures)
            raise EnvironmentPlanningError(f"cannot build environment plan: {details}")

        service_contract = _target_service_contract(scenario)
        scenario_id = scenario.scenario_id
        suffix = _trace_suffix(scenario_id, run_id)
        base_namespace = str(scenario.document["environment"].get("namespace", "chamber"))
        namespace = _kubernetes_name(base_namespace, scenario_id, suffix=suffix)
        labels = _chamber_labels(scenario_id=scenario_id, run_id=run_id)

        deployment_name = _kubernetes_name(service_contract.name, "deployment", suffix=suffix)
        service_name = _kubernetes_name(service_contract.name, "svc", suffix=suffix)
        resource_names = {
            "namespace": namespace,
            "deployment": deployment_name,
            "service": service_name,
        }

        namespace_manifest = _namespace_manifest(namespace=namespace, labels=labels)
        deployment_manifest = _deployment_manifest(
            name=deployment_name,
            namespace=namespace,
            labels=labels,
            service=service_contract,
        )
        service_manifest = _service_manifest(
            name=service_name,
            namespace=namespace,
            labels=labels,
            service=service_contract,
            deployment_name=deployment_name,
        )
        manifests = (namespace_manifest, deployment_manifest, service_manifest)
        readiness_checks = _readiness_checks(
            namespace=namespace,
            deployment_name=deployment_name,
            service_name=service_name,
            service=service_contract,
        )
        cleanup_selectors = {
            "app.kubernetes.io/managed-by": "ampule-chamber",
            "chamber.ampule.dev/run-id": labels["chamber.ampule.dev/run-id"],
        }
        cleanup_actions = (
            EnvironmentAction(
                action_type="cleanup",
                name="delete-managed-resources",
                description=(
                    "Delete Kubernetes resources matching chamber run labels before "
                    "removing the namespace."
                ),
            ),
            EnvironmentAction(
                action_type="cleanup",
                name="delete-namespace",
                description=f"Delete namespace {namespace}.",
                manifest=namespace_manifest,
            ),
        )
        cleanup = CleanupPlan(
            namespace=namespace,
            selectors=cleanup_selectors,
            actions=cleanup_actions,
        )
        metadata = EnvironmentMetadata(
            run_id=run_id,
            scenario_id=scenario_id,
            provider=self.provider_name,
            source_provider=str(scenario.document["environment"]["provider"]),
            namespace=namespace,
            labels=labels,
            resource_names=resource_names,
            readiness_checks=readiness_checks,
            cleanup_selectors=cleanup_selectors,
        )
        actions = (
            EnvironmentAction(
                action_type="provision",
                name="create-namespace",
                description=f"Create isolated namespace {namespace}.",
                manifest=namespace_manifest,
            ),
            EnvironmentAction(
                action_type="deploy",
                name="apply-deployment",
                description=f"Apply target Deployment {deployment_name}.",
                manifest=deployment_manifest,
            ),
            EnvironmentAction(
                action_type="deploy",
                name="apply-service",
                description=f"Apply target Service {service_name}.",
                manifest=service_manifest,
            ),
            EnvironmentAction(
                action_type="verify",
                name="verify-readiness",
                description="Verify pod rollout, endpoints, health, readiness, and startup logs.",
            ),
            EnvironmentAction(
                action_type="collect",
                name="collect-environment-metadata",
                description="Capture baseline pod status, events, endpoints, and startup logs.",
            ),
            *cleanup_actions,
        )

        return EnvironmentPlan(
            run_id=run_id,
            scenario_id=scenario_id,
            provider=self.provider_name,
            namespace=namespace,
            actions=actions,
            manifests=manifests,
            metadata=metadata,
            cleanup=cleanup,
        )


def get_environment_provider(provider_name: str = KIND_PROVIDER) -> EnvironmentProvider:
    """Return the environment provider for a dry-run chamber target."""

    if provider_name == KIND_PROVIDER:
        return KindEnvironmentProvider()
    if provider_name in FUTURE_PROVIDER_NAMES:
        raise EnvironmentPlanningError(
            f"{provider_name!r} provider is reserved for a later phase and is not implemented"
        )
    raise EnvironmentPlanningError(f"unsupported environment provider: {provider_name!r}")


def plan_environment(
    scenario: Scenario,
    *,
    run_id: str,
    provider_name: str = KIND_PROVIDER,
) -> EnvironmentPlan:
    """Build a deterministic dry-run environment plan for a scenario."""

    _require_non_empty(run_id, "run_id")
    provider = get_environment_provider(provider_name)
    return provider.build_plan(scenario, run_id=run_id)


def validate_environment_contract(
    scenario: Scenario,
    *,
    provider_name: str = KIND_PROVIDER,
) -> tuple[PlanningFailure, ...]:
    """Return specific environment contract failures for dry-run planning."""

    failures: list[PlanningFailure] = []
    if provider_name not in SUPPORTED_DRY_RUN_PROVIDERS:
        failures.append(
            PlanningFailure(
                reason="unsupported_provider",
                message=f"provider {provider_name!r} is not supported for dry-run planning",
                path="provider",
            )
        )

    document = scenario.document
    target = _mapping(document.get("target"), "target", failures)
    service = _mapping(target.get("service") if target else None, "target.service", failures)
    environment = _mapping(document.get("environment"), "environment", failures)
    if service is not None:
        _service_name(service, failures)
        _service_image(service, failures)
        _service_port(service, failures)
        _endpoint(service, "healthEndpoint", failures)
        _endpoint(service, "readinessEndpoint", failures)
    if environment is not None:
        _replicas(environment, failures)
        _resources(environment, failures)

    return tuple(failures)


def _target_service_contract(scenario: Scenario) -> TargetServiceContract:
    document = scenario.document
    service = document["target"]["service"]
    environment = document["environment"]
    port = service["ports"][0]
    service_name = str(service["name"])
    resources = {
        "cpuRequest": str(environment["resources"]["cpuRequest"]),
        "cpuLimit": str(environment["resources"]["cpuLimit"]),
        "memoryRequest": str(environment["resources"]["memoryRequest"]),
        "memoryLimit": str(environment["resources"]["memoryLimit"]),
    }
    return TargetServiceContract(
        source_name=service_name,
        name=_dns_label(service_name),
        image=str(service["image"]),
        port_name=str(port.get("name", "http")),
        port=int(port["port"]),
        replicas=int(environment["replicas"]),
        resources=resources,
        health_endpoint=str(service["healthEndpoint"]),
        readiness_endpoint=str(service["readinessEndpoint"]),
    )


def _namespace_manifest(*, namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": labels,
        },
    }


def _deployment_manifest(
    *,
    name: str,
    namespace: str,
    labels: dict[str, str],
    service: TargetServiceContract,
) -> dict[str, Any]:
    selector_labels = {
        "app.kubernetes.io/name": service.name,
        "chamber.ampule.dev/run-id": labels["chamber.ampule.dev/run-id"],
    }
    pod_labels = {**labels, **selector_labels}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {
                "chamber.ampule.dev/source-service-name": service.source_name,
            },
        },
        "spec": {
            "replicas": service.replicas,
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "containers": [
                        {
                            "name": service.name,
                            "image": service.image,
                            "ports": [
                                {
                                    "name": service.port_name,
                                    "containerPort": service.port,
                                }
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": service.resources["cpuRequest"],
                                    "memory": service.resources["memoryRequest"],
                                },
                                "limits": {
                                    "cpu": service.resources["cpuLimit"],
                                    "memory": service.resources["memoryLimit"],
                                },
                            },
                            "livenessProbe": _http_probe(
                                path=service.health_endpoint,
                                port_name=service.port_name,
                            ),
                            "readinessProbe": _http_probe(
                                path=service.readiness_endpoint,
                                port_name=service.port_name,
                            ),
                        }
                    ]
                },
            },
        },
    }


def _service_manifest(
    *,
    name: str,
    namespace: str,
    labels: dict[str, str],
    service: TargetServiceContract,
    deployment_name: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "annotations": {
                "chamber.ampule.dev/deployment-name": deployment_name,
            },
        },
        "spec": {
            "selector": {
                "app.kubernetes.io/name": service.name,
                "chamber.ampule.dev/run-id": labels["chamber.ampule.dev/run-id"],
            },
            "ports": [
                {
                    "name": service.port_name,
                    "port": service.port,
                    "targetPort": service.port_name,
                }
            ],
            "type": "ClusterIP",
        },
    }


def _http_probe(*, path: str, port_name: str) -> dict[str, Any]:
    return {
        "httpGet": {
            "path": path,
            "port": port_name,
        },
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "failureThreshold": 3,
    }


def _readiness_checks(
    *,
    namespace: str,
    deployment_name: str,
    service_name: str,
    service: TargetServiceContract,
) -> tuple[ReadinessCheck, ...]:
    return (
        ReadinessCheck(
            name="deployment-rollout",
            description="Deployment rollout completes with available replicas.",
            target=f"deployment/{deployment_name} in namespace {namespace}",
        ),
        ReadinessCheck(
            name="service-endpoints",
            description="Service has ready endpoints for the target pods.",
            target=f"service/{service_name}:{service.port}",
        ),
        ReadinessCheck(
            name="readiness-probe",
            description="Target readiness endpoint returns HTTP 200 through the pod probe.",
            target=service.readiness_endpoint,
        ),
        ReadinessCheck(
            name="health-probe",
            description="Target health endpoint returns HTTP 200 through the pod probe.",
            target=service.health_endpoint,
        ),
        ReadinessCheck(
            name="startup-logs",
            description="Initial pod logs are collected for fatal startup errors.",
            target=f"deployment/{deployment_name}",
        ),
    )


def _chamber_labels(*, scenario_id: str, run_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "ampule-chamber",
        "app.kubernetes.io/part-of": "ampule-chamber",
        "chamber.ampule.dev/scenario-id": _label_value(scenario_id),
        "chamber.ampule.dev/run-id": _label_value(run_id),
    }


def _trace_suffix(scenario_id: str, run_id: str) -> str:
    return sha1(f"{scenario_id}:{run_id}".encode()).hexdigest()[:8]


def _kubernetes_name(*parts: str, suffix: str) -> str:
    cleaned_parts = [_dns_fragment(part) for part in parts if _dns_fragment(part)]
    base = "-".join(cleaned_parts) or "chamber"
    max_base_length = 63 - len(suffix) - 1
    return f"{base[:max_base_length].rstrip('-')}-{suffix}"


def _dns_fragment(value: str) -> str:
    result = []
    previous_dash = False
    for character in value.lower():
        if character.isalnum():
            result.append(character)
            previous_dash = False
        elif not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-")


def _label_value(value: str) -> str:
    fragment = _dns_label(value)
    if not fragment:
        return "unknown"
    return fragment


def _dns_label(value: str) -> str:
    return _dns_fragment(value)[:63].strip("-")


def _mapping(
    value: Any,
    path: str,
    failures: list[PlanningFailure],
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    failures.append(
        PlanningFailure(
            reason="unavailable_manifest_data",
            message="expected a mapping",
            path=path,
        )
    )
    return None


def _service_name(service: dict[str, Any], failures: list[PlanningFailure]) -> None:
    value = service.get("name")
    if not _is_non_empty_string(value):
        failures.append(
            PlanningFailure(
                reason="unavailable_manifest_data",
                message="target service name is required",
                path="target.service.name",
            )
        )
        return
    if not _dns_label(str(value)):
        failures.append(
            PlanningFailure(
                reason="invalid_service_name",
                message="target service name must contain at least one DNS-label character",
                path="target.service.name",
            )
        )


def _service_image(service: dict[str, Any], failures: list[PlanningFailure]) -> None:
    if not _is_non_empty_string(service.get("image")):
        failures.append(
            PlanningFailure(
                reason="missing_image",
                message="target service image is required to generate a Deployment",
                path="target.service.image",
            )
        )


def _service_port(service: dict[str, Any], failures: list[PlanningFailure]) -> None:
    ports = service.get("ports")
    if not isinstance(ports, list) or not ports:
        failures.append(
            PlanningFailure(
                reason="missing_service_port",
                message="at least one target service port is required",
                path="target.service.ports",
            )
        )
        return
    first_port = ports[0]
    if not isinstance(first_port, dict) or not isinstance(first_port.get("port"), int):
        failures.append(
            PlanningFailure(
                reason="missing_service_port",
                message="first target service port must include an integer port",
                path="target.service.ports[0].port",
            )
        )
        return
    port = first_port["port"]
    if port <= 0 or port > 65535:
        failures.append(
            PlanningFailure(
                reason="missing_service_port",
                message="first target service port must be between 1 and 65535",
                path="target.service.ports[0].port",
            )
        )


def _endpoint(
    service: dict[str, Any],
    key: str,
    failures: list[PlanningFailure],
) -> None:
    if not _is_non_empty_string(service.get(key)):
        failures.append(
            PlanningFailure(
                reason="unavailable_manifest_data",
                message=f"{key} is required for probe generation",
                path=f"target.service.{key}",
            )
        )


def _replicas(environment: dict[str, Any], failures: list[PlanningFailure]) -> None:
    replicas = environment.get("replicas")
    if not isinstance(replicas, int) or replicas <= 0:
        failures.append(
            PlanningFailure(
                reason="invalid_replicas",
                message="environment replicas must be a positive integer",
                path="environment.replicas",
            )
        )


def _resources(environment: dict[str, Any], failures: list[PlanningFailure]) -> None:
    resources = environment.get("resources")
    if not isinstance(resources, dict):
        failures.append(
            PlanningFailure(
                reason="invalid_resources",
                message="environment resources must be a mapping",
                path="environment.resources",
            )
        )
        return
    missing = sorted(RESOURCE_KEYS - set(resources))
    if missing:
        failures.append(
            PlanningFailure(
                reason="invalid_resources",
                message=f"environment resources are missing: {', '.join(missing)}",
                path="environment.resources",
            )
        )
        return
    for key in sorted(RESOURCE_KEYS):
        if not _is_non_empty_string(resources[key]):
            failures.append(
                PlanningFailure(
                    reason="invalid_resources",
                    message=f"environment resource {key} must be a non-empty string",
                    path=f"environment.resources.{key}",
                )
            )


def _require_non_empty(value: str, path: str) -> None:
    if not _is_non_empty_string(value):
        raise EnvironmentPlanningError(f"{path} must be a non-empty string")


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
