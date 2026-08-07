"""Content-safe correlated evidence projection for operator investigations."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

EXPLORER_SCHEMA_VERSION = "chamber.ampule.dev/evidence-explorer/v1"
MAX_PROJECTED_EVENTS = 1000
MAX_SOURCE_EVENTS = 300
MAX_RELAYNA_TASKS = 200
MAX_RELAYNA_EVENTS_PER_TASK = 5
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
MAX_SAFE_LOG_EVENTS = 100
SUPPORTED_EVIDENCE_IDS = {
    "k6-summary",
    "relayna-summary",
    "relayna-workers",
    "kubernetes-commands",
    "rollback",
    "prometheus-memory",
}
_LOG_EVENT = re.compile(
    r"^(?P<timestamp>\d{4}-\d\d-\d\d[T ][^ ]+)\s+"
    r"(?P<severity>DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<event>[A-Za-z0-9_.:/-]{1,80})(?:\s|$)",
    re.IGNORECASE,
)


def build_evidence_explorer(
    run_dir: Path,
    *,
    run_id: str,
    evidence: tuple[dict[str, Any], ...],
    findings: list[dict[str, Any]],
    run_events: tuple[dict[str, Any], ...],
    config: dict[str, Any],
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize registered evidence into one bounded, filterable timeline."""

    entries = {
        str(item.get("evidence_id")): item
        for item in evidence
        if str(item.get("evidence_id")) in SUPPORTED_EVIDENCE_IDS
    }
    events: list[dict[str, Any]] = []
    events.extend(_run_events(run_events))
    if item := entries.get("k6-summary"):
        events.extend(_k6_events(run_dir, item, config=config))
    if item := entries.get("relayna-summary"):
        events.extend(_relayna_events(run_dir, item))
    if item := entries.get("relayna-workers"):
        events.extend(_worker_events(run_dir, item))
    if item := entries.get("kubernetes-commands"):
        events.extend(_kubernetes_events(run_dir, item))
    if item := entries.get("rollback"):
        events.extend(_rollback_events(run_dir, item))
    if item := entries.get("prometheus-memory"):
        events.extend(_prometheus_events(run_dir, item))

    events.sort(key=lambda value: (value["timestamp"], value["event_id"]))
    original_count = len(events)
    if len(events) > MAX_PROJECTED_EVENTS:
        events = _evenly_bounded(events, MAX_PROJECTED_EVENTS)
    projection = {
        "schema_version": EXPLORER_SCHEMA_VERSION,
        "events": events,
        "source_event_count": original_count,
        "projected_event_count": len(events),
        "truncated": original_count > len(events),
        "supported_evidence_ids": sorted(entries),
        "correlation_legend": {
            "exact": "The item carries its own timestamp and exact source identity.",
            "run_window": "The item is associated by the bounded run window or workload labels.",
            "inferred": "The timestamp or relationship is derived from surrounding run evidence.",
        },
    }
    return query_evidence_explorer(
        projection,
        run_id=run_id,
        findings=findings,
        query=query or {},
    )


