"""Guided-run analysis and conclusive assessment result assembly."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chamber.analysis import Finding, analyze_evidence
from chamber.observability import EvidenceArtifact
from chamber.runs import registered_evidence

SEVERITY_PENALTIES = {"critical": 40, "high": 25, "medium": 10, "low": 5}
PROMETHEUS_REQUIRED_QUERIES = (
    "container_memory_working_set_bytes",
    "container_cpu_usage_seconds_total",
    "kube_pod_container_status_restarts_total",
)


def analyze_guided_run(
    run_dir: Path,
    *,
    config: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Analyze persisted guided-run evidence with the shared detector set."""

    run_id = str(metadata.get("run_id", run_dir.name))
    service_value = config.get("service")
    service: dict[str, Any] = service_value if isinstance(service_value, dict) else {}
    scenario_id = str(config.get("scenarioId", f"{service.get('name', 'service')}-assessment"))
    evidence = _command_evidence(run_dir, run_id=run_id, scenario_id=scenario_id)
    if not evidence:
        evidence = (
            EvidenceArtifact(
                evidence_id="run-metadata",
                run_id=run_id,
                scenario_id=scenario_id,
                source="ampule-chamber",
                signal_type="run_metadata",
                resource=str(run_dir),
                collected_at=_now(),
                start_time=None,
                end_time=None,
                payload={},
            ),
        )
    summary_path = _k6_summary_path(run_dir, metadata)
    result = analyze_evidence(
        evidence,
        k6_summary_path=summary_path if summary_path and summary_path.exists() else None,
    )
    findings = []
    seen: set[str] = set()
    for finding in result.findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        payload = asdict(finding)
        payload["recommendations"] = list(_recommendations(finding))
        findings.append(payload)
    return tuple(findings)


