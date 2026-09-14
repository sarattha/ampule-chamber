"""Bounded guided experiments with controller and scoped telemetry evidence."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from chamber.runs import write_json_atomic

FAMILIES = {
    "dependency_delay": "Dependency latency",
    "dependency_outage": "Dependency outage",
    "cpu_pressure": "CPU pressure",
    "memory_pressure": "Memory pressure",
    "queue_drain": "Queue backpressure and drain",
}


def validate_experiment(config: dict[str, Any]) -> None:
    experiment = config.get("experiment")
    if not experiment:
        return
    if not isinstance(experiment, dict) or experiment.get("family") not in FAMILIES:
        raise ValueError("Unknown experiment family")
    runtime = config.get("runtime", {})
    if runtime.get("provider") != "kubernetes" or runtime.get("mode") != "attach":
        raise ValueError("Experiments require Kubernetes attach mode")
    if runtime.get("faults"):
        raise ValueError("Select one experiment or a legacy fault, not both")
    for key, default, low, high in (
        ("durationSeconds", 30, 10, 300),
        ("recoverySeconds", 30, 5, 300),
    ):
        value = experiment.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"experiment.{key} must be between {low} and {high}")
        experiment[key] = value
    family = experiment["family"]
    if family != "queue_drain":
        for key in ("pod", "dependencyPod") if family.startswith("dependency") else ("pod",):
            if not re.fullmatch(
                r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", str(experiment.get(key, ""))
            ):
                raise ValueError(f"experiment.{key} must identify an explicit pod")
        if family.startswith("dependency") and experiment["pod"] == experiment["dependencyPod"]:
            raise ValueError("Dependency pod must differ from target pod")
        for key, default, low, high in (
            ("latencyMs", 100, 1, 5000),
            ("cpuLoad", 50, 1, 100),
            ("memoryMiB", 64, 1, 512),
        ):
            value = experiment.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"experiment.{key} must be between {low} and {high}")
            experiment[key] = value
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", str(experiment.get("container", ""))):
            raise ValueError("Select an explicit target container")
    if family == "queue_drain":
        if not runtime.get("prometheusUrl"):
            raise ValueError("Queue experiments require a Prometheus URL")
        for key in ("depthQuery", "ageQuery"):
            query = experiment.get(key, "")
            if not isinstance(query, str) or not query or len(query) > 2000:
                raise ValueError(f"experiment.{key} is required (maximum 2000 characters)")
        labels = experiment.get("queueLabels", {})
        if (
            not isinstance(labels, dict)
            or not labels
            or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in labels.items())
        ):
            raise ValueError("Queue identity labels are required")
        for key, default in (("maxDepth", 100), ("maxAgeSeconds", 60)):
            value = experiment.get(key, default)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"experiment.{key} must be positive and finite")
            experiment[key] = value


def chaos_document(experiment: dict[str, Any], namespace: str, name: str) -> dict[str, Any]:
    family = experiment["family"]
    spec: dict[str, Any] = {
        "mode": "all",
        "selector": {"pods": {namespace: [experiment["pod"]]}},
        "duration": f"{experiment['durationSeconds']}s",
    }
    if family.startswith("dependency"):
        spec.update(
            {
                "action": "delay" if family == "dependency_delay" else "loss",
                "direction": "to",
                "target": {
                    "mode": "all",
                    "selector": {"pods": {namespace: [experiment["dependencyPod"]]}},
                },
            }
        )
        spec["delay" if family == "dependency_delay" else "loss"] = (
            {"latency": f"{experiment['latencyMs']}ms"}
            if family == "dependency_delay"
            else {"loss": "100", "correlation": "100"}
        )
        kind = "NetworkChaos"
    else:
        kind = "StressChaos"
        spec["containerNames"] = [experiment["container"]]
        spec["stressors"] = (
            {"cpu": {"workers": 1, "load": experiment["cpuLoad"]}}
            if family == "cpu_pressure"
            else {"memory": {"workers": 1, "size": f"{experiment['memoryMiB']}MB"}}
        )
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


class ExperimentSession:
    """Persist intent before mutation and always attempt controller recovery."""

    def __init__(self, config: dict[str, Any], run_dir: Path, runner: Any, context: str):
        self.config = config
        self.spec = config["experiment"]
        self.run_dir = run_dir
        self.runner = runner
        self.base = ("kubectl", "--context", context, "-n", config["runtime"]["namespace"])
        self.resource: str | None = None
        self.evidence: dict[str, Any] = {
            "family": self.spec["family"],
            "assertions": [],
            "commands": [],
            "samples": [],
            "restored": self.spec["family"] == "queue_drain",
        }
        self.path = run_dir / "evidence/experiment.json"

    def persist(self) -> None:
        write_json_atomic(self.path, self.evidence)

    def command(self, *args: str) -> Any:
        result = self.runner.run((*self.base, *args))
        self.evidence["commands"].append(
            {
                "command": list(args),
                "exit_status": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timestamp": time.time(),
            }
        )
        self.persist()
        if result.returncode:
            raise RuntimeError(f"Experiment command failed: {' '.join(args[:3])}")
        return result

    def start(self) -> None:
        self.evidence["started_at"] = time.time()
        self.persist()
        if self.spec["family"] == "queue_drain":
            self.sample("baseline")
            return
        namespace = self.config["runtime"]["namespace"]
        # Check each explicitly selected pod's opt-in, not an unrelated workload label.
        for pod in dict.fromkeys(
            [
                self.spec["pod"],
                *(
                    [self.spec["dependencyPod"]]
                    if self.spec["family"].startswith("dependency")
                    else []
                ),
            ]
        ):
            payload = json.loads(self.command("get", "pod", pod, "-o", "json").stdout)
            if (
                payload.get("metadata", {}).get("labels", {}).get("chamber.ampule.dev/allow-faults")
                != "true"
            ):
                raise ValueError(f"Pod {pod} requires chamber.ampule.dev/allow-faults=true")
            if pod == self.spec["pod"] and self.spec["container"] not in [
                c["name"] for c in payload.get("spec", {}).get("containers", [])
            ]:
                raise ValueError("Selected container was not discovered on target pod")
        document = chaos_document(self.spec, namespace, "ampule-" + self.run_dir.name[-24:].lower())
        resource = document["kind"].lower() + ".chaos-mesh.org/" + document["metadata"]["name"]
        path = self.run_dir / "experiment-resource.json"
        write_json_atomic(path, document)
        # Persist recovery identity before creation, including ambiguous command failures.
        self.resource = resource
        self.evidence["resource"] = resource
        self.persist()
        self.command("create", "-f", str(path))
        self.command("wait", resource, "--for=condition=AllInjected", "--timeout=30s")
        observed = json.loads(self.command("get", resource, "-o", "json").stdout)
        self.evidence["injection"] = observed.get("status", {})
        conditions = self.evidence["injection"].get("conditions", [])
        injected = any(
            c.get("type") == "AllInjected" and c.get("status") == "True" for c in conditions
        )
        self.assertion(
            "Fault injected on selected pods", "pass" if injected else "missing", True, injected
        )
        if not injected:
            raise RuntimeError("Controller did not confirm fault injection")

    def record_load(self, success: bool) -> None:
        if not self.resource:
            return
        observed = json.loads(self.command("get", self.resource, "-o", "json").stdout)
        self.evidence["after_load"] = observed.get("status", {})
        still_injected = any(
            c.get("type") == "AllInjected" and c.get("status") == "True"
            for c in self.evidence["after_load"].get("conditions", [])
        )
        self.assertion(
            "Traffic meets contract during fault",
            "missing" if not still_injected else "pass" if success else "fail",
            "Traffic succeeds while fault remains injected",
            success
            if still_injected
            else "Fault ended before traffic completed; increase duration",
        )

    def sample(self, phase: str) -> None:
        if self.spec["family"] != "queue_drain":
            return
        sample: dict[str, Any] = {"phase": phase, "timestamp": time.time()}
        if phase == "recovery":
            self.evidence.setdefault("recovery_started_at", sample["timestamp"])
        for name, key in (("depth", "depthQuery"), ("age", "ageQuery")):
            try:
                url = (
                    self.config["runtime"]["prometheusUrl"].rstrip("/")
                    + "/api/v1/query?"
                    + urlencode({"query": self.spec[key]})
                )
                with urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read(1024 * 1024))
                series = payload["data"]["result"]
                if payload.get("status") != "success" or len(series) != 1:
                    raise ValueError("Query must return exactly one queue series")
                item = series[0]
                labels = {
                    **self.spec["queueLabels"],
                    "namespace": self.config["runtime"]["namespace"],
                }
                if any(item["metric"].get(k) != v for k, v in labels.items()):
                    raise ValueError("Queue identity or namespace mismatch")
                timestamp, value = map(float, item["value"])
                if (
                    not math.isfinite(value)
                    or value < 0
                    or not math.isfinite(timestamp)
                    or not 0 <= time.time() - timestamp <= 15
                ):
                    raise ValueError("Queue metric is stale, nonfinite, or negative")
                sample[name] = value
                sample[name + "_timestamp"] = timestamp
            except Exception as exc:
                sample[name] = None
                sample[name + "_error"] = str(exc)
        self.evidence["samples"].append(sample)
        self.persist()

    def assertion(self, name: str, state: str, expected: Any, observed: Any) -> None:
        self.evidence["assertions"].append(
            {
                "name": name,
                "state": state,
                "expected": expected,
                "observed": observed,
                "evidence_id": "experiment",
            }
        )
        self.persist()

    def finish(self) -> None:
        if self.resource:
            try:
                self.command(
                    "annotate", self.resource, "experiment.chaos-mesh.org/pause=true", "--overwrite"
                )
                self.command("wait", self.resource, "--for=condition=AllRecovered", "--timeout=60s")
                observed = json.loads(self.command("get", self.resource, "-o", "json").stdout)
                self.evidence["recovery"] = observed.get("status", {})
                restored = any(
                    c.get("type") == "AllRecovered" and c.get("status") == "True"
                    for c in self.evidence["recovery"].get("conditions", [])
                )
                self.command("delete", self.resource, "--wait=true", "--timeout=60s")
                self.evidence["restored"] = restored
            except Exception as exc:
                self.evidence["recovery_error"] = str(exc)
                self.evidence["manual_remediation"] = (
                    f"Inspect {self.resource}, restore fault through Chaos Mesh, "
                    "and verify affected pods."
                )
            self.assertion(
                "Fault restored",
                "pass" if self.evidence["restored"] else "fail",
                True,
                self.evidence["restored"],
            )
        self.persist()

    def evaluate_queue(self) -> None:
        samples = self.evidence["samples"]
        complete = all(s.get("depth") is not None and s.get("age") is not None for s in samples)
        complete = complete and sum(s["phase"] == "load" for s in samples) >= 2
        complete = complete and all(
            any(s["phase"] == p for s in samples) for p in ("baseline", "load", "recovery")
        )
        if not complete:
            self.assertion(
                "Queue depth and age coverage",
                "missing",
                "Fresh scoped baseline, load and recovery samples",
                None,
            )
            return
        peak = max(s["depth"] for s in samples)
        age = max(s["age"] for s in samples)
        growth = max(s["depth"] for s in samples if s["phase"] == "load") > samples[0]["depth"]
        self.assertion(
            "Backpressure exercised",
            "pass" if growth else "missing",
            "Queue grows during load",
            growth,
        )
        self.assertion(
            "Queue depth stays within budget",
            "pass" if peak <= self.spec["maxDepth"] else "fail",
            self.spec["maxDepth"],
            peak,
        )
        self.assertion(
            "Oldest task age stays within budget",
            "pass" if age <= self.spec["maxAgeSeconds"] else "fail",
            self.spec["maxAgeSeconds"],
            age,
        )
        recovery = []
        for sample in samples:
            if sample["phase"] != "recovery":
                continue
            identity = (sample.get("depth_timestamp"), sample.get("age_timestamp"))
            if not recovery or identity != (
                recovery[-1].get("depth_timestamp"),
                recovery[-1].get("age_timestamp"),
            ):
                recovery.append(sample)
        started = self.evidence.get("recovery_started_at", float("inf"))
        fresh = len(recovery) >= 2 and all(
            started
            <= recovery[-2].get(name + "_timestamp", -1)
            < recovery[-1].get(name + "_timestamp", -1)
            for name in ("depth", "age")
        )
        drained = fresh and all(s["depth"] == 0 and s["age"] == 0 for s in recovery[-2:])
        self.assertion(
            "Queue drains after admissions stop",
            "missing" if not fresh else "pass" if drained else "fail",
            "Two distinct post-admission-stop empty queue samples for depth and age",
            {"depth": samples[-1]["depth"], "age": samples[-1]["age"]},
        )
