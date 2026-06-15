"""Controlled fault planning for chamber scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chamber.contracts.scenario import Scenario
from chamber.environment import EnvironmentMetadata, ServiceResource
from chamber.load.planning import parse_duration_seconds

DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
DEPENDENCY_ERRORS = "dependency_errors"
DEPENDENCY_RATE_LIMIT = "dependency_rate_limit"
DEPENDENCY_LATENCY = "dependency_latency"
NETWORK_LOSS = "network_loss"
POD_KILL = "pod_kill"
CPU_PRESSURE = "cpu_pressure"
MEMORY_PRESSURE = "memory_pressure"
NO_FAULT = "none"


class FaultPlanningError(ValueError):
    """Raised when a fault plan cannot be generated."""


@dataclass(frozen=True)
class FaultAction:
    """A reversible dry-run or live-local fault command."""

    action_type: str
    name: str
    description: str
    offset_seconds: int
    command: tuple[str, ...]
    manifest: dict[str, Any] | None = None
    manifest_path: str | None = None


@dataclass(frozen=True)
class FaultEvent:
    """Timeline event emitted by a planned fault."""

    event_type: str
    name: str
    offset_seconds: int
    description: str
    target: str


@dataclass(frozen=True)
class FaultPlan:
    """Deterministic fault plan for one scenario."""

    run_id: str
    scenario_id: str
    namespace: str
    actions: tuple[FaultAction, ...]
    events: tuple[FaultEvent, ...]


def plan_faults(
    scenario: Scenario,
    *,
    environment: EnvironmentMetadata,
    artifact_dir: str | Path,
) -> FaultPlan:
    """Build a deterministic fault plan from scenario faults."""

    actions: list[FaultAction] = []
    events: list[FaultEvent] = []
    for index, fault in enumerate(_faults(scenario)):
        fault_type = fault["type"]
        if fault_type == NO_FAULT:
            continue
        if fault_type == MEMORY_PRESSURE:
            _append_resource_pressure(
                scenario,
                fault,
                index=index,
                environment=environment,
                actions=actions,
                events=events,
                pressure_type="memory",
            )
            continue
        if fault_type == CPU_PRESSURE:
            _append_resource_pressure(
                scenario,
                fault,
                index=index,
                environment=environment,
                actions=actions,
                events=events,
                pressure_type="cpu",
            )
            continue
        if fault_type == POD_KILL:
            _append_pod_kill(
                fault, index=index, environment=environment, actions=actions, events=events
            )
            continue
        if fault_type in {DEPENDENCY_ERRORS, DEPENDENCY_RATE_LIMIT, DEPENDENCY_LATENCY}:
            _append_dependency_response_fault(
                fault,
                index=index,
                environment=environment,
                actions=actions,
                events=events,
                fault_type=fault_type,
            )
            continue
        if fault_type in {DEPENDENCY_UNAVAILABLE, NETWORK_LOSS}:
            _append_dependency_unavailable(
                scenario,
                fault,
                index=index,
                environment=environment,
                artifact_dir=Path(artifact_dir),
                actions=actions,
                events=events,
                policy_mode=fault_type,
            )
            continue
        raise FaultPlanningError(f"faults[{index}].type: {fault_type!r} is not implemented")

    return FaultPlan(
        run_id=environment.run_id,
        scenario_id=scenario.scenario_id,
        namespace=environment.namespace,
        actions=tuple(actions),
        events=tuple(events),
    )


def _append_dependency_unavailable(
    scenario: Scenario,
    fault: dict[str, Any],
    *,
    index: int,
    environment: EnvironmentMetadata,
    artifact_dir: Path,
    actions: list[FaultAction],
    events: list[FaultEvent],
    policy_mode: str,
) -> None:
    target = fault.get("target")
    if not isinstance(target, str) or not target.strip():
        raise FaultPlanningError(f"faults[{index}].target: dependency target is required")
    start_after = _duration_field(fault, "startAfter", index=index)
    duration = _duration_field(fault, "duration", index=index)
    remove_at = start_after + duration
    policy_name = f"deny-egress-{_dns_fragment(target)}"
    manifest_path = artifact_dir / f"{scenario.scenario_id}-{policy_name}.yaml"
    manifest = _network_policy_manifest(
        name=policy_name,
        namespace=environment.namespace,
        labels=environment.labels,
    )
    _write_manifest(manifest_path, manifest)
    apply_command = ("kubectl", "apply", "-f", str(manifest_path))
    delete_command = ("kubectl", "delete", "-f", str(manifest_path), "--ignore-not-found=true")
    description = str(fault["description"])
    event_name = "network-loss" if policy_mode == NETWORK_LOSS else "unavailable"

    actions.extend(
        (
            FaultAction(
                action_type="inject",
                name=f"inject-{target}-{event_name}",
                description=description,
                offset_seconds=start_after,
                command=apply_command,
                manifest=manifest,
                manifest_path=str(manifest_path),
            ),
            FaultAction(
                action_type="remove",
                name=f"remove-{target}-{event_name}",
                description=f"Remove {policy_mode} policy for {target}.",
                offset_seconds=remove_at,
                command=delete_command,
                manifest=manifest,
                manifest_path=str(manifest_path),
            ),
        )
    )
    events.extend(
        (
            FaultEvent(
                event_type="fault_start",
                name=f"{target}-{event_name}-start",
                offset_seconds=start_after,
                description=description,
                target=target,
            ),
            FaultEvent(
                event_type="fault_removed",
                name=f"{target}-{event_name}-removed",
                offset_seconds=remove_at,
                description=f"{target} {policy_mode} fault removed.",
                target=target,
            ),
        )
    )


def _append_resource_pressure(
    scenario: Scenario,
    fault: dict[str, Any],
    *,
    index: int,
    environment: EnvironmentMetadata,
    actions: list[FaultAction],
    events: list[FaultEvent],
    pressure_type: str,
) -> None:
    start_after = _optional_duration_field(fault, "startAfter", index=index, default=0)
    duration = _optional_duration_field(
        fault,
        "duration",
        index=index,
        default=_scenario_max_duration(scenario),
    )
    remove_at = start_after + duration
    deployment = environment.resource_names["deployment"]
    namespace = environment.namespace
    service = scenario.document["target"]["service"]
    container_name = str(service["name"])
    resources = scenario.document["environment"]["resources"]
    if pressure_type == "memory":
        pressure_resources = {
            "requests": {"cpu": str(resources["cpuRequest"]), "memory": "64Mi"},
            "limits": {"cpu": str(resources["cpuLimit"]), "memory": "96Mi"},
        }
    elif pressure_type == "cpu":
        pressure_resources = {
            "requests": {"cpu": "50m", "memory": str(resources["memoryRequest"])},
            "limits": {"cpu": "100m", "memory": str(resources["memoryLimit"])},
        }
    else:
        raise FaultPlanningError(f"unsupported resource pressure type {pressure_type!r}")
    pressure_patch = json.dumps(
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": pressure_resources,
                            }
                        ]
                    }
                }
            }
        }
    )
    restore_patch = json.dumps(
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": {
                                    "requests": {
                                        "cpu": str(resources["cpuRequest"]),
                                        "memory": str(resources["memoryRequest"]),
                                    },
                                    "limits": {
                                        "cpu": str(resources["cpuLimit"]),
                                        "memory": str(resources["memoryLimit"]),
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
    )
    description = str(fault["description"])
    actions.extend(
        (
            FaultAction(
                action_type="inject",
                name=f"inject-{pressure_type}-pressure",
                description=description,
                offset_seconds=start_after,
                command=(
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    "deployment",
                    deployment,
                    "--type=strategic",
                    "-p",
                    pressure_patch,
                ),
            ),
            FaultAction(
                action_type="remove",
                name=f"remove-{pressure_type}-pressure",
                description=f"Restore sample-service resources after {pressure_type} pressure.",
                offset_seconds=remove_at,
                command=(
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    "deployment",
                    deployment,
                    "--type=strategic",
                    "-p",
                    restore_patch,
                ),
            ),
        )
    )
    events.extend(
        (
            FaultEvent(
                event_type="fault_start",
                name=f"{pressure_type}-pressure-start",
                offset_seconds=start_after,
                description=description,
                target=deployment,
            ),
            FaultEvent(
                event_type="fault_removed",
                name=f"{pressure_type}-pressure-removed",
                offset_seconds=remove_at,
                description=f"{pressure_type} pressure patch removed.",
                target=deployment,
            ),
        )
    )


def _append_pod_kill(
    fault: dict[str, Any],
    *,
    index: int,
    environment: EnvironmentMetadata,
    actions: list[FaultAction],
    events: list[FaultEvent],
) -> None:
    target = _target_name(fault, default="target")
    start_after = _optional_duration_field(fault, "startAfter", index=index, default=0)
    resource = _service_resource(environment, target=target, default_role="target")
    selector = (
        f"app.kubernetes.io/name={resource.name},"
        f"chamber.ampule.dev/run-id={environment.labels['chamber.ampule.dev/run-id']}"
    )
    description = str(fault["description"])
    actions.append(
        FaultAction(
            action_type="inject",
            name=f"kill-{resource.name}-pod",
            description=description,
            offset_seconds=start_after,
            command=(
                "kubectl",
                "-n",
                environment.namespace,
                "delete",
                "pod",
                "-l",
                selector,
                "--wait=false",
            ),
        )
    )
    events.append(
        FaultEvent(
            event_type="fault_start",
            name=f"{resource.name}-pod-kill",
            offset_seconds=start_after,
            description=description,
            target=resource.name,
        )
    )


def _append_dependency_response_fault(
    fault: dict[str, Any],
    *,
    index: int,
    environment: EnvironmentMetadata,
    actions: list[FaultAction],
    events: list[FaultEvent],
    fault_type: str,
) -> None:
    target = _target_name(fault, default="")
    resource = _service_resource(environment, target=target, default_role="dependency")
    start_after = _duration_field(fault, "startAfter", index=index)
    duration = _duration_field(fault, "duration", index=index)
    remove_at = start_after + duration
    env_patch = _dependency_env_patch(resource, fault_type=fault_type)
    restore_patch = _dependency_env_patch(resource, fault_type=NO_FAULT)
    description = str(fault["description"])
    actions.extend(
        (
            FaultAction(
                action_type="inject",
                name=f"inject-{resource.name}-{fault_type}",
                description=description,
                offset_seconds=start_after,
                command=(
                    "kubectl",
                    "-n",
                    environment.namespace,
                    "patch",
                    "deployment",
                    resource.deployment,
                    "--type=strategic",
                    "-p",
                    env_patch,
                ),
            ),
            FaultAction(
                action_type="remove",
                name=f"remove-{resource.name}-{fault_type}",
                description=f"Restore dependency response behavior for {resource.source_name}.",
                offset_seconds=remove_at,
                command=(
                    "kubectl",
                    "-n",
                    environment.namespace,
                    "patch",
                    "deployment",
                    resource.deployment,
                    "--type=strategic",
                    "-p",
                    restore_patch,
                ),
            ),
        )
    )
    events.extend(
        (
            FaultEvent(
                event_type="fault_start",
                name=f"{resource.name}-{fault_type}-start",
                offset_seconds=start_after,
                description=description,
                target=resource.source_name,
            ),
            FaultEvent(
                event_type="fault_removed",
                name=f"{resource.name}-{fault_type}-removed",
                offset_seconds=remove_at,
                description=f"{fault_type} fault removed for {resource.source_name}.",
                target=resource.source_name,
            ),
        )
    )


def _dependency_env_patch(resource: ServiceResource, *, fault_type: str) -> str:
    fault_status = "0"
    delay_ms = "0"
    if fault_type == DEPENDENCY_ERRORS:
        fault_status = "500"
    elif fault_type == DEPENDENCY_RATE_LIMIT:
        fault_status = "429"
    elif fault_type == DEPENDENCY_LATENCY:
        delay_ms = "1500"
    elif fault_type != NO_FAULT:
        raise FaultPlanningError(f"unsupported dependency response fault {fault_type!r}")
    return json.dumps(
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": resource.name,
                                "env": [
                                    {"name": "FAULT_STATUS", "value": fault_status},
                                    {"name": "FAULT_DELAY_MS", "value": delay_ms},
                                ],
                            }
                        ]
                    }
                }
            }
        }
    )


def _network_policy_manifest(
    *,
    name: str,
    namespace: str,
    labels: dict[str, str],
) -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    "chamber.ampule.dev/run-id": labels["chamber.ampule.dev/run-id"],
                }
            },
            "policyTypes": ["Egress"],
            "egress": [],
        },
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def _duration_field(fault: dict[str, Any], key: str, *, index: int) -> int:
    try:
        return parse_duration_seconds(fault.get(key))
    except ValueError as exc:
        raise FaultPlanningError(f"faults[{index}].{key}: {exc}") from exc


def _optional_duration_field(
    fault: dict[str, Any],
    key: str,
    *,
    index: int,
    default: int,
) -> int:
    if key not in fault:
        return default
    return _duration_field(fault, key, index=index)


def _scenario_max_duration(scenario: Scenario) -> int:
    safety = scenario.document.get("safety")
    if not isinstance(safety, dict):
        return 0
    try:
        return parse_duration_seconds(safety.get("maxDuration"))
    except ValueError as exc:
        raise FaultPlanningError(f"safety.maxDuration: {exc}") from exc


def _faults(scenario: Scenario) -> tuple[dict[str, Any], ...]:
    raw_faults = scenario.document.get("faults", [])
    if not isinstance(raw_faults, list):
        raise FaultPlanningError("faults: expected list")
    faults = []
    for index, fault in enumerate(raw_faults):
        if not isinstance(fault, dict):
            raise FaultPlanningError(f"faults[{index}]: expected mapping")
        if not isinstance(fault.get("type"), str):
            raise FaultPlanningError(f"faults[{index}].type: fault type is required")
        if not isinstance(fault.get("description"), str) or not fault["description"].strip():
            raise FaultPlanningError(f"faults[{index}].description: description is required")
        faults.append(fault)
    return tuple(faults)


def _target_name(fault: dict[str, Any], *, default: str) -> str:
    target = fault.get("target", default)
    if not isinstance(target, str) or not target.strip():
        return default
    return target


def _service_resource(
    environment: EnvironmentMetadata,
    *,
    target: str,
    default_role: str,
) -> ServiceResource:
    resources = environment.service_resources
    if target in {"", "target"}:
        for resource in resources:
            if resource.role == default_role:
                return resource
    for resource in resources:
        if target in {resource.source_name, resource.name, resource.deployment, resource.service}:
            return resource
    raise FaultPlanningError(f"fault target {target!r} is not a chamber-managed service")


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
    return "".join(result).strip("-") or "dependency"