def query_evidence_explorer(
    projection: dict[str, Any],
    *,
    run_id: str,
    findings: list[dict[str, Any]],
    query: dict[str, str],
) -> dict[str, Any]:
    """Apply context-preserving filters and bounded pagination to a projection."""

    all_events = [item for item in projection.get("events", []) if isinstance(item, dict)]
    finding_id = query.get("finding", "").strip()
    finding = next(
        (item for item in findings if str(item.get("finding_id", "")) == finding_id),
        None,
    )
    cited_ids = _finding_evidence_ids(finding)
    cited_events = [
        item for item in all_events if item["source_identity"]["evidence_id"] in cited_ids
    ]
    relevant_cited_events = [
        item for item in cited_events if _finding_signal_matches(finding, str(item.get("signal")))
    ]
    if relevant_cited_events:
        cited_events = relevant_cited_events
    exact_cited_events = [item for item in cited_events if item.get("correlation") == "exact"]
    if exact_cited_events:
        cited_events = exact_cited_events
    highlighted_ids = {str(item["event_id"]) for item in cited_events}
    default_start = min((item["timestamp"] for item in all_events), default="")
    default_end = max((item["timestamp"] for item in all_events), default="")
    if cited_events:
        default_start = min(item["timestamp"] for item in cited_events)
        default_end = max(item["timestamp"] for item in cited_events)
    window_start = _iso(query.get("start")) or default_start
    window_end = _iso(query.get("end")) or default_end
    if window_start and window_end and window_start > window_end:
        window_start, window_end = window_end, window_start

    selected = {
        key: query.get(key, "").strip()
        for key in ("journey", "workload", "pod", "task_id", "signal", "severity")
    }
    filtered = []
    for item in all_events:
        item = dict(item)
        item["highlighted"] = str(item["event_id"]) in highlighted_ids
        if window_start and item["timestamp"] < window_start:
            continue
        if window_end and item["timestamp"] > window_end:
            continue
        if any(selected[key] and str(item.get(key) or "") != selected[key] for key in selected):
            continue
        filtered.append(item)

    page_size = _bounded_int(query.get("page_size"), DEFAULT_PAGE_SIZE, 1, MAX_PAGE_SIZE)
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = min(_bounded_int(query.get("page"), 1, 1, total_pages), total_pages)
    start_index = (page - 1) * page_size
    page_events = filtered[start_index : start_index + page_size]
    context_query = {
        **{key: value for key, value in selected.items() if value},
        **({"finding": finding_id} if finding_id else {}),
        **({"start": window_start} if window_start else {}),
        **({"end": window_end} if window_end else {}),
        "page_size": str(page_size),
    }
    result = dict(projection)
    result["events"] = page_events
    result["filters"] = {
        **selected,
        "finding": finding_id,
        "start": window_start,
        "end": window_end,
    }
    result["facets"] = {
        key: sorted({str(item[key]) for item in all_events if item.get(key)}) for key in selected
    }
    result["window"] = {
        "start": window_start,
        "end": window_end,
        "available_start": min((item["timestamp"] for item in all_events), default=""),
        "available_end": max((item["timestamp"] for item in all_events), default=""),
    }
    result["finding_context"] = (
        {
            "finding_id": finding_id,
            "signal": str(finding.get("signal_type", "unknown")),
            "severity": str(finding.get("severity", "info")),
            "cited_evidence_ids": sorted(cited_ids),
            "highlighted_event_count": sum(item["highlighted"] for item in filtered),
        }
        if finding is not None
        else None
    )
    result["pagination"] = {
        "page": page,
        "page_size": page_size,
        "total_items": len(filtered),
        "total_pages": total_pages,
        "previous_url": _page_url(run_id, context_query, page - 1) if page > 1 else None,
        "next_url": _page_url(run_id, context_query, page + 1) if page < total_pages else None,
    }
    return result


def _run_events(values: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    events = []
    for index, value in enumerate(values[:MAX_SOURCE_EVENTS], 1):
        timestamp = _iso(value.get("observed_at") or value.get("timestamp"))
        if not timestamp:
            continue
        event_type = _safe_token(value.get("event_type") or value.get("type") or "run_event")
        events.append(
            _event(
                event_id=f"run:{index}",
                timestamp=timestamp,
                source="ampule-chamber",
                source_id="run-events",
                signal="run_lifecycle",
                category="run",
                severity="info",
                correlation="exact",
                title=event_type.replace("_", " ").title(),
                summary=f"Run lifecycle state: {_safe_token(value.get('state') or 'observed')}.",
            )
        )
    return events


def _k6_events(
    run_dir: Path, source: dict[str, Any], *, config: dict[str, Any]
) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict):
        return []
    timestamp = _source_timestamp(source)
    requests = _metric_value(metrics.get("http_reqs"), "count")
    errors = _metric_value(metrics.get("http_req_failed"), "rate", "value")
    latency = _metric_value(metrics.get("http_req_duration"), "p(95)", "p95", "avg")
    journeys = _journey_names(config) or ["traffic"]
    events = []
    for index, journey in enumerate(journeys, 1):
        events.append(
            _event(
                event_id=f"k6:journey:{index}",
                timestamp=timestamp,
                source="k6",
                source_id="k6-summary",
                signal="request_outcome",
                category="traffic",
                severity="warning" if errors and errors > 0 else "info",
                correlation="run_window",
                title=f"{journey} request outcomes",
                summary=_metric_summary(requests=requests, error_rate=errors, latency_ms=latency),
                journey=journey,
                value=requests,
                unit="requests",
            )
        )
    if latency is not None:
        events.append(
            _event(
                event_id="k6:latency",
                timestamp=timestamp,
                source="k6",
                source_id="k6-summary",
                signal="latency",
                category="metric",
                severity="info",
                correlation="run_window",
                title="HTTP latency",
                summary=f"Observed bounded HTTP latency was {latency:g} ms.",
                value=latency,
                unit="ms",
            )
        )
    if errors is not None:
        events.append(
            _event(
                event_id="k6:error-rate",
                timestamp=timestamp,
                source="k6",
                source_id="k6-summary",
                signal="error_rate",
                category="metric",
                severity="warning" if errors > 0 else "info",
                correlation="run_window",
                title="HTTP error rate",
                summary=f"Observed HTTP error rate was {errors:.4g}.",
                value=errors,
                unit="ratio",
            )
        )
    return events


