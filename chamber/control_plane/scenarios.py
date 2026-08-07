"""Durable scenario catalog and control-plane normalization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from chamber.contracts.scenario import SCENARIO_KIND, SCHEMA_VERSION, validate_scenario_document
from chamber.load.planning import TrafficPlanningError, parse_duration_seconds
from chamber.workflow import WorkflowError, validate_config

MAX_SCENARIO_BYTES = 256 * 1024
SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")
PLACEHOLDER_PATTERN = re.compile(r"\$\{[^}]+\}|\{\{[^}]+\}\}")


class ScenarioCatalogError(ValueError):
    """Raised for invalid, unsafe, or incompatible catalog operations."""


class ScenarioCatalog:
    """Read bundled scenarios and atomically persist user ChamberConfigs."""

    def __init__(self, workspace: Path, bundled_dir: Path) -> None:
        self.workspace = workspace.resolve()
        self.bundled_dir = bundled_dir.resolve()
        self.user_dir = self.workspace / "scenarios"
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def list(self, validate_journeys: JourneyValidator) -> tuple[dict[str, Any], ...]:
        entries = []
        for source, directory in (("bundled", self.bundled_dir), ("user", self.user_dir)):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")):
                try:
                    normalized = normalize_document(
                        _read_document(path), source=source, validate_journeys=validate_journeys
                    )
                except (OSError, ScenarioCatalogError, ValueError, WorkflowError):
                    continue
                entries.append(_metadata(normalized, source=source))
        return tuple(entries)

    def read(
        self, source: str, scenario_id: str, validate_journeys: JourneyValidator
    ) -> dict[str, Any]:
        path = self._path(source, scenario_id)
        if not path.is_file():
            raise FileNotFoundError(scenario_id)
        return normalize_document(
            _read_document(path), source=source, validate_journeys=validate_journeys
        )

    def save(
        self,
        document: dict[str, Any],
        *,
        replace: bool,
        validate_journeys: JourneyValidator,
    ) -> dict[str, Any]:
        payload, result = self._prepare_save(document, validate_journeys)
        scenario_id = str(result["identity"]["id"])
        path = self._path("user", scenario_id)
        collision_message = (
            f"scenario {scenario_id!r} already exists; confirm replacement explicitly"
        )
        if path.exists() and not replace:
            raise FileExistsError(collision_message)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temporary, path)
            else:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    raise FileExistsError(collision_message) from None
        finally:
            temporary.unlink(missing_ok=True)
        return result

    def prepare_save(
        self,
        document: dict[str, Any],
        *,
        replace: bool,
        validate_journeys: JourneyValidator,
    ) -> dict[str, Any]:
        """Validate and preview the exact saved result without publishing it."""

        _, result = self._prepare_save(document, validate_journeys)
        scenario_id = str(result["identity"]["id"])
        if self._path("user", scenario_id).exists() and not replace:
            raise FileExistsError(
                f"scenario {scenario_id!r} already exists; confirm replacement explicitly"
            )
        return result

    def _prepare_save(
        self,
        document: dict[str, Any],
        validate_journeys: JourneyValidator,
    ) -> tuple[str, dict[str, Any]]:
        normalized = normalize_document(
            document, source="user", validate_journeys=validate_journeys
        )
        if normalized["kind"] != "ChamberConfig":
            raise ScenarioCatalogError("user scenarios must be saved as a ChamberConfig")
        stored = dict(document)
        scenario = dict(_mapping(stored.get("scenario")))
        scenario.update(normalized["identity"])
        scenario["source"] = "user"
        scenario.pop("revision", None)
        stored["scenario"] = scenario
        revision = _revision(stored)
        scenario["revision"] = revision
        payload = yaml.safe_dump(stored, sort_keys=False)
        if len(payload.encode()) > MAX_SCENARIO_BYTES:
            raise ScenarioCatalogError("scenario document exceeds the 256 KiB limit")
        result = normalize_document(stored, source="user", validate_journeys=validate_journeys)
        result["identity"]["revision"] = revision
        return payload, result

    def _path(self, source: str, scenario_id: str) -> Path:
        _validate_id(scenario_id)
        if source not in {"bundled", "user"}:
            raise ScenarioCatalogError("scenario source must be bundled or user")
        root = self.bundled_dir if source == "bundled" else self.user_dir
        path = (root / f"{scenario_id}.yaml").resolve()
        if not path.is_relative_to(root):
            raise ScenarioCatalogError("scenario path is outside the catalog")
        if source == "bundled" and not path.exists():
            matches = []
            for candidate in root.glob("*.yaml"):
                try:
                    document = _read_document(candidate)
                except (OSError, ScenarioCatalogError):
                    continue
                metadata = _mapping(document.get("metadata"))
                if metadata.get("id") == scenario_id:
                    matches.append(candidate.resolve())
            if len(matches) == 1:
                return matches[0]
        return path


JourneyValidator = Callable[[str], list[dict[str, Any]]]


def parse_scenario_document(content: str) -> dict[str, Any]:
    """Parse bounded YAML or JSON input into a mapping."""

    if len(content.encode()) > MAX_SCENARIO_BYTES:
        raise ScenarioCatalogError("scenario document exceeds the 256 KiB limit")
    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ScenarioCatalogError(f"scenario is not valid YAML or JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ScenarioCatalogError("scenario document must be a YAML or JSON mapping")
    return value


def normalize_document(
    document: dict[str, Any],
    *,
    source: str,
    validate_journeys: JourneyValidator,
) -> dict[str, Any]:
    """Validate a supported document and return one editable UI projection."""

    _bounded(document)
    api_version = document.get("apiVersion")
    kind = document.get("kind")
    if api_version != SCHEMA_VERSION:
        raise ScenarioCatalogError(f"apiVersion must be {SCHEMA_VERSION!r}; got {api_version!r}")
    if kind not in {SCENARIO_KIND, "ChamberConfig"}:
        raise ScenarioCatalogError("kind must be Scenario or ChamberConfig")
    placeholders = _placeholder_paths(document)
    if placeholders:
        raise ScenarioCatalogError(
            "resolve target-dependent placeholders before use: " + ", ".join(placeholders)
        )
    if kind == SCENARIO_KIND:
        try:
            validate_scenario_document(document, source="scenario")
        except ValueError as exc:
            raise ScenarioCatalogError(str(exc)) from exc
        projection = _scenario_projection(document)
    else:
        try:
            validate_config(document, source="scenario", require_repo=False)
        except WorkflowError as exc:
            raise ScenarioCatalogError(str(exc)) from exc
        projection = _config_projection(document)
    journeys = validate_journeys(json.dumps(projection["journeys"]))
    if kind == SCENARIO_KIND:
        _validate_scenario_safety(document, journeys)
    projection["journeys"] = journeys
    projection.update(
        {
            "apiVersion": SCHEMA_VERSION,
            "kind": str(kind),
            "source": source,
            "revision": _revision(document),
        }
    )
    projection["identity"]["source"] = source
    projection["identity"]["revision"] = projection["revision"]
    return projection


def compatibility_warnings(normalized: dict[str, Any], *, service_name: str = "") -> list[str]:
    """Return review-time target and safety warnings without mutating the exercise."""

    warnings = list(normalized.get("warnings", []))
    target = str(normalized.get("targetService", "")).strip()
    if service_name.strip() and target and service_name.strip() != target:
        warnings.append(
            f"Scenario target {target!r} differs from selected service {service_name.strip()!r}; "
            "review paths, ports, and workload compatibility."
        )
    fault = str(normalized.get("recommendedFault", "none"))
    if fault != "none":
        warnings.append(
            f"Scenario defines {fault!r}; it remains disabled until explicitly selected."
        )
    return warnings


def _scenario_projection(document: dict[str, Any]) -> dict[str, Any]:
    metadata = _mapping(document["metadata"])
    target = _mapping(_mapping(document["target"])["service"])
    traffic = _mapping(document["traffic"])
    target_port = _first_service_port(target.get("ports"))
    body = traffic.get("body")
    journey: dict[str, Any] = {
        "name": str(metadata["id"]),
        "method": str(traffic.get("method", "GET")).upper(),
        "path": str(traffic.get("entrypoint", target.get("healthEndpoint", "/health"))),
        "expectedStatus": int(traffic.get("expectedStatus", 200)),
        "tool": str(traffic.get("tool", "k6")),
        "requestEncoding": "json" if body is not None else "none",
        "stages": traffic["stages"],
    }
    if body is not None:
        journey["body"] = body
    faults = [
        str(item.get("type", "none"))
        for item in document.get("faults", [])
        if isinstance(item, dict) and item.get("type") != "none"
    ]
    recommended = faults[0] if faults and faults[0] in {"pod_kill", "deployment_scale"} else "none"
    warnings = []
    unsupported = [fault for fault in faults if fault not in {"pod_kill", "deployment_scale"}]
    if unsupported:
        warnings.append("Unsupported UI fault templates: " + ", ".join(unsupported))
    return {
        "identity": {
            "id": str(metadata["id"]),
            "name": str(metadata["name"]),
            "description": str(metadata.get("description", "")),
            "tags": list(metadata.get("tags", [])),
        },
        "targetService": str(target.get("name", "")),
        "targetServicePort": target_port,
        "journeys": [journey],
        "recommendedFault": recommended,
        "configuredFaults": faults,
        "requiredSignals": list(_mapping(document["observability"]).get("signals", [])),
        "agentMode": "offline",
        "agentExclusions": [],
        "warnings": warnings,
    }


def _config_projection(document: dict[str, Any]) -> dict[str, Any]:
    service = _mapping(document["service"])
    traffic = _mapping(document["traffic"])
    scenario = _mapping(document.get("scenario"))
    scenario_id = str(scenario.get("id", document.get("scenarioId", ""))).strip()
    if not scenario_id:
        scenario_id = f"{service['name']}-assessment"
    _validate_id(scenario_id)
    runtime = _mapping(document.get("runtime"))
    traffic_access = _mapping(runtime.get("trafficAccess"))
    target_port = traffic_access.get("servicePort")
    if not isinstance(target_port, int) or isinstance(target_port, bool):
        target_port = None
    faults = [
        str(item.get("type", "none"))
        for item in runtime.get("faults", [])
        if isinstance(item, dict) and item.get("type") != "none"
    ]
    recommended = faults[0] if faults and faults[0] in {"pod_kill", "deployment_scale"} else "none"
    agents = _mapping(document.get("agents"))
    exclusions = agents.get("exclude", [])
    return {
        "identity": {
            "id": scenario_id,
            "name": str(scenario.get("name", scenario_id)),
            "description": str(scenario.get("description", "")),
            "tags": list(scenario.get("tags", [])),
        },
        "targetService": str(service["name"]),
        "targetServicePort": target_port,
        "journeys": list(traffic["journeys"]),
        "recommendedFault": recommended,
        "configuredFaults": faults,
        "requiredSignals": list(scenario.get("requiredSignals", [])),
        "agentMode": str(agents.get("mode", "offline")),
        "agentExclusions": list(exclusions) if isinstance(exclusions, list) else [],
        "origin": scenario.get("origin"),
        "warnings": [],
    }


def _first_service_port(value: Any) -> int | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        port = item.get("port")
        if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
            return port
    return None


def _validate_scenario_safety(document: dict[str, Any], journeys: list[dict[str, Any]]) -> None:
    safety = _mapping(document.get("safety"))
    raw_max_vus = safety.get("maxVirtualUsers")
    if not isinstance(raw_max_vus, str) or not raw_max_vus.strip().isdigit():
        raise ScenarioCatalogError("scenario.safety.maxVirtualUsers must be a positive integer")
    max_vus = int(raw_max_vus)
    if max_vus <= 0:
        raise ScenarioCatalogError("scenario.safety.maxVirtualUsers must be a positive integer")
    try:
        max_duration = parse_duration_seconds(safety.get("maxDuration"))
    except TrafficPlanningError as exc:
        raise ScenarioCatalogError(f"scenario.safety.maxDuration: {exc}") from exc

    projected_vus = 0
    projected_duration = 0
    for journey in journeys:
        vus = journey.get("vus")
        if isinstance(vus, int) and not isinstance(vus, bool):
            projected_vus = max(projected_vus, vus)
        duration_seconds = journey.get("durationSeconds")
        if isinstance(duration_seconds, int) and not isinstance(duration_seconds, bool):
            projected_duration += duration_seconds
        stages = journey.get("stages", [])
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            target_vus = stage.get("targetVus")
            if isinstance(target_vus, int) and not isinstance(target_vus, bool):
                projected_vus = max(projected_vus, target_vus)
            try:
                projected_duration += parse_duration_seconds(stage.get("duration"))
            except TrafficPlanningError as exc:
                raise ScenarioCatalogError(f"scenario.traffic stage duration: {exc}") from exc
    if projected_vus > max_vus:
        raise ScenarioCatalogError(
            f"scenario traffic requires {projected_vus} VUs but "
            f"safety.maxVirtualUsers allows {max_vus}"
        )
    if projected_duration > max_duration:
        raise ScenarioCatalogError(
            f"scenario traffic duration {projected_duration}s exceeds "
            f"safety.maxDuration {safety['maxDuration']}"
        )


def _metadata(normalized: dict[str, Any], *, source: str) -> dict[str, Any]:
    journeys = normalized["journeys"]
    adapters = sorted({str(item.get("adapter", "http")) for item in journeys})
    max_vus = 0
    durations = []
    for journey in journeys:
        max_vus = max(max_vus, int(journey.get("vus", 0) or 0))
        for stage in journey.get("stages", []):
            if isinstance(stage, dict):
                max_vus = max(max_vus, int(stage.get("targetVus", 0) or 0))
                if stage.get("duration"):
                    durations.append(str(stage["duration"]))
        if journey.get("durationSeconds"):
            durations.append(f"{journey['durationSeconds']}s")
    identity = normalized["identity"]
    return {
        **identity,
        "source": source,
        "revision": normalized["revision"],
        "kind": normalized["kind"],
        "trafficAdapters": adapters,
        "journeyCount": len(journeys),
        "maxVirtualUsers": max_vus,
        "expectedDuration": " + ".join(durations) or "not specified",
        "faults": normalized["configuredFaults"],
        "requiredSignals": normalized["requiredSignals"],
        "warnings": normalized["warnings"],
    }


def _read_document(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise
    return parse_scenario_document(content)


def _bounded(document: dict[str, Any]) -> None:
    try:
        size = len(json.dumps(document, sort_keys=True).encode())
    except (TypeError, ValueError) as exc:
        raise ScenarioCatalogError("scenario contains unsupported values") from exc
    if size > MAX_SCENARIO_BYTES:
        raise ScenarioCatalogError("scenario document exceeds the 256 KiB limit")


def _revision(document: dict[str, Any]) -> str:
    value = dict(document)
    scenario = _mapping(value.get("scenario"))
    if scenario:
        value["scenario"] = {key: item for key, item in scenario.items() if key != "revision"}
    payload = yaml.safe_dump(value, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _validate_id(value: str) -> None:
    if not SCENARIO_ID_PATTERN.fullmatch(value):
        raise ScenarioCatalogError(
            "scenario ID must be 1-63 lowercase letters, numbers, dots, or hyphens"
        )


def _placeholder_paths(value: Any, path: str = "document") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_placeholder_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_placeholder_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str) and PLACEHOLDER_PATTERN.search(value):
        found.append(path)
    return found


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
