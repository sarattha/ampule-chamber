"""Generic raw-Kubernetes real-service onboarding planning."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import yaml

from chamber.environment import EnvironmentAction, ReadinessCheck

DEFAULT_REPOSITORY_REF = "external-working-tree"
SECRET_MARKER = "<redacted>"


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
class ImageBuildSpec:
    """Operator-provided local image build input."""

    name: str
    dockerfile: str
    image: str
    context: str = "."


@dataclass(frozen=True)
class ImageReplacement:
    """Image replacement for adapted manifests."""

    source: str
    target: str


@dataclass(frozen=True)
class WorkloadSpec:
    """Operator-provided workload role and readiness metadata."""

    name: str
    role: str
    kind: str = "Deployment"
    readiness: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkloadPlan:
    """Chamber-owned workload adapted from external manifests."""

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
class TrafficJourney:
    """HTTP traffic journey used by load planning and reports."""

    tool: str
    method: str
    entrypoint: str
    expected_status: int
    body: dict[str, Any] | None = None


@dataclass(frozen=True)
class FollowUpCheck:
    """Post-traffic check proving behavior beyond request acceptance."""

    name: str
    check_type: str
    target: str
    expected: str
    service_name: str | None = None


@dataclass(frozen=True)
class EvidenceAttribution:
    """Planned source ownership for a runtime evidence signal."""

    signal_type: str
    source: str
    resource: str
    service_name: str
    role: str


@dataclass(frozen=True)
class LivePreflightResult:
    """Live-run prerequisite result that can be recorded as evidence."""

    ready: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class OnboardingSpec:
    """Generic raw-Kubernetes onboarding input."""

    service_name: str
    repo_path: str | Path
    manifest_paths: tuple[str, ...]
    workload_roles: tuple[WorkloadSpec, ...]
    image_builds: tuple[ImageBuildSpec, ...]
    traffic: TrafficJourney
    scenario_id: str = "external-service-001"
    namespace_base: str = "chamber-external-service"
    repository_ref: str = DEFAULT_REPOSITORY_REF
    image_replacements: tuple[ImageReplacement, ...] = ()
    required_env: tuple[str, ...] = ()
    secret_env: tuple[str, ...] = ()
    external_dependencies: tuple[ExternalDependencyPolicy, ...] = ()
    follow_up_checks: tuple[FollowUpCheck, ...] = ()
    config_overrides: Mapping[str, str] | None = None


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
    follow_up_checks: tuple[FollowUpCheck, ...]
    evidence_attribution: tuple[EvidenceAttribution, ...]
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]


def validate_onboarding_environment(
    required_env: tuple[str, ...],
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return missing environment variables required for a live run."""

    values = os.environ if env is None else env
    return tuple(name for name in required_env if not values.get(name))


def build_onboarding_plan(
    spec: OnboardingSpec,
    *,
    run_id: str,
    env: Mapping[str, str] | None = None,
    kind_context: str = "kind-ampule-chamber",
) -> RealServiceOnboardingPlan:
    """Build a redacted onboarding plan from raw Kubernetes YAML."""

    _validate_spec(spec)
    repository = Path(spec.repo_path)
    labels = _labels(scenario_id=spec.scenario_id, run_id=run_id)
    suffix = sha256(f"{spec.scenario_id}:{run_id}".encode()).hexdigest()[:8]
    namespace = f"{_dns_fragment(spec.namespace_base)}-{suffix}"
    source_manifests = _load_manifests(repository, spec.manifest_paths)
    manifests = (
        _namespace_manifest(namespace, labels),
        *(
            _adapt_manifest(
                item,
                namespace=namespace,
                labels=labels,
                replacements=spec.image_replacements,
                config_overrides=spec.config_overrides or {},
                secret_env=spec.secret_env,
            )
            for item in source_manifests
        ),
    )
    image_builds = tuple(
        _image_build(item, repository=repository, kind_context=kind_context)
        for item in spec.image_builds
    )
    workloads = _workloads(manifests, repository=repository, workload_roles=spec.workload_roles)
    missing = validate_onboarding_environment(spec.required_env, env)
    redacted_config = _redacted_config(spec, os.environ if env is None else env)
    blockers = tuple(f"{name} is required for live onboarding evidence" for name in missing)
    evidence_attribution = _evidence_attribution(
        workloads,
        follow_up_checks=spec.follow_up_checks,
        external_dependencies=spec.external_dependencies,
    )
    return RealServiceOnboardingPlan(
        run_id=run_id,
        service_name=spec.service_name,
        repository_path=str(repository),
        repository_ref=spec.repository_ref,
        namespace=namespace,
        labels=labels,
        image_builds=image_builds,
        workloads=workloads,
        manifests=manifests,
        actions=_actions(manifests),
        readiness_checks=_readiness_checks(workloads, spec.workload_roles, namespace=namespace),
        redacted_config=redacted_config,
        external_dependencies=spec.external_dependencies,
        traffic_journey=_traffic_dict(spec.traffic, spec.follow_up_checks),
        follow_up_checks=spec.follow_up_checks,
        evidence_attribution=evidence_attribution,
        blockers=blockers,
        limitations=_onboarding_limitations(spec),
    )


