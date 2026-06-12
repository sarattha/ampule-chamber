"""Controlled fault planning for chamber scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chamber.contracts.scenario import Scenario
from chamber.environment import EnvironmentMetadata
from chamber.load.planning import parse_duration_seconds

DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
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
            _append_memory_pressure(
                scenario,
                fault,
                index=index,
                environment=environment,
                actions=actions,
                events=events,
            )
            continue
        if fault_type != DEPENDENCY_UNAVAILABLE:
            raise FaultPlanningError(
                f"faults[{index}].type: {fault_type!r} is reserved for a later phase"
            )
        _append_dependency_unavailable(
            scenario,
            fault,
            index=index,
            environment=environment,
            artifact_dir=Path(artifact_dir),
            actions=actions,
            events=events,
        )

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

    actions.extend(
        (
            FaultAction(
                action_type="inject",
                name=f"inject-{target}-unavailable",
                description=description,
                offset_seconds=start_after,
                command=apply_command,
                manifest=manifest,
                manifest_path=str(manifest_path),
            ),
            FaultAction(
                action_type="remove",
                name=f"remove-{target}-unavailable",
                description=f"Remove dependency-unavailable policy for {target}.",
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
                name=f"{target}-unavailable-start",
                offset_seconds=start_after,
                description=description,
                target=target,
            ),
            FaultEvent(
                event_type="fault_removed",
                name=f"{target}-unavailable-removed",
                offset_seconds=remove_at,
                description=f"{target} dependency-unavailable fault removed.",
                target=target,
            ),
        )
    )


def _append_memory_pressure(
    scenario: Scenario,
    fault: dict[str, Any],
    *,
    index: int,
    environment: EnvironmentMetadata,
    actions: list[FaultAction],
    events: list[FaultEvent],
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
    pressure_patch = json.dumps(
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
                                        "memory": "64Mi",
                                    },
                                    "limits": {
                                        "cpu": str(resources["cpuLimit"]),
                                        "memory": "96Mi",
                                    },
                                },
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
                name="inject-memory-pressure",
                description=description,
                offset_seconds=start_after,
                command=(
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    "deployment",
                    deployment,
                    "--type=merge",
                    "-p",
                    pressure_patch,
                ),
            ),
            FaultAction(
                action_type="remove",
                name="remove-memory-pressure",
                description="Restore the sample-service memory limit after pressure window.",
                offset_seconds=remove_at,
                command=(
                    "kubectl",
                    "-n",
                    namespace,
                    "patch",
                    "deployment",
                    deployment,
                    "--type=merge",
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
                name="memory-pressure-start",
                offset_seconds=start_after,
                description=description,
                target=deployment,
            ),
            FaultEvent(
                event_type="fault_removed",
                name="memory-pressure-removed",
                offset_seconds=remove_at,
                description="Memory pressure patch removed.",
                target=deployment,
            ),
        )
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
