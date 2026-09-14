"""Reusable single-host chamber environments and admission budgets."""

from __future__ import annotations

import builtins
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chamber.runs import write_json_atomic


class ChamberProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    context: str = Field(min_length=1, max_length=200, pattern=r"\S")
    namespace: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    service: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    workload: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    prometheus_url: str = ""
    max_vus: int = Field(default=25, ge=1, le=100)
    max_duration_seconds: int = Field(default=300, ge=20, le=7200)
    allow_faults: bool = False
    chaos_mesh: bool = False


class ChamberStore:
    def __init__(self, workspace: Path):
        self.directory = workspace / "chambers"
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, profile: ChamberProfile) -> dict[str, Any]:
        payload = {"id": uuid.uuid4().hex, "revision": 1, **profile.model_dump()}
        write_json_atomic(self.directory / f"{payload['id']}.json", payload)
        return payload

    def get(self, chamber_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", chamber_id):
            raise ValueError("Invalid chamber ID")
        return json.loads((self.directory / f"{chamber_id}.json").read_text())

    def list(self) -> builtins.list[dict[str, Any]]:
        return [json.loads(path.read_text()) for path in sorted(self.directory.glob("*.json"))]

    def bind(self, config: dict[str, Any], chamber_id: str) -> None:
        profile = self.get(chamber_id)
        runtime = config.setdefault("runtime", {})
        if runtime.get("provider") != "kubernetes" or runtime.get("mode") != "attach":
            raise ValueError("Named chambers require Kubernetes attach mode")
        if (runtime.get("kubernetesContext"), runtime.get("namespace")) != (
            profile["context"],
            profile["namespace"],
        ):
            raise ValueError("Target context and namespace must match the selected chamber")
        if config["service"]["name"] != profile["service"] or any(
            item.get("name") != profile["workload"] for item in config["deployment"]["workloads"]
        ):
            raise ValueError("Target service and workload must match the selected chamber")
        config["chamber"] = profile
        validate_budget(config)


def validate_budget(config: dict[str, Any]) -> None:
    profile = config.get("chamber")
    if not profile:
        return
    runtime = config["runtime"]
    if runtime.get("faults") or config.get("experiment", {}).get("family") not in {
        None,
        "queue_drain",
    }:
        if not profile["allow_faults"]:
            raise ValueError("This chamber does not allow faults")
    if (
        config.get("experiment", {}).get("family") not in {None, "queue_drain"}
        and not profile["chaos_mesh"]
    ):
        raise ValueError("This chamber has no configured Chaos Mesh capability")
    if config["traffic"].get("load"):
        from chamber.load.suite import validate_suite

        validate_suite(config["traffic"])
        load = config["traffic"]["load"]
        if load["maxInFlight"] > profile["max_vus"]:
            raise ValueError("Load suite exceeds the chamber in-flight budget")
        duration = (
            sum(stage["durationSeconds"] for stage in load["stages"])
            + load["warmupSeconds"]
            + load["recoverySeconds"]
            + load["timeoutSeconds"]
            * (
                len(load["stages"]) + bool(load["warmupSeconds"]) + bool(load["recoverySeconds"])
                if load["model"] == "capacity"
                else 1
            )
        )
        if config.get("experiment"):
            duration += config["experiment"]["recoverySeconds"] * 2 + load["timeoutSeconds"] * 2
        if duration > profile["max_duration_seconds"]:
            raise ValueError("Load suite and drain exceed the chamber duration budget")
        return
    duration = 0.0
    for journey in config["traffic"]["journeys"]:
        stages = journey.get("stages", [])
        vus = max([journey.get("vus", 1), *(stage["targetVus"] for stage in stages)])
        if vus > profile["max_vus"]:
            raise ValueError("Traffic exceeds the chamber VU budget")
        if stages:
            for stage in stages:
                match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", str(stage["duration"]))
                if not match:
                    raise ValueError("Cannot bound the journey duration")
                duration += float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match[2]]
        else:
            # Fixed iteration requests may each use their full lifecycle/request timeout.
            duration += journey.get("iterations", 1) * journey.get("relayna", {}).get(
                "timeoutSeconds", 60
            )
    recovery_windows = (
        1
        if config.get("experiment", {}).get("family") == "queue_drain"
        else len(config["traffic"]["journeys"])
    )
    duration += config.get("experiment", {}).get("recoverySeconds", 0) * recovery_windows
    if duration > profile["max_duration_seconds"]:
        raise ValueError("Traffic and recovery exceed the chamber duration budget")


def target_key(config: dict[str, Any], context: str | None = None) -> str | None:
    runtime = config.get("runtime", {})
    if runtime.get("provider") != "kubernetes":
        return None
    # Namespace-wide exclusion also prevents separate chamber profiles competing for capacity.
    identity = [
        context or runtime.get("kubernetesContext"),
        runtime.get("namespace", runtime.get("namespaceBase")),
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()