def preflight_live_onboarding(
    plan: RealServiceOnboardingPlan,
    *,
    env: Mapping[str, str] | None = None,
    required_env: tuple[str, ...] = (),
    prometheus_url: str | None = None,
) -> LivePreflightResult:
    """Return blockers that prevent a live external-service run."""

    values = os.environ if env is None else env
    blockers = list(plan.blockers)
    blockers.extend(
        f"{name} is required for live onboarding evidence"
        for name in required_env
        if not values.get(name)
    )
    if not prometheus_url and not values.get("PROMETHEUS_URL"):
        blockers.append("PROMETHEUS_URL is required for live Prometheus evidence")
    if not plan.image_builds:
        blockers.append("at least one local image build must be planned before live execution")
    return LivePreflightResult(ready=not blockers, blockers=tuple(dict.fromkeys(blockers)))


def _validate_spec(spec: OnboardingSpec) -> None:
    if not spec.service_name.strip():
        raise OnboardingValidationError("service_name is required")
    if not spec.manifest_paths:
        raise OnboardingValidationError("at least one manifest path is required")
    if not spec.workload_roles:
        raise OnboardingValidationError("at least one workload role is required")


def _load_manifests(
    repository: Path, manifest_paths: tuple[str, ...]
) -> tuple[dict[str, Any], ...]:
    documents: list[dict[str, Any]] = []
    for manifest_path in manifest_paths:
        path = repository / manifest_path
        if not path.exists():
            raise OnboardingValidationError(f"required manifest file is missing: {path}")
        with path.open(encoding="utf-8") as handle:
            for item in yaml.safe_load_all(handle):
                if item is None:
                    continue
                if not isinstance(item, dict):
                    raise OnboardingValidationError(f"{path}: YAML documents must be mappings")
                documents.append(item)
    return tuple(documents)


def _adapt_manifest(
    manifest: dict[str, Any],
    *,
    namespace: str,
    labels: dict[str, str],
    replacements: tuple[ImageReplacement, ...],
    config_overrides: Mapping[str, str],
    secret_env: tuple[str, ...],
) -> dict[str, Any]:
    item = _copy(manifest)
    kind = str(item.get("kind", ""))
    if kind == "Namespace":
        return _namespace_manifest(namespace, labels)
    metadata = item.setdefault("metadata", {})
    metadata["namespace"] = namespace
    metadata["labels"] = {**metadata.get("labels", {}), **labels}
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "ScaledJob"}:
        _adapt_pod_template(item, labels=labels, replacements=replacements, secret_env=secret_env)
    if kind == "ConfigMap":
        data = item.setdefault("data", {})
        if isinstance(data, dict):
            for key, value in config_overrides.items():
                data[key] = value
    if kind == "Secret":
        _redact_secret_manifest(item)
    return item


def _adapt_pod_template(
    manifest: dict[str, Any],
    *,
    labels: dict[str, str],
    replacements: tuple[ImageReplacement, ...],
    secret_env: tuple[str, ...],
) -> None:
    template = _pod_template(manifest)
    metadata = template.setdefault("metadata", {})
    metadata["labels"] = {**metadata.get("labels", {}), **labels}
    for container in _containers(template):
        image = container.get("image")
        if isinstance(image, str):
            for replacement in replacements:
                if image == replacement.source:
                    container["image"] = replacement.target
        if container.get("imagePullPolicy") == "Always":
            container["imagePullPolicy"] = "IfNotPresent"
        container.setdefault(
            "resources",
            {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "1", "memory": "768Mi"},
            },
        )
        _redact_container_secret_env(container, secret_env)


