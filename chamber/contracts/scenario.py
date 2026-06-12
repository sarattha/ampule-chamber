"""Scenario schema parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "chamber.ampule.dev/v1alpha1"
SCENARIO_KIND = "Scenario"

REQUIRED_TOP_LEVEL_KEYS = {
    "apiVersion",
    "kind",
    "metadata",
    "target",
    "environment",
    "baseline",
    "traffic",
    "observability",
    "failureConditions",
    "successConditions",
    "safety",
}

OBSERVABILITY_SIGNALS = {
    "pod_status",
    "kubernetes_events",
    "container_restarts",
    "memory_usage",
    "cpu_usage",
    "cpu_throttling",
    "request_latency",
    "error_rate",
    "logs",
    "traces",
    "queue_depth",
    "dependency_health",
}

FAILURE_CONDITION_TYPES = {
    "oom_killed",
    "restart_count_increase",
    "restart_loop",
    "latency_above_ms",
    "error_rate_above_percent",
    "memory_growth_for",
    "cpu_throttling_above_percent",
    "dependency_unavailable",
    "dependency_error_rate_above_percent",
    "retry_amplification",
    "queue_backlog_not_draining",
    "recovery_time_above",
    "baseline_check_failed",
}

SUCCESS_CONDITION_TYPES = {
    "no_oom_killed",
    "no_restart_loop",
    "latency_below_ms",
    "error_rate_below_percent",
    "memory_growth_below_percent",
    "cpu_throttling_below_percent",
    "dependency_recovers",
    "queue_backlog_drains_within",
    "recovery_time_below",
    "baseline_checks_pass",
}

TRAFFIC_TOOLS = {"none", "k6", "locust", "custom"}
ENVIRONMENT_PROVIDERS = {"docker", "kind", "k3d", "aks", "existing_kubernetes"}
FAULT_TYPES = {
    "none",
    "pod_kill",
    "dependency_unavailable",
    "dependency_latency",
    "dependency_errors",
    "dependency_rate_limit",
    "memory_pressure",
    "cpu_pressure",
    "network_loss",
    "dns_failure",
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario document violates the MVP schema."""


@dataclass(frozen=True)
class Scenario:
    """Validated scenario data."""

    path: Path
    document: dict[str, Any]

    @property
    def scenario_id(self) -> str:
        return str(self.document["metadata"]["id"])

    @property
    def name(self) -> str:
        return str(self.document["metadata"]["name"])

    @property
    def target_service_name(self) -> str:
        return str(self.document["target"]["service"]["name"])