def build_assessment_result(
    run_dir: Path,
    *,
    config: dict[str, Any],
    metadata: dict[str, Any],
    findings: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Build a result that cannot report readiness without required evidence."""

    stage = str(metadata.get("stage", "planned"))
    mode = str(metadata.get("mode", "local"))
    runtime_value = config.get("runtime")
    runtime: dict[str, Any] = runtime_value if isinstance(runtime_value, dict) else {}
    metadata_runtime_value = metadata.get("runtime")
    metadata_runtime = metadata_runtime_value if isinstance(metadata_runtime_value, dict) else {}
    runtime_mode = str(runtime.get("mode", metadata.get("runtime_mode", "deploy")))
    required = ["preflight", "kubernetes-commands", _traffic_evidence_id(config)]
    if runtime.get("prometheusUrl") or metadata_runtime.get("prometheus_url"):
        required.append("prometheus-memory")
    if runtime_mode == "attach":
        required.extend(("attach-discovery", "pre-test-state", "rollback"))
    available = {str(item.get("evidence_id")) for item in registered_evidence(run_dir)}
    prometheus_available, prometheus_limitations = _prometheus_evidence_status(
        run_dir, required="prometheus-memory" in required
    )
    present = [
        item
        for item in required
        if item in available and (item != "prometheus-memory" or prometheus_available)
    ]
    missing = [item for item in required if item not in present]
    evidence_coverage = round(100 * len(present) / len(required)) if required else 100
    rollback_value = metadata.get("rollback")
    rollback: dict[str, Any] = rollback_value if isinstance(rollback_value, dict) else {}
    rollback_verified = bool(rollback.get("verified", True))
    traffic_value = metadata.get("traffic_result")
    traffic: dict[str, Any] = traffic_value if isinstance(traffic_value, dict) else {}
    traffic_success = bool(traffic.get("success", metadata.get("success", False)))

    status: str
    conclusive = False
    score: int | None = None
    if stage == "cancelled":
        status = "cancelled"
    elif stage in {"failed", "preflight_failed"} or not rollback_verified:
        status = "failed"
    elif mode != "kubernetes":
        status = "inconclusive"
        missing = ["live-kubernetes-execution"]
        evidence_coverage = 0
    elif missing:
        status = "inconclusive"
    elif not traffic_success:
        status = "failed"
    else:
        conclusive = True
        score = _score(findings)
        has_critical = any(str(item.get("severity")) == "critical" for item in findings)
        has_high = any(str(item.get("severity")) == "high" for item in findings)
        if has_critical or score < 70:
            status = "not_ready"
        elif score >= 90 and not has_high:
            status = "ready"
        else:
            status = "conditional"

    return {
        "schema_version": "chamber.ampule.dev/result/v1",
        "run_id": str(metadata.get("run_id", run_dir.name)),
        "status": status,
        "conclusive": conclusive,
        "readiness_score": score,
        "evidence_coverage_percent": evidence_coverage,
        "execution_coverage_percent": 100 if stage == "assessed" else 0,
        "rollback_verified": rollback_verified,
        "cleanup_verified": bool(metadata.get("cleanup_performed"))
        if runtime_mode != "attach"
        else True,
        "required_evidence_ids": required,
        "available_evidence_ids": sorted(available),
        "missing_evidence_ids": missing,
        "evidence_limitations": list(prometheus_limitations),
        "finding_count": len(findings),
        "generated_at": _now(),
    }


def _prometheus_evidence_status(
    run_dir: Path,
    *,
    required: bool,
) -> tuple[bool, tuple[str, ...]]:
    if not required:
        return True, ()
    path = run_dir / "evidence/prometheus-memory.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, (f"Prometheus evidence is unreadable: {exc}",)
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, dict):
        return False, ("Prometheus evidence does not contain required queries.",)
    expected_pod_names = _string_values(payload.get("pod_names"))
    limitations = []
    for name in PROMETHEUS_REQUIRED_QUERIES:
        query = queries.get(name)
        if not isinstance(query, dict):
            limitations.append(f"Prometheus query {name} is missing.")
            continue
        if query.get("ok") is not True:
            error = query.get("error")
            limitations.append(
                f"Prometheus query {name} failed"
                + (f": {error}" if isinstance(error, str) and error else ".")
            )
            continue
        series_count = query.get("series_count")
        if not isinstance(series_count, int) or series_count < 1:
            limitations.append(
                f"Prometheus query {name} succeeded but returned zero matching series."
            )
            continue
        _, missing_pod_names = prometheus_query_pod_coverage(query, expected_pod_names)
        if missing_pod_names:
            limitations.append(
                f"Prometheus query {name} returned no series for selected pods: "
                + ", ".join(missing_pod_names)
                + "."
            )
    return not limitations, tuple(limitations)


def prometheus_query_pod_coverage(
    query: dict[str, Any],
    expected_pod_names: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return observed and missing selected pods for one persisted query result."""

    observed = set(_string_values(query.get("observed_pod_names")))
    series = query.get("series")
    if isinstance(series, list):
        for item in series:
            metric = item.get("metric") if isinstance(item, dict) else None
            pod_name = metric.get("pod") if isinstance(metric, dict) else None
            if isinstance(pod_name, str) and pod_name:
                observed.add(pod_name)
    expected = tuple(dict.fromkeys(expected_pod_names))
    return tuple(sorted(observed)), tuple(name for name in expected if name not in observed)


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _command_evidence(
    run_dir: Path,
    *,
    run_id: str,
    scenario_id: str,
) -> tuple[EvidenceArtifact, ...]:
    path = run_dir / "evidence/kubernetes-commands.json"
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(commands, list):
        return ()
    artifacts = []
    for index, item in enumerate(commands, start=1):
        if not isinstance(item, dict):
            continue
        exit_status = item.get("exit_status")
        if not isinstance(exit_status, int) or exit_status != 0:
            continue
        command = item.get("command")
        stdout = item.get("stdout")
        if not isinstance(command, list) or not isinstance(stdout, str):
            continue
        words = [str(value) for value in command]
        signal_type: str | None = None
        evidence_payload: dict[str, Any] | None = None
        if "get" in words and ("pods" in words or "pod" in words) and "json" in words:
            signal_type = "pod_status"
            evidence_payload = _json_object(stdout)
            if evidence_payload is not None and "items" not in evidence_payload:
                evidence_payload = {"items": [evidence_payload]}
        elif "get" in words and "events" in words and "json" in words:
            signal_type = "kubernetes_events"
            evidence_payload = _json_object(stdout)
        if signal_type is None or evidence_payload is None:
            continue
        artifacts.append(
            EvidenceArtifact(
                evidence_id=f"kubernetes-command-{index}",
                run_id=run_id,
                scenario_id=scenario_id,
                source="kubernetes",
                signal_type=signal_type,
                resource=_resource(words, run_dir.name),
                collected_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                start_time=None,
                end_time=None,
                payload=evidence_payload,
            )
        )
    return tuple(artifacts)


def _k6_summary_path(run_dir: Path, metadata: dict[str, Any]) -> Path | None:
    traffic_value = metadata.get("traffic_result")
    traffic: dict[str, Any] = traffic_value if isinstance(traffic_value, dict) else {}
    value = traffic.get("summary_path")
    candidates = [run_dir / "evidence/k6-summary.json"]
    if isinstance(value, str) and value:
        supplied = Path(value)
        candidates.insert(0, supplied if supplied.is_absolute() else run_dir / supplied)
        candidates.append(run_dir / "evidence" / supplied.name)
    return next((item for item in candidates if item.exists()), None)


def _traffic_evidence_id(config: dict[str, Any]) -> str:
    traffic = config.get("traffic")
    journeys = traffic.get("journeys") if isinstance(traffic, dict) else None
    if (
        isinstance(journeys, list)
        and journeys
        and all(isinstance(item, dict) and item.get("adapter") == "relayna" for item in journeys)
    ):
        return "relayna-summary"
    return "k6-summary"


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _resource(command: list[str], default: str) -> str:
    for value in command:
        if value.startswith(("pod/", "deployment/", "service/")):
            return value
    return default


def _score(findings: tuple[dict[str, Any], ...]) -> int:
    return max(
        0,
        100 - sum(SEVERITY_PENALTIES.get(str(item.get("severity", "low")), 0) for item in findings),
    )


def _recommendations(finding: Finding) -> tuple[str, ...]:
    return {
        "oom_killed": (
            "Profile memory under the reproduced traffic profile.",
            "Review limits only after ruling out unbounded retention or queues.",
        ),
        "restart_loop": (
            "Inspect container termination logs and startup dependencies.",
            "Retest after correcting the crash trigger or probe behavior.",
        ),
        "request_latency": (
            "Trace the slow request path and correlate it with CPU and dependency latency.",
        ),
        "error_rate": (
            "Review dependency, timeout, retry, and fallback behavior during the failing window.",
        ),
        "retry_amplification": (
            "Bound retries with backoff and verify downstream request amplification.",
        ),
        "failed_recovery": (
            "Add a recovery check and confirm error rate returns to baseline after fault removal.",
        ),
    }.get(finding.signal_type, ("Review the cited evidence and rerun after remediation.",))


def _now() -> str:
    return datetime.now(UTC).isoformat()
