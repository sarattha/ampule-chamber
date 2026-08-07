"""Bounded reliability-goal proposals for the control-plane wizard."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_PRESETS: dict[str, dict[str, Any]] = {
    "baseline_readiness": {
        "label": "Baseline readiness",
        "description": "Confirm steady readiness and request success before injecting failure.",
        "method": "GET",
        "path": "/health",
        "expected_status": 200,
        "stages": (("30s", 4), ("30s", 0)),
        "outcomes": (
            "The endpoint continues returning the expected status.",
            "The target remains ready with no unexpected restarts.",
        ),
        "evidence": (
            "pod_status",
            "kubernetes_events",
            "logs",
            "request_latency",
            "error_rate",
        ),
        "recommended_fault": "none",
        "fault_summary": "No injected fault",
    },
    "pod_recovery": {
        "label": "Pod recovery",
        "description": (
            "Measure whether traffic and readiness recover after one target pod is lost."
        ),
        "method": "GET",
        "path": "/health",
        "expected_status": 200,
        "stages": (("20s", 4), ("35s", 4), ("20s", 0)),
        "outcomes": (
            "A replacement pod becomes ready within the bounded recovery window.",
            "The endpoint returns to the expected status without a restart loop.",
        ),
        "evidence": (
            "pod_status",
            "kubernetes_events",
            "container_restarts",
            "request_latency",
            "error_rate",
            "logs",
        ),
        "recommended_fault": "pod_kill",
        "fault_summary": "Pod loss is recommended but remains disabled",
    },
    "dependency_degradation": {
        "label": "Dependency degradation",
        "description": "Observe service behavior while a declared dependency is degraded.",
        "method": "GET",
        "path": "/health",
        "expected_status": 200,
        "stages": (("30s", 4), ("40s", 4), ("20s", 0)),
        "outcomes": (
            "Dependency errors remain bounded and visible.",
            "The target recovers after the dependency is restored.",
        ),
        "evidence": (
            "dependency_health",
            "request_latency",
            "error_rate",
            "logs",
            "kubernetes_events",
        ),
        "recommended_fault": "none",
        "fault_summary": "Dependency fault input required; no fault is selected",
    },
    "backpressure": {
        "label": "Queue or task backpressure",
        "description": (
            "Apply bounded concurrency and verify queued work drains after traffic stops."
        ),
        "method": "POST",
        "path": "/tasks",
        "expected_status": 202,
        "stages": (("25s", 4), ("45s", 8), ("20s", 0)),
        "outcomes": (
            "Admission stays within the expected response contract.",
            "The queue drains after load returns to zero.",
        ),
        "evidence": (
            "queue_depth",
            "request_latency",
            "error_rate",
            "cpu_usage",
            "memory_usage",
            "logs",
        ),
        "recommended_fault": "none",
        "fault_summary": "Traffic creates pressure; no injected fault",
    },
    "memory_oom": {
        "label": "Memory and OOM recovery",
        "description": "Track memory behavior and recovery around an explicit pressure mechanism.",
        "method": "GET",
        "path": "/health",
        "expected_status": 200,
        "stages": (("30s", 4), ("60s", 4), ("30s", 0)),
        "outcomes": (
            "OOM kills and restart behavior are captured when pressure is applied.",
            "The target returns to readiness without a restart loop.",
        ),
        "evidence": (
            "memory_usage",
            "container_restarts",
            "pod_status",
            "kubernetes_events",
            "logs",
        ),
        "recommended_fault": "none",
        "fault_summary": "Memory pressure input required; no fault is selected",
    },
    "latency_error_regression": {
        "label": "Latency and error regression",
        "description": (
            "Capture bounded latency and error evidence for comparison with another run."
        ),
        "method": "GET",
        "path": "/health",
        "expected_status": 200,
        "stages": (("20s", 4), ("50s", 10), ("20s", 0)),
        "outcomes": (
            "Latency and error-rate evidence is available for comparison.",
            "The service returns to steady readiness after peak load.",
        ),
        "evidence": (
            "request_latency",
            "error_rate",
            "cpu_usage",
            "memory_usage",
            "pod_status",
            "logs",
        ),
        "recommended_fault": "none",
        "fault_summary": "No injected fault",
    },
}


def goal_catalog() -> tuple[dict[str, str], ...]:
    """Return stable presentation metadata for the supported reliability goals."""

    return tuple(
        {"id": goal_id, "label": str(item["label"]), "description": str(item["description"])}
        for goal_id, item in _PRESETS.items()
    )


def propose_goal(
    goal: str,
    *,
    service_name: str = "",
    workload_name: str = "",
    service_port: int | None = None,
    request_path: str = "",
    repository_available: bool = False,
    attach_mode: bool = False,
    discovery_complete: bool = False,
    dependency_names: tuple[str, ...] = (),
    telemetry_available: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build one bounded proposal while making discovery gaps explicit."""

    try:
        preset = deepcopy(_PRESETS[goal])
    except KeyError as exc:
        raise ValueError(f"unknown reliability goal: {goal}") from exc

    selected_path = request_path.strip() or str(preset["path"])
    stages = [
        {"duration": duration, "targetVus": target_vus} for duration, target_vus in preset["stages"]
    ]
    max_vus = max(stage["targetVus"] for stage in stages)
    duration_seconds = sum(_seconds(stage["duration"]) for stage in stages)
    service = service_name.strip()
    workload = workload_name.strip()
    dependencies = tuple(name.strip() for name in dependency_names if name.strip())
    telemetry = {item.strip() for item in telemetry_available if item.strip()}

    assumptions = [
        (
            f"The selected endpoint {selected_path!r} represents the {preset['label'].lower()} "
            "request path."
        ),
        (
            f"HTTP {preset['method']} should return {preset['expected_status']} throughout the "
            "exercise unless the selected goal expects bounded disruption."
        ),
        f"Traffic is capped at {max_vus} VUs for {duration_seconds} seconds.",
    ]
    missing_inputs: list[str] = []
    if service:
        assumptions.append(f"Traffic targets the discovered or entered Service {service!r}.")
    else:
        missing_inputs.append("Select or discover the target Service name.")
    if service_port is None:
        missing_inputs.append("Confirm the target Service port.")
    if attach_mode and workload:
        assumptions.append(f"Recovery evidence is correlated to workload {workload!r}.")
    elif attach_mode:
        missing_inputs.append("Select or discover the backing workload.")
    if repository_available:
        assumptions.append("Repository inspection is available for target and dependency context.")
    elif attach_mode:
        assumptions.append(
            "The service is attached without a repository; source-level endpoints and dependencies "
            "cannot be inferred."
        )
    if attach_mode and not discovery_complete:
        assumptions.append(
            "Attach values were entered by the operator and were not fully discovered."
        )
    if goal == "dependency_degradation":
        if dependencies:
            assumptions.append(f"Declared dependencies: {', '.join(dependencies)}.")
        else:
            missing_inputs.append("Choose the dependency and its bounded degradation mechanism.")
    if goal == "backpressure":
        missing_inputs.append("Confirm the task admission request body and queue-depth signal.")
    if goal == "memory_oom":
        missing_inputs.append("Configure an approved memory-pressure mechanism in Advanced mode.")
    if goal in {"backpressure", "memory_oom", "latency_error_regression"} and (
        "prometheus" not in telemetry
    ):
        missing_inputs.append("Add a Prometheus URL to collect the required runtime metrics.")

    recommended_fault = str(preset["recommended_fault"])
    if recommended_fault != "none":
        assumptions.append(
            f"{recommended_fault} is recommended, but faults remain disabled until explicitly "
            "selected."
        )
    journey = {
        "name": goal.replace("_", "-"),
        "method": str(preset["method"]),
        "path": selected_path,
        "expectedStatus": int(preset["expected_status"]),
        "tool": "k6",
        "requestEncoding": "none",
        "stages": stages,
    }
    return {
        "goal": goal,
        "label": preset["label"],
        "description": preset["description"],
        "journeys": [journey],
        "recommendedFault": recommended_fault,
        "selectedFault": "none",
        "faultSummary": preset["fault_summary"],
        "expectedOutcomes": list(preset["outcomes"]),
        "requiredEvidence": list(preset["evidence"]),
        "safety": {
            "maxVirtualUsers": max_vus,
            "maxDurationSeconds": duration_seconds,
            "maxFaults": 1 if recommended_fault != "none" else 0,
            "faultsRequireExplicitSelection": True,
        },
        "assumptions": assumptions,
        "missingInputs": missing_inputs,
        "requestPreview": {
            "method": preset["method"],
            "path": selected_path,
            "expectedStatus": preset["expected_status"],
            "service": service or "<service required>",
            "port": service_port or "<port required>",
        },
    }


def _seconds(duration: str) -> int:
    unit = duration[-1]
    value = int(duration[:-1])
    return value * (60 if unit == "m" else 1)