def load_scenario(path: str | Path) -> Scenario:
    """Load and validate a scenario YAML file."""

    scenario_path = Path(path)
    try:
        raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioValidationError(f"{scenario_path}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioValidationError(f"{scenario_path}: scenario must be a YAML mapping")

    validate_scenario_document(raw, source=str(scenario_path))
    return Scenario(path=scenario_path, document=raw)


def validate_scenario_document(document: dict[str, Any], *, source: str = "<memory>") -> None:
    """Validate a scenario document against the phase 01 MVP schema."""

    _require_keys(document, REQUIRED_TOP_LEVEL_KEYS, source)
    _require_equal(document["apiVersion"], SCHEMA_VERSION, f"{source}.apiVersion")
    _require_equal(document["kind"], SCENARIO_KIND, f"{source}.kind")

    metadata = _mapping(document["metadata"], f"{source}.metadata")
    _require_non_empty_string(metadata.get("id"), f"{source}.metadata.id")
    _require_non_empty_string(metadata.get("name"), f"{source}.metadata.name")
    if "tags" in metadata:
        _string_list(metadata["tags"], f"{source}.metadata.tags")

    target = _mapping(document["target"], f"{source}.target")
    service = _mapping(target.get("service"), f"{source}.target.service")
    _require_non_empty_string(service.get("name"), f"{source}.target.service.name")
    runtime = _mapping(target.get("runtime"), f"{source}.target.runtime")
    _require_enum(
        runtime.get("provider"), ENVIRONMENT_PROVIDERS, f"{source}.target.runtime.provider"
    )
    if runtime.get("provider") == "docker":
        _require_non_empty_string(
            runtime.get("composeFile"), f"{source}.target.runtime.composeFile"
        )

    environment = _mapping(document["environment"], f"{source}.environment")
    _require_enum(
        environment.get("provider"), ENVIRONMENT_PROVIDERS, f"{source}.environment.provider"
    )
    _positive_int(environment.get("replicas"), f"{source}.environment.replicas")
    _mapping(environment.get("resources"), f"{source}.environment.resources")
    if "dependencies" in environment:
        dependencies = _list(environment["dependencies"], f"{source}.environment.dependencies")
        for index, dependency in enumerate(dependencies):
            dep_path = f"{source}.environment.dependencies[{index}]"
            dep = _mapping(dependency, dep_path)
            _require_non_empty_string(dep.get("name"), f"{dep_path}.name")
            _require_non_empty_string(dep.get("type"), f"{dep_path}.type")

    baseline = _mapping(document["baseline"], f"{source}.baseline")
    checks = _non_empty_list(baseline.get("checks"), f"{source}.baseline.checks")
    for index, check in enumerate(checks):
        check_path = f"{source}.baseline.checks[{index}]"
        item = _mapping(check, check_path)
        _require_non_empty_string(item.get("type"), f"{check_path}.type")
        _require_non_empty_string(item.get("description"), f"{check_path}.description")

    traffic = _mapping(document["traffic"], f"{source}.traffic")
    _require_enum(traffic.get("tool"), TRAFFIC_TOOLS, f"{source}.traffic.tool")
    stages = _non_empty_list(traffic.get("stages"), f"{source}.traffic.stages")
    for index, stage in enumerate(stages):
        stage_path = f"{source}.traffic.stages[{index}]"
        stage_doc = _mapping(stage, stage_path)
        _require_non_empty_string(stage_doc.get("duration"), f"{stage_path}.duration")
        _non_negative_int(stage_doc.get("targetVus"), f"{stage_path}.targetVus")

    if "faults" in document:
        faults = _list(document["faults"], f"{source}.faults")
        for index, fault in enumerate(faults):
            fault_path = f"{source}.faults[{index}]"
            fault_doc = _mapping(fault, fault_path)
            _require_enum(fault_doc.get("type"), FAULT_TYPES, f"{fault_path}.type")
            _require_non_empty_string(fault_doc.get("description"), f"{fault_path}.description")

    observability = _mapping(document["observability"], f"{source}.observability")
    signals = _non_empty_list(observability.get("signals"), f"{source}.observability.signals")
    for index, signal in enumerate(signals):
        _require_enum(signal, OBSERVABILITY_SIGNALS, f"{source}.observability.signals[{index}]")

    _validate_conditions(
        document["failureConditions"],
        FAILURE_CONDITION_TYPES,
        f"{source}.failureConditions",
    )
    _validate_conditions(
        document["successConditions"],
        SUCCESS_CONDITION_TYPES,
        f"{source}.successConditions",
    )

    safety = _mapping(document["safety"], f"{source}.safety")
    _require_non_empty_string(safety.get("maxDuration"), f"{source}.safety.maxDuration")
    _require_non_empty_string(safety.get("maxVirtualUsers"), f"{source}.safety.maxVirtualUsers")


def _validate_conditions(value: Any, allowed_types: set[str], path: str) -> None:
    conditions = _non_empty_list(value, path)
    for index, condition in enumerate(conditions):
        condition_path = f"{path}[{index}]"
        doc = _mapping(condition, condition_path)
        _require_enum(doc.get("type"), allowed_types, f"{condition_path}.type")
        _require_non_empty_string(doc.get("description"), f"{condition_path}.description")


def _require_keys(document: dict[str, Any], keys: set[str], path: str) -> None:
    missing = sorted(keys - set(document))
    if missing:
        raise ScenarioValidationError(f"{path}: missing required keys: {', '.join(missing)}")


def _require_equal(value: Any, expected: str, path: str) -> None:
    if value != expected:
        raise ScenarioValidationError(f"{path}: expected {expected!r}, got {value!r}")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioValidationError(f"{path}: expected mapping")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScenarioValidationError(f"{path}: expected list")
    return value


def _non_empty_list(value: Any, path: str) -> list[Any]:
    items = _list(value, path)
    if not items:
        raise ScenarioValidationError(f"{path}: expected at least one item")
    return items


def _string_list(value: Any, path: str) -> None:
    items = _list(value, path)
    for index, item in enumerate(items):
        _require_non_empty_string(item, f"{path}[{index}]")


def _require_non_empty_string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{path}: expected non-empty string")


def _require_enum(value: Any, allowed: set[str], path: str) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ScenarioValidationError(f"{path}: expected one of {allowed_values}; got {value!r}")


def _positive_int(value: Any, path: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ScenarioValidationError(f"{path}: expected positive integer")


def _non_negative_int(value: Any, path: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise ScenarioValidationError(f"{path}: expected non-negative integer")
