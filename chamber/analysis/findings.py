"""Evidence-backed reliability finding detection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chamber.contracts.scenario import Scenario
from chamber.observability import EvidenceArtifact
from chamber.orchestrator import ExperimentTimeline

MEMORY_USAGE_PERCENT = 90.0
CPU_USAGE_PERCENT = 90.0
CPU_THROTTLING_PERCENT = 20.0
LATENCY_MS = 1000.0
ERROR_RATE_PERCENT = 5.0
RESTART_LOOP_COUNT = 3


@dataclass(frozen=True)
class Finding:
    """Reliability finding backed by observed evidence."""

    finding_id: str
    signal_type: str
    affected_resource: str
    observed_facts: tuple[str, ...]
    suspected_cause: str
    severity: str
    confidence: str
    evidence_ids: tuple[str, ...]
    related_timeline_ids: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisResult:
    """Detected findings from one chamber evidence set."""

    run_id: str
    scenario_id: str
    findings: tuple[Finding, ...]


def analyze_evidence(
    evidence: tuple[EvidenceArtifact, ...],
    *,
    scenario: Scenario | None = None,
    timeline: ExperimentTimeline | None = None,
    k6_summary_path: str | Path | None = None,
) -> AnalysisResult:
    """Detect MVP failure signals and correlate them to experiment events."""

    findings: list[Finding] = []
    thresholds = _thresholds(scenario)
    for artifact in evidence:
        if artifact.signal_type == "pod_status":
            findings.extend(_pod_status_findings(artifact, timeline=timeline))
        elif artifact.signal_type == "kubernetes_events":
            findings.extend(_event_findings(artifact, timeline=timeline))
        elif artifact.source == "prometheus":
            finding = _prometheus_finding(artifact, thresholds=thresholds, timeline=timeline)
            if finding is not None:
                findings.append(finding)

    if k6_summary_path is not None:
        k6_artifact = _k6_summary_artifact(evidence, k6_summary_path)
        findings.extend(_k6_findings(k6_artifact, thresholds=thresholds, timeline=timeline))
        findings.extend(
            _scenario_cascade_findings(
                k6_artifact,
                scenario=scenario,
                thresholds=thresholds,
                timeline=timeline,
            )
        )

    run_id = evidence[0].run_id if evidence else ""
    scenario_id = (
        evidence[0].scenario_id if evidence else (scenario.scenario_id if scenario else "")
    )
    return AnalysisResult(run_id=run_id, scenario_id=scenario_id, findings=tuple(findings))


def _pod_status_findings(
    artifact: EvidenceArtifact,
    *,
    timeline: ExperimentTimeline | None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for pod in _items(artifact.payload):
        pod_name = _metadata_name(pod, default=artifact.resource)
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if not isinstance(statuses, list):
            continue
        for status in statuses:
            if not isinstance(status, dict):
                continue
            restarts = _number(status.get("restartCount"))
            container = str(status.get("name") or pod_name)
            waiting_reason = _nested_reason(status, "state", "waiting")
            last_reason = _nested_reason(status, "lastState", "terminated")
            evidence_ids = (artifact.evidence_id,)
            if last_reason == "OOMKilled":
                findings.append(
                    Finding(
                        finding_id=f"{artifact.run_id}:oom-killed:{pod_name}:{container}",
                        signal_type="oom_killed",
                        affected_resource=f"{pod_name}/{container}",
                        observed_facts=(
                            f"Container {container} last terminated with reason OOMKilled.",
                            f"Restart count is {int(restarts)}.",
                        ),
                        suspected_cause=(
                            "Container memory demand exceeded its configured or "
                            "node-enforced limit."
                        ),
                        severity="critical",
                        confidence="high",
                        evidence_ids=evidence_ids,
                        related_timeline_ids=_timeline_ids(timeline, signal_type="oom_killed"),
                    )
                )
            if waiting_reason == "CrashLoopBackOff" or restarts >= RESTART_LOOP_COUNT:
                findings.append(
                    Finding(
                        finding_id=f"{artifact.run_id}:restart-loop:{pod_name}:{container}",
                        signal_type="restart_loop",
                        affected_resource=f"{pod_name}/{container}",
                        observed_facts=(
                            "Container "
                            f"{container} waiting reason is {waiting_reason or 'unknown'}.",
                            f"Restart count is {int(restarts)}.",
                        ),
                        suspected_cause=(
                            "The container is repeatedly failing after startup or during load."
                        ),
                        severity="high",
                        confidence="high" if waiting_reason == "CrashLoopBackOff" else "medium",
                        evidence_ids=evidence_ids,
                        related_timeline_ids=_timeline_ids(timeline, signal_type="restart_loop"),
                    )
                )
    return tuple(findings)


def _event_findings(
    artifact: EvidenceArtifact,
    *,
    timeline: ExperimentTimeline | None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for event in _items(artifact.payload):
        reason = str(event.get("reason") or "")
        message = str(event.get("message") or "")
        resource = _involved_object_name(event) or artifact.resource
        if reason in {"OOMKilling", "Killing"} and "oom" in message.lower():
            findings.append(
                Finding(
                    finding_id=f"{artifact.run_id}:event-oom:{resource}",
                    signal_type="oom_killed",
                    affected_resource=resource,
                    observed_facts=(f"Kubernetes event {reason}: {message}",),
                    suspected_cause="Node or kubelet reported memory pressure killing evidence.",
                    severity="critical",
                    confidence="medium",
                    evidence_ids=(artifact.evidence_id,),
                    related_timeline_ids=_timeline_ids(timeline, signal_type="oom_killed"),
                )
            )
    return tuple(findings)


def _prometheus_finding(
    artifact: EvidenceArtifact,
    *,
    thresholds: dict[str, float],
    timeline: ExperimentTimeline | None,
) -> Finding | None:
    value = _max_prometheus_value(artifact.payload)
    if value is None:
        return None

    threshold_by_signal = {
        "memory_usage": thresholds["memory_usage"],
        "cpu_usage": thresholds["cpu_usage"],
        "cpu_throttling": thresholds["cpu_throttling"],
        "request_latency": thresholds["latency_ms"],
        "error_rate": thresholds["error_rate_percent"],
    }
    threshold = threshold_by_signal.get(artifact.signal_type)
    if threshold is None:
        return None
    breached = value >= threshold
    if artifact.signal_type == "cpu_throttling":
        breached = value > threshold
    if not breached:
        return None

    severity = "high" if artifact.signal_type in {"request_latency", "error_rate"} else "medium"
    suspected = {
        "memory_usage": "Memory usage is near the configured limit and may precede OOM kills.",
        "cpu_usage": "CPU usage is saturated during the measured window.",
        "cpu_throttling": "CPU throttling indicates the service is constrained by CPU limits.",
        "request_latency": "Request latency exceeded the scenario or MVP threshold.",
        "error_rate": "Request errors exceeded the scenario or MVP threshold.",
    }[artifact.signal_type]
    unit = "ms" if artifact.signal_type == "request_latency" else "%"
    return Finding(
        finding_id=f"{artifact.run_id}:prometheus:{artifact.signal_type}:{artifact.resource}",
        signal_type=artifact.signal_type,
        affected_resource=artifact.resource,
        observed_facts=(
            f"Observed {artifact.signal_type} {value:g}{unit} >= {threshold:g}{unit}.",
        ),
        suspected_cause=suspected,
        severity=severity,
        confidence="medium",
        evidence_ids=(artifact.evidence_id,),
        related_timeline_ids=_timeline_ids(timeline, signal_type=artifact.signal_type),
    )


def _k6_findings(
    artifact: EvidenceArtifact,
    *,
    thresholds: dict[str, float],
    timeline: ExperimentTimeline | None,
) -> tuple[Finding, ...]:
    metrics = artifact.payload.get("metrics")
    if not isinstance(metrics, dict):
        return ()

    findings: list[Finding] = []
    latency = _k6_metric_value(metrics, "http_req_duration", "p(95)")
    if latency is not None and latency >= thresholds["latency_ms"]:
        findings.append(
            Finding(
                finding_id=f"{artifact.run_id}:k6:request-latency",
                signal_type="request_latency",
                affected_resource=artifact.resource,
                observed_facts=(
                    f"k6 p95 latency was {latency:g}ms "
                    f"against threshold {thresholds['latency_ms']:g}ms.",
                ),
                suspected_cause="Load-runner evidence shows user-visible latency degradation.",
                severity="high",
                confidence="high",
                evidence_ids=(artifact.evidence_id,),
                related_timeline_ids=_timeline_ids(timeline, signal_type="request_latency"),
            )
        )

    failure_rate = _k6_metric_value(metrics, "http_req_failed", "value")
    if failure_rate is not None:
        failure_percent = failure_rate * 100
        if failure_percent > thresholds["error_rate_percent"]:
            findings.append(
                Finding(
                    finding_id=f"{artifact.run_id}:k6:error-rate",
                    signal_type="error_rate",
                    affected_resource=artifact.resource,
                    observed_facts=(
                        f"k6 HTTP failure rate was {failure_percent:g}% "
                        f"against threshold {thresholds['error_rate_percent']:g}%.",
                    ),
                    suspected_cause=(
                        "The service returned elevated errors during traffic or fault windows."
                    ),
                    severity="high",
                    confidence="high",
                    evidence_ids=(artifact.evidence_id,),
                    related_timeline_ids=_timeline_ids(timeline, signal_type="error_rate"),
                )
            )
    return tuple(findings)


def _scenario_cascade_findings(
    artifact: EvidenceArtifact,
    *,
    scenario: Scenario | None,
    thresholds: dict[str, float],
    timeline: ExperimentTimeline | None,
) -> tuple[Finding, ...]:
    if scenario is None:
        return ()
    metrics = artifact.payload.get("metrics")
    if not isinstance(metrics, dict):
        return ()
    failure_rate = _k6_metric_value(metrics, "http_req_failed", "value")
    if failure_rate is None:
        return ()
    failure_percent = failure_rate * 100
    findings: list[Finding] = []
    condition_types = {
        str(condition.get("type"))
        for condition in scenario.document.get("failureConditions", [])
        if isinstance(condition, dict)
    }
    has_dependency_fault = any(
        isinstance(fault, dict) and str(fault.get("type")).startswith("dependency_")
        for fault in scenario.document.get("faults", [])
    )
    if "retry_amplification" in condition_types and has_dependency_fault:
        findings.append(
            Finding(
                finding_id=f"{artifact.run_id}:k6:retry-amplification",
                signal_type="retry_amplification",
                affected_resource=artifact.resource,
                observed_facts=(
                    "Scenario declares retry-amplification as a failure condition.",
                    (
                        "k6 HTTP failure rate during dependency-path traffic was "
                        f"{failure_percent:g}%."
                    ),
                ),
                suspected_cause=(
                    "Dependency degradation may be propagating through the target request path."
                ),
                severity="medium",
                confidence="low",
                evidence_ids=(artifact.evidence_id,),
                related_timeline_ids=_timeline_ids(timeline, signal_type="retry_amplification"),
            )
        )
    if (
        "recovery_time_above" in condition_types
        and failure_percent > thresholds["error_rate_percent"]
    ):
        findings.append(
            Finding(
                finding_id=f"{artifact.run_id}:k6:failed-recovery",
                signal_type="failed_recovery",
                affected_resource=artifact.resource,
                observed_facts=(
                    f"k6 HTTP failure rate was {failure_percent:g}% "
                    f"against recovery threshold {thresholds['error_rate_percent']:g}%.",
                ),
                suspected_cause="The target may not have recovered cleanly after the fault window.",
                severity="high",
                confidence="medium",
                evidence_ids=(artifact.evidence_id,),
                related_timeline_ids=_timeline_ids(timeline, signal_type="failed_recovery"),
            )
        )
    return tuple(findings)


def _k6_summary_artifact(
    evidence: tuple[EvidenceArtifact, ...],
    path: str | Path,
) -> EvidenceArtifact:
    summary_path = Path(path)
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("k6 summary must be a JSON object")
    run_id = evidence[0].run_id if evidence else "unknown-run"
    scenario_id = evidence[0].scenario_id if evidence else "unknown-scenario"
    return EvidenceArtifact(
        evidence_id=f"{run_id}:k6-summary:{summary_path.name}",
        run_id=run_id,
        scenario_id=scenario_id,
        source="k6",
        signal_type="traffic_summary",
        resource=str(summary_path),
        collected_at="from-file",
        start_time=None,
        end_time=None,
        payload=raw,
    )


def _thresholds(scenario: Scenario | None) -> dict[str, float]:
    values = {
        "memory_usage": MEMORY_USAGE_PERCENT,
        "cpu_usage": CPU_USAGE_PERCENT,
        "cpu_throttling": CPU_THROTTLING_PERCENT,
        "latency_ms": LATENCY_MS,
        "error_rate_percent": ERROR_RATE_PERCENT,
    }
    if scenario is None:
        return values
    for condition in scenario.document.get("failureConditions", []):
        if not isinstance(condition, dict):
            continue
        condition_type = condition.get("type")
        if condition_type == "latency_above_ms":
            value = _number(condition.get("thresholdMs"))
            if value > 0:
                values["latency_ms"] = min(values["latency_ms"], value)
        elif condition_type in {"error_rate_above_percent", "dependency_error_rate_above_percent"}:
            value = _number(condition.get("threshold"))
            if value > 0:
                values["error_rate_percent"] = min(values["error_rate_percent"], value)
        elif condition_type == "cpu_throttling_above_percent":
            value = _number(condition.get("threshold"))
            if value > 0:
                values["cpu_throttling"] = min(values["cpu_throttling"], value)
    return values


def _items(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _metadata_name(value: dict[str, Any], *, default: str) -> str:
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("name"), str):
        return str(metadata["name"])
    return default


def _nested_reason(value: dict[str, Any], *keys: str) -> str | None:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, dict) and isinstance(current.get("reason"), str):
        return str(current["reason"])
    return None


def _involved_object_name(event: dict[str, Any]) -> str | None:
    involved = event.get("involvedObject")
    if isinstance(involved, dict) and isinstance(involved.get("name"), str):
        return str(involved["name"])
    return None


def _max_prometheus_value(payload: dict[str, Any]) -> float | None:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    series = data.get("result")
    if not isinstance(series, list):
        return None
    values: list[float] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        sample = item.get("value")
        if isinstance(sample, list) and len(sample) >= 2:
            values.append(_number(sample[1]))
        samples = item.get("values")
        if isinstance(samples, list):
            for ranged_sample in samples:
                if isinstance(ranged_sample, list) and len(ranged_sample) >= 2:
                    values.append(_number(ranged_sample[1]))
    return max(values) if values else None


def _k6_metric_value(metrics: dict[str, Any], metric_name: str, field: str) -> float | None:
    metric = metrics.get(metric_name)
    if not isinstance(metric, dict):
        return None
    if field not in metric:
        return None
    return _number(metric[field])


def _number(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _timeline_ids(
    timeline: ExperimentTimeline | None,
    *,
    signal_type: str,
) -> tuple[str, ...]:
    if timeline is None:
        return ()
    if signal_type in {"error_rate", "request_latency", "dependency_health"}:
        wanted = {"traffic_start", "traffic_stop", "fault_start", "fault_removed"}
    elif signal_type in {
        "oom_killed",
        "restart_loop",
        "memory_usage",
        "cpu_usage",
        "cpu_throttling",
    }:
        wanted = {"traffic_start", "traffic_stop"}
    else:
        wanted = {"traffic_start", "traffic_stop", "fault_start", "fault_removed"}
    return tuple(event.name for event in timeline.events if event.event_type in wanted)