def _relayna_events(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(raw_tasks, list):
        return []
    artifact_time = _datetime(source.get("collected_at")) or datetime.now(UTC)
    events = []
    for task_index, task in enumerate(raw_tasks[:MAX_RELAYNA_TASKS], 1):
        if not isinstance(task, dict):
            continue
        task_id = _safe_token(task.get("task_id") or "unassigned")
        journey = _safe_token(task.get("journey") or "relayna")
        duration_ms = _number(task.get("total_duration_ms")) or 0
        started = artifact_time - timedelta(milliseconds=max(duration_ms, 0))
        events.append(
            _event(
                event_id=f"relayna:{task_index}:admission",
                timestamp=started.isoformat(),
                source="relayna",
                source_id="relayna-summary",
                signal="admission",
                category="relayna",
                severity="info",
                correlation="inferred",
                title="Task admitted",
                summary=f"Relayna admitted task {task_id} for journey {journey}.",
                journey=journey,
                task_id=task_id,
            )
        )
        raw_events = task.get("events")
        safe_events = raw_events if isinstance(raw_events, list) else []
        count = max(1, len(safe_events))
        indexed_events = list(enumerate(safe_events, 1))
        bounded_events = _evenly_bounded(indexed_events, MAX_RELAYNA_EVENTS_PER_TASK)
        for event_index, raw in bounded_events:
            if not isinstance(raw, dict):
                continue
            exact_timestamp = _iso(raw.get("timestamp"))
            inferred = started + timedelta(milliseconds=duration_ms * event_index / count)
            status = _safe_token(
                raw.get("status")
                or raw.get("stage")
                or raw.get("event")
                or raw.get("type")
                or raw.get("kind")
                or "task_event"
            )
            events.append(
                _event(
                    event_id=f"relayna:{task_index}:event:{event_index}",
                    timestamp=exact_timestamp or inferred.isoformat(),
                    source="relayna",
                    source_id="relayna-summary",
                    signal=_relayna_signal(status),
                    category="relayna",
                    severity="error" if status.lower() in {"failed", "error"} else "info",
                    correlation="exact" if exact_timestamp else "inferred",
                    title=status.replace("_", " ").title(),
                    summary=f"Task {task_id} reported operational state {status}.",
                    journey=journey,
                    task_id=task_id,
                )
            )
    return events


def _worker_events(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    workers = payload.get("workers") if isinstance(payload, dict) else None
    if not isinstance(workers, list):
        return []
    events = []
    for index, worker in enumerate(workers[:MAX_SOURCE_EVENTS], 1):
        if not isinstance(worker, dict):
            continue
        pod = _safe_token(worker.get("name") or worker.get("pod_name") or "worker")
        task_ids = _safe_string_list(worker.get("task_ids"))
        correlation = (
            "exact" if worker.get("correlation") == "task_label" and task_ids else "run_window"
        )
        events.append(
            _event(
                event_id=f"relayna-worker:{index}",
                timestamp=_iso(worker.get("start_time")) or _source_timestamp(source),
                source="kubernetes",
                source_id="relayna-workers",
                signal="worker",
                category="relayna",
                severity="info",
                correlation=correlation,
                title="Relayna worker observed",
                summary=f"Worker pod {pod} was observed for this run.",
                workload=_safe_token(worker.get("owner") or worker.get("job_name") or "worker"),
                pod=pod,
                task_id=task_ids[0] if len(task_ids) == 1 else None,
            )
        )
    return events


def _kubernetes_events(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(commands, list):
        return []
    fallback = _source_timestamp(source)
    command_events: list[dict[str, Any]] = []
    pod_events: list[dict[str, Any]] = []
    log_events: list[dict[str, Any]] = []
    log_count = 0
    for command_index, record in enumerate(commands, 1):
        if not isinstance(record, dict):
            continue
        command = _safe_string_list(record.get("command"))
        stdout = record.get("stdout")
        stdout = stdout if isinstance(stdout, str) else ""
        if _is_command(command, "get", "events"):
            parsed = _json_text(stdout)
            for item_index, item in enumerate(_items(parsed)[:MAX_SOURCE_EVENTS], 1):
                metadata = _mapping(item.get("metadata"))
                involved = _mapping(item.get("involvedObject"))
                reason = _safe_token(item.get("reason") or "KubernetesEvent")
                event_type = _safe_token(item.get("type") or "Normal")
                command_events.append(
                    _event(
                        event_id=f"kubernetes:event:{command_index}:{item_index}",
                        timestamp=_iso(
                            item.get("eventTime")
                            or item.get("lastTimestamp")
                            or item.get("firstTimestamp")
                            or metadata.get("creationTimestamp")
                        )
                        or fallback,
                        source="kubernetes",
                        source_id="kubernetes-commands",
                        signal="kubernetes_event",
                        category="kubernetes",
                        severity="warning" if event_type.lower() == "warning" else "info",
                        correlation="exact" if _event_has_timestamp(item, metadata) else "inferred",
                        title=reason,
                        summary=(
                            f"Kubernetes {event_type} event {reason} affected the "
                            "observed resource."
                        ),
                        workload=(
                            _safe_token(involved.get("name")) if involved.get("name") else None
                        ),
                        pod=(
                            _safe_token(involved.get("name"))
                            if involved.get("kind") == "Pod" and involved.get("name")
                            else None
                        ),
                    )
                )
        elif _is_command(command, "get", "pod") or _is_command(command, "get", "pods"):
            parsed = _json_text(stdout)
            values = _items(parsed) or ([parsed] if isinstance(parsed, dict) else [])
            for item_index, item in enumerate(values[:MAX_SOURCE_EVENTS], 1):
                metadata = _mapping(item.get("metadata"))
                status = _mapping(item.get("status"))
                pod = _safe_token(metadata.get("name") or "pod")
                phase = _safe_token(status.get("phase") or "Unknown")
                pod_events.append(
                    _event(
                        event_id=f"kubernetes:pod:{command_index}:{item_index}",
                        timestamp=_iso(status.get("startTime") or metadata.get("creationTimestamp"))
                        or fallback,
                        source="kubernetes",
                        source_id="kubernetes-commands",
                        signal="workload_transition",
                        category="kubernetes",
                        severity="warning" if phase in {"Failed", "Unknown"} else "info",
                        correlation=(
                            "exact"
                            if status.get("startTime") or metadata.get("creationTimestamp")
                            else "inferred"
                        ),
                        title=f"Pod {phase}",
                        summary=f"Pod {pod} was observed in phase {phase}.",
                        workload=_owner_name(metadata),
                        pod=pod,
                    )
                )
        elif "logs" in command and log_count < MAX_SAFE_LOG_EVENTS:
            pod = _command_resource(command, "pod/")
            workload = _command_resource(command, "deployment/")
            for line_index, line in enumerate(stdout.splitlines(), 1):
                match = _LOG_EVENT.match(line)
                if match is None:
                    continue
                log_count += 1
                if log_count > MAX_SAFE_LOG_EVENTS:
                    break
                severity = match.group("severity").lower().replace("warning", "warn")
                event_name = _safe_token(match.group("event"))
                log_events.append(
                    _event(
                        event_id=f"kubernetes:log:{command_index}:{line_index}",
                        timestamp=_iso(match.group("timestamp")) or fallback,
                        source="kubernetes-logs",
                        source_id="kubernetes-commands",
                        signal="log_event",
                        category="log",
                        severity={"warn": "warning", "critical": "error"}.get(severity, severity),
                        correlation="exact" if _iso(match.group("timestamp")) else "inferred",
                        title=event_name,
                        summary=f"Bounded operational log event {event_name} ({severity}).",
                        workload=workload,
                        pod=pod,
                    )
                )
    return _bounded_categories(
        (command_events, pod_events, log_events),
        MAX_SOURCE_EVENTS,
    )


def _rollback_events(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(actions, list):
        return []
    timestamp = _source_timestamp(source)
    events = []
    for index, action in enumerate(actions[:MAX_SOURCE_EVENTS], 1):
        if not isinstance(action, dict):
            continue
        fault_type = _safe_token(action.get("type") or "fault")
        pod = _safe_token(action.get("pod")) if action.get("pod") else None
        workload = _safe_token(action.get("deployment")) if action.get("deployment") else None
        events.append(
            _event(
                event_id=f"rollback:{index}:fault",
                timestamp=timestamp,
                source="chaos",
                source_id="rollback",
                signal="fault_injected",
                category="fault",
                severity="warning",
                correlation="inferred",
                title=f"{fault_type.replace('_', ' ').title()} injected",
                summary=f"Controlled fault {fault_type} targeted the selected workload.",
                workload=workload,
                pod=pod,
            )
        )
        restored = bool(action.get("restored", payload.get("verified", False)))
        events.append(
            _event(
                event_id=f"rollback:{index}:restore",
                timestamp=timestamp,
                source="chaos",
                source_id="rollback",
                signal="rollback",
                category="fault",
                severity="info" if restored else "error",
                correlation="inferred",
                title="Rollback verified" if restored else "Rollback not verified",
                summary=(
                    f"Rollback for {fault_type} was {'verified' if restored else 'not verified'}."
                ),
                workload=workload,
                pod=pod,
            )
        )
    return events


def _prometheus_events(run_dir: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _json(run_dir / str(source["relative_path"]))
    if not isinstance(payload, dict):
        return []
    workloads = {
        str(item.get("pod_name")): item
        for item in payload.get("workloads", [])
        if isinstance(item, dict) and item.get("pod_name")
    }
    signal_names = {
        "container_memory_working_set_bytes": ("memory", "bytes"),
        "container_cpu_usage_cores": ("cpu", "cores"),
        "kube_pod_container_status_restarts_total": ("restarts", "count"),
    }
    range_queries = payload.get("range_queries")
    events = []
    if isinstance(range_queries, dict):
        for query_name, (signal, unit) in signal_names.items():
            query = range_queries.get(query_name)
            series = query.get("series") if isinstance(query, dict) else None
            samples: dict[tuple[str, float], float] = defaultdict(float)
            for item in series if isinstance(series, list) else []:
                if not isinstance(item, dict):
                    continue
                metric = _mapping(item.get("metric"))
                pod = metric.get("pod")
                if not isinstance(pod, str) or not pod:
                    continue
                for sample in (
                    item.get("values", []) if isinstance(item.get("values"), list) else []
                ):
                    if not isinstance(sample, list | tuple) or len(sample) < 2:
                        continue
                    timestamp = _number(sample[0])
                    value = _number(sample[1])
                    if timestamp is not None and value is not None:
                        samples[(pod, timestamp)] += value
            bounded = _evenly_bounded(
                sorted(samples.items(), key=lambda item: item[0][1]), MAX_SOURCE_EVENTS
            )
            for index, ((pod, timestamp), value) in enumerate(bounded, 1):
                workload = workloads.get(pod, {})
                correlation = _workload_correlation(workload)
                task_ids = _safe_string_list(workload.get("task_ids"))
                events.append(
                    _event(
                        event_id=f"prometheus:{signal}:{index}",
                        timestamp=datetime.fromtimestamp(timestamp, UTC).isoformat(),
                        source="prometheus",
                        source_id="prometheus-memory",
                        signal=signal,
                        category="metric",
                        severity="warning" if signal == "restarts" and value > 0 else "info",
                        correlation=correlation,
                        title=f"{signal.title()} sample",
                        summary=f"Pod {pod} {signal} was {value:.4g} {unit}.",
                        workload=_safe_token(workload.get("role"))
                        if workload.get("role")
                        else None,
                        pod=pod,
                        task_id=task_ids[0] if len(task_ids) == 1 else None,
                        value=value,
                        unit=unit,
                    )
                )
    if events:
        return events
    summaries = payload.get("summaries")
    for index, item in enumerate(summaries if isinstance(summaries, list) else [], 1):
        if not isinstance(item, dict):
            continue
        pod = _safe_token(item.get("pod_name") or "pod")
        task_ids = _safe_string_list(item.get("task_ids"))
        for signal, field, unit in (
            ("memory", "peak_memory_bytes", "bytes"),
            ("cpu", "peak_cpu_cores", "cores"),
            ("restarts", "max_restarts", "count"),
        ):
            value = _number(item.get(field))
            if value is None:
                continue
            events.append(
                _event(
                    event_id=f"prometheus:summary:{index}:{signal}",
                    timestamp=_source_timestamp(source),
                    source="prometheus",
                    source_id="prometheus-memory",
                    signal=signal,
                    category="metric",
                    severity="warning" if signal == "restarts" and value > 0 else "info",
                    correlation=_workload_correlation(item, fallback="run_window"),
                    title=f"Peak {signal}",
                    summary=f"Pod {pod} peak {signal} was {value:.4g} {unit}.",
                    workload=_safe_token(item.get("role")) if item.get("role") else None,
                    pod=pod,
                    task_id=task_ids[0] if len(task_ids) == 1 else None,
                    value=value,
                    unit=unit,
                )
            )
    return events


def _event(
    *,
    event_id: str,
    timestamp: str,
    source: str,
    source_id: str,
    signal: str,
    category: str,
    severity: str,
    correlation: str,
    title: str,
    summary: str,
    journey: str | None = None,
    workload: str | None = None,
    pod: str | None = None,
    task_id: str | None = None,
    value: float | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "source_identity": {
            "evidence_id": source_id,
            "source": source,
            "signal_type": signal,
        },
        "category": category,
        "signal": signal,
        "severity": severity if severity in {"debug", "info", "warning", "error"} else "info",
        "correlation": correlation,
        "title": title[:160],
        "summary": summary[:320],
        "journey": journey,
        "workload": workload,
        "pod": pod,
        "task_id": task_id,
        "value": value,
        "unit": unit,
    }


def _finding_evidence_ids(finding: dict[str, Any] | None) -> set[str]:
    if finding is None:
        return set()
    ids = {str(value) for value in finding.get("evidence_ids", []) if value}
    public = set()
    for evidence_id in ids:
        if evidence_id.startswith("kubernetes-command-"):
            public.add("kubernetes-commands")
        elif ":k6-summary:" in evidence_id:
            public.add("k6-summary")
        else:
            public.add(evidence_id)
    return public


def _finding_signal_matches(finding: dict[str, Any] | None, event_signal: str) -> bool:
    if finding is None:
        return False
    signal = str(finding.get("signal_type", "")).lower()
    related = {
        "restart_loop": {"restarts", "kubernetes_event", "log_event"},
        "oom_killed": {"memory", "kubernetes_event", "log_event"},
        "request_latency": {"latency", "request_outcome"},
        "error_rate": {"error_rate", "request_outcome"},
        "retry_amplification": {"error_rate", "request_outcome", "log_event"},
        "failed_recovery": {"rollback", "workload_transition", "terminal"},
    }
    return event_signal == signal or event_signal in related.get(signal, set())


def _source_timestamp(source: dict[str, Any]) -> str:
    return _iso(source.get("collected_at")) or datetime.fromtimestamp(0, UTC).isoformat()


def _journey_names(config: dict[str, Any]) -> list[str]:
    traffic = config.get("traffic")
    journeys = traffic.get("journeys") if isinstance(traffic, dict) else None
    return [
        _safe_token(item.get("name") or item.get("path") or f"journey-{index}")
        for index, item in enumerate(journeys if isinstance(journeys, list) else [], 1)
        if isinstance(item, dict)
    ]


def _metric_value(value: Any, *keys: str) -> float | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("values")
    candidates = nested if isinstance(nested, dict) else value
    for key in keys:
        number = _number(candidates.get(key))
        if number is not None:
            return number
    return None


def _metric_summary(
    *, requests: float | None, error_rate: float | None, latency_ms: float | None
) -> str:
    values = []
    if requests is not None:
        values.append(f"{requests:g} requests")
    if error_rate is not None:
        values.append(f"error rate {error_rate:.4g}")
    if latency_ms is not None:
        values.append(f"latency {latency_ms:g} ms")
    return (
        "Observed " + (", ".join(values) if values else "a bounded traffic outcome summary") + "."
    )


def _relayna_signal(status: str) -> str:
    lowered = status.lower()
    if lowered in {"completed", "failed", "cancelled", "canceled"}:
        return "terminal"
    if "worker" in lowered or lowered in {"running", "processing"}:
        return "worker"
    return "task"


def _is_command(command: list[str], action: str, resource: str) -> bool:
    return action in command and resource in command


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = value.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _event_has_timestamp(item: dict[str, Any], metadata: dict[str, Any]) -> bool:
    return bool(
        item.get("eventTime")
        or item.get("lastTimestamp")
        or item.get("firstTimestamp")
        or metadata.get("creationTimestamp")
    )


def _owner_name(metadata: dict[str, Any]) -> str | None:
    owners = metadata.get("ownerReferences")
    if not isinstance(owners, list):
        return None
    for owner in owners:
        if isinstance(owner, dict) and owner.get("name"):
            return _safe_token(owner["name"])
    return None


def _command_resource(command: list[str], prefix: str) -> str | None:
    return next(
        (_safe_token(item.removeprefix(prefix)) for item in command if item.startswith(prefix)),
        None,
    )


def _workload_correlation(value: dict[str, Any], *, fallback: str = "exact") -> str:
    correlation = str(value.get("correlation", ""))
    if correlation in {"task_label", "selected_target", "prometheus_range"}:
        return "exact"
    if correlation in {"run_window_and_service_labels", "run_window"}:
        return "run_window"
    return fallback


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _json_text(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_string_list(value: Any) -> list[str]:
    return [_safe_token(item) for item in value] if isinstance(value, list | tuple) else []


def _safe_token(value: Any) -> str:
    text = str(value or "unknown")
    return re.sub(r"[^A-Za-z0-9_.:/@-]", "_", text)[:160]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromtimestamp(float(text), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: Any) -> str | None:
    parsed = _datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(result, maximum))


def _evenly_bounded(values: Iterable[Any], maximum: int) -> list[Any]:
    items = list(values)
    if len(items) <= maximum:
        return items
    if maximum <= 1:
        return items[:maximum]
    return [items[round(index * (len(items) - 1) / (maximum - 1))] for index in range(maximum)]


def _bounded_categories(
    categories: Iterable[Iterable[dict[str, Any]]], maximum: int
) -> list[dict[str, Any]]:
    groups = [list(category) for category in categories if category]
    if not groups or maximum <= 0:
        return []
    if sum(len(group) for group in groups) <= maximum:
        return [item for group in groups for item in group]

    allocations = [1 if index < maximum else 0 for index in range(len(groups))]
    remaining = maximum - sum(allocations)
    while remaining > 0:
        advanced = False
        for index, group in enumerate(groups):
            if allocations[index] >= len(group):
                continue
            allocations[index] += 1
            remaining -= 1
            advanced = True
            if remaining == 0:
                break
        if not advanced:
            break
    return [
        item
        for group, allocation in zip(groups, allocations, strict=True)
        for item in _evenly_bounded(group, allocation)
    ]


def _page_url(run_id: str, query: dict[str, str], page: int) -> str:
    return f"/runs/{run_id}?" + urlencode({"tab": "evidence", **query, "page": str(page)})