def _redact_container_secret_env(container: dict[str, Any], secret_env: tuple[str, ...]) -> None:
    secret_names = set(secret_env)
    for item in container.get("env", []):
        if not isinstance(item, dict) or item.get("name") not in secret_names:
            continue
        if "value" in item:
            item["value"] = SECRET_MARKER


def _pod_template(manifest: dict[str, Any]) -> dict[str, Any]:
    kind = manifest.get("kind")
    if kind == "CronJob":
        return cast(dict[str, Any], manifest["spec"]["jobTemplate"]["spec"]["template"])
    if kind == "ScaledJob":
        return cast(dict[str, Any], manifest["spec"]["jobTargetRef"]["template"])
    if kind == "Job":
        return cast(dict[str, Any], manifest["spec"]["template"])
    return cast(dict[str, Any], manifest["spec"]["template"])


def _containers(template: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    spec = template.setdefault("spec", {})
    containers = spec.get("containers", [])
    init_containers = spec.get("initContainers", [])
    return tuple(item for item in (*containers, *init_containers) if isinstance(item, dict))


def _redact_secret_manifest(manifest: dict[str, Any]) -> None:
    metadata = manifest.setdefault("metadata", {})
    metadata["annotations"] = {
        **metadata.get("annotations", {}),
        "chamber.ampule.dev/redacted": "true",
    }
    for field in ("data", "stringData"):
        values = manifest.get(field)
        if isinstance(values, dict):
            manifest[field] = {str(key): SECRET_MARKER for key in values}


def _workloads(
    manifests: tuple[dict[str, Any], ...],
    *,
    repository: Path,
    workload_roles: tuple[WorkloadSpec, ...],
) -> tuple[WorkloadPlan, ...]:
    roles = {(item.kind, item.name): item.role for item in workload_roles}
    workloads: list[WorkloadPlan] = []
    for manifest in manifests:
        kind = str(manifest.get("kind", ""))
        if kind not in {
            "Deployment",
            "StatefulSet",
            "DaemonSet",
            "Job",
            "CronJob",
            "ScaledJob",
            "Service",
        }:
            continue
        name = str(manifest["metadata"]["name"])
        role = roles.get((kind, name)) or roles.get(("Deployment", name)) or "supporting"
        containers = _containers(_pod_template(manifest)) if kind != "Service" else ()
        image = str(containers[0]["image"]) if containers and containers[0].get("image") else None
        ports = _ports(manifest, containers)
        workloads.append(
            WorkloadPlan(
                name=name,
                role=role,
                kind=kind,
                source_path=str(repository),
                image=image,
                ports=ports,
            )
        )
    return tuple(workloads)


def _ports(manifest: dict[str, Any], containers: tuple[dict[str, Any], ...]) -> tuple[int, ...]:
    if manifest.get("kind") == "Service":
        return tuple(
            int(port["port"])
            for port in manifest.get("spec", {}).get("ports", [])
            if isinstance(port, dict) and isinstance(port.get("port"), int)
        )
    ports: list[int] = []
    for container in containers:
        for port in container.get("ports", []):
            if isinstance(port, dict) and isinstance(port.get("containerPort"), int):
                ports.append(int(port["containerPort"]))
    return tuple(ports)


def _readiness_checks(
    workloads: tuple[WorkloadPlan, ...],
    workload_roles: tuple[WorkloadSpec, ...],
    *,
    namespace: str,
) -> tuple[ReadinessCheck, ...]:
    configured = {(item.kind, item.name): item.readiness for item in workload_roles}
    checks: list[ReadinessCheck] = []
    for workload in workloads:
        for readiness in configured.get((workload.kind, workload.name), ()):
            checks.append(
                ReadinessCheck(
                    name=f"{workload.name}-{readiness}",
                    description=f"Verify {workload.name} {readiness}.",
                    target=f"{workload.kind.lower()}/{workload.name} in namespace {namespace}",
                )
            )
        if workload.kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ScaledJob"}:
            checks.append(
                ReadinessCheck(
                    name=f"{workload.name}-ready",
                    description=f"Verify {workload.name} reaches a ready state.",
                    target=f"{workload.kind.lower()}/{workload.name} in namespace {namespace}",
                )
            )
    return tuple(checks)


def _evidence_attribution(
    workloads: tuple[WorkloadPlan, ...],
    *,
    follow_up_checks: tuple[FollowUpCheck, ...],
    external_dependencies: tuple[ExternalDependencyPolicy, ...],
) -> tuple[EvidenceAttribution, ...]:
    items: list[EvidenceAttribution] = []
    for workload in workloads:
        if workload.kind == "Service":
            continue
        items.append(
            EvidenceAttribution(
                "pod_status", "kubernetes", workload.name, workload.name, workload.role
            )
        )
        items.append(
            EvidenceAttribution("logs", "kubernetes", workload.name, workload.name, workload.role)
        )
    for check in follow_up_checks:
        items.append(
            EvidenceAttribution(
                check.check_type,
                "follow_up",
                check.target,
                check.service_name or check.name,
                "follow_up",
            )
        )
    for dependency in external_dependencies:
        items.append(
            EvidenceAttribution(
                "external_dependency",
                "external",
                dependency.endpoint,
                dependency.name,
                "external",
            )
        )
    return tuple(items)


def _redacted_config(
    spec: OnboardingSpec,
    env: Mapping[str, str],
) -> tuple[RedactedConfigEntry, ...]:
    entries = [
        RedactedConfigEntry(
            name=name,
            source="environment",
            value="<redacted-present>" if env.get(name) else "<missing>",
            secret=name in spec.secret_env,
            required=name in spec.required_env,
        )
        for name in sorted(set((*spec.required_env, *spec.secret_env)))
    ]
    if spec.config_overrides:
        for key, value in sorted(spec.config_overrides.items()):
            entries.append(
                RedactedConfigEntry(
                    name=key,
                    source="onboarding",
                    value=value,
                    secret=False,
                    required=False,
                )
            )
    return tuple(entries)


def _traffic_dict(
    traffic: TrafficJourney,
    follow_up_checks: tuple[FollowUpCheck, ...],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tool": traffic.tool,
        "method": traffic.method,
        "entrypoint": traffic.entrypoint,
        "expectedStatus": traffic.expected_status,
    }
    if traffic.body is not None:
        data["body"] = traffic.body
    if follow_up_checks:
        data["followUps"] = [
            {
                "name": item.name,
                "type": item.check_type,
                "target": item.target,
                "expected": item.expected,
                "serviceName": item.service_name,
            }
            for item in follow_up_checks
        ]
    return data


def _onboarding_limitations(spec: OnboardingSpec) -> tuple[str, ...]:
    limitations = [
        "External repository is read-only and provided by the operator at run time.",
    ]
    if _manifest_paths_need_overlay_rendering(spec.manifest_paths):
        limitations.append(
            "Input manifests were treated as already-rendered Kubernetes YAML; "
            "Helm/Kustomize rendering was not performed by this run."
        )
    return tuple(limitations)


def _manifest_paths_need_overlay_rendering(paths: tuple[str, ...]) -> bool:
    overlay_markers = ("helm", "chart", "kustomize", "kustomization")
    return any(
        any(marker in Path(path).as_posix().lower() for marker in overlay_markers) for path in paths
    )


def _image_build(
    spec: ImageBuildSpec,
    *,
    repository: Path,
    kind_context: str,
) -> ImageBuildPlan:
    context = repository / spec.context
    dockerfile = repository / spec.dockerfile
    return ImageBuildPlan(
        name=spec.name,
        context=str(context),
        dockerfile=str(dockerfile),
        image=spec.image,
        build_command=("docker", "build", "-f", str(dockerfile), "-t", spec.image, str(context)),
        kind_load_command=(
            "kind",
            "load",
            "docker-image",
            spec.image,
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


def _namespace_manifest(namespace: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace, "labels": labels},
    }


def _labels(*, scenario_id: str, run_id: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "ampule-chamber",
        "app.kubernetes.io/part-of": "ampule-chamber",
        "chamber.ampule.dev/scenario-id": _dns_fragment(scenario_id),
        "chamber.ampule.dev/run-id": _dns_fragment(run_id),
    }


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
    return ("".join(result).strip("-") or "external")[:63]


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    copied = yaml.safe_load(yaml.safe_dump(value))
    if not isinstance(copied, dict):
        raise OnboardingValidationError("internal manifest copy did not produce a mapping")
    return cast(dict[str, Any], copied)
