"""Shared use-case facade for CLI and control-plane adapters."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import yaml

from chamber.application.evidence import build_evidence_explorer
from chamber.runs import (
    RunIndex,
    new_run_directory,
    read_json_value,
    registered_evidence,
    write_json_atomic,
)


@dataclass(frozen=True)
class RunComparison:
    """Stable comparison projection for two compatible runs."""

    baseline_run_id: str
    candidate_run_id: str
    compatible: bool
    score_delta: int | None
    evidence_coverage_delta: int | None
    added_finding_ids: tuple[str, ...]
    resolved_finding_ids: tuple[str, ...]
    notes: tuple[str, ...]
    baseline: dict[str, Any]
    candidate: dict[str, Any]
    compatibility_reasons: tuple[dict[str, Any], ...]
    signal_deltas: tuple[dict[str, Any], ...]
    finding_changes: tuple[dict[str, Any], ...]
    config_changes: tuple[dict[str, Any], ...]


class ChamberApplication:
    """Coordinate public Ampule Chamber use cases without interface-specific logic."""

    def __init__(self, workspace: Path = Path(".chamber")) -> None:
        self.workspace = workspace

    def initialize(self) -> Path:
        from chamber.workflow import init_workspace

        return init_workspace(self.workspace)

    def inspect_repository(self, repo: Path) -> dict[str, Any]:
        from chamber.workflow import infer_config

        return infer_config(repo)

    def onboard(self, repo: Path, *, output: Path) -> Path:
        from chamber.workflow import onboard_repository

        return onboard_repository(repo, output=output)

    def plan(self, config: Path, *, run_dir: Path | None = None) -> Path:
        from chamber.workflow import plan_config

        if run_dir is None:
            name = config.stem
            try:
                payload = yaml.safe_load(config.read_text(encoding="utf-8"))
                service = payload.get("service") if isinstance(payload, dict) else None
                if isinstance(service, dict) and service.get("name"):
                    name = str(service["name"])
            except (OSError, yaml.YAMLError):
                pass
            run_dir = new_run_directory(self.workspace / "runs", name)
        return plan_config(config, run_dir=run_dir)

    def assess(
        self,
        *,
        repo: Path | None = None,
        config: Path | None = None,
        resume: Path | None = None,
        mode: str = "local",
        agents_mode: str | None = None,
        agents_exclude: tuple[str, ...] = (),
        context: str | None = None,
        prometheus_url: str | None = None,
    ) -> Path:
        from chamber.workflow import assess

        return assess(
            repo=repo,
            config=config,
            resume=resume,
            mode=mode,
            agents_mode=agents_mode,
            agents_exclude=agents_exclude,
            context=context,
            prometheus_url=prometheus_url,
        )

    def report(self, run_dir: Path) -> Path:
        from chamber.workflow import render_report_from_run

        return render_report_from_run(run_dir)

    def list_runs(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        index = RunIndex(self.workspace)
        index.rebuild()
        rows: list[dict[str, Any]] = []
        for indexed in index.list_runs(limit=limit):
            rows.append(_run_list_item(indexed))
        return tuple(rows)

    def query_runs(
        self,
        *,
        search: str = "",
        state: str = "",
        outcome: str = "",
        environment: str = "",
        coverage: str = "",
        fault: str = "",
        date_from: str = "",
        date_to: str = "",
        view: str = "",
        page: int = 1,
        page_size: int = 25,
        include_archived: bool = False,
        max_page_size: int = 100,
    ) -> dict[str, Any]:
        """Return the URL-filterable operational run workspace projection."""

        all_rows = list(self.list_runs(limit=1000))
        _mark_regressions(all_rows)
        visible = [row for row in all_rows if include_archived or not row["archived"]]
        summary = _run_summary_cards(visible)
        filtered = [
            row
            for row in visible
            if _run_matches(
                row,
                search=search,
                state=state,
                outcome=outcome,
                environment=environment,
                coverage=coverage,
                fault=fault,
                date_from=date_from,
                date_to=date_to,
                view=view,
            )
        ]
        bounded_page_size = max(1, min(page_size, max(1, min(max_page_size, 1000))))
        total_pages = max(1, (len(filtered) + bounded_page_size - 1) // bounded_page_size)
        selected_page = max(1, min(page, total_pages))
        start = (selected_page - 1) * bounded_page_size
        items = filtered[start : start + bounded_page_size]
        groups: list[dict[str, Any]] = []
        for service_name in dict.fromkeys(str(row["service_name"]) for row in items):
            service_runs = [row for row in items if row["service_name"] == service_name]
            groups.append(
                {
                    "service_name": service_name,
                    "latest": service_runs[0],
                    "runs": service_runs,
                    "trend": _service_trend(service_runs),
                }
            )
        return {
            "schema_version": "chamber.ampule.dev/run-workspace/v1",
            "runs": items,
            "groups": groups,
            "summary": summary,
            "pagination": {
                "page": selected_page,
                "page_size": bounded_page_size,
                "total_items": len(filtered),
                "total_pages": total_pages,
            },
            "filters": {
                "search": search,
                "state": state,
                "outcome": outcome,
                "environment": environment,
                "coverage": coverage,
                "fault": fault,
                "date_from": date_from,
                "date_to": date_to,
                "view": view,
                "include_archived": include_archived,
            },
            "facets": {
                "states": sorted({str(row["state"]) for row in visible}),
                "outcomes": sorted({str(row["status"]) for row in visible}),
                "environments": sorted({str(row["environment"]) for row in visible}),
                "faults": sorted(
                    {str(value) for row in visible for value in row.get("fault_types", ())}
                ),
            },
            "partial_index": len(all_rows) >= 1000,
        }

    def set_run_archived(self, run_id: str, *, archived: bool) -> dict[str, Any]:
        """Toggle the recoverable archive state on a canonical run record."""

        run_dir = self.run_path(run_id)
        path = run_dir / "run.json"
        record = _mapping(_json_or_default(path, {}))
        record["archived"] = archived
        record["archived_at"] = _now() if archived else None
        write_json_atomic(path, record)
        return _run_list_item({**record, "run_dir": str(run_dir)})

    def set_run_tags(self, run_id: str, *, tags: tuple[str, ...]) -> dict[str, Any]:
        """Replace operator tags on one run with normalized searchable values."""

        run_dir = self.run_path(run_id)
        path = run_dir / "run.json"
        record = _mapping(_json_or_default(path, {}))
        record["tags"] = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))[:20]
        write_json_atomic(path, record)
        return _run_list_item({**record, "run_dir": str(run_dir)})

    def run_path(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id:
            raise ValueError("invalid run id")
        path = (self.workspace / "runs" / run_id).resolve()
        runs_root = (self.workspace / "runs").resolve()
        if not path.is_relative_to(runs_root) or not path.is_dir():
            raise FileNotFoundError(run_id)
        return path

    def get_run(
        self,
        run_id: str,
        *,
        evidence_query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        run_dir = self.run_path(run_id)
        evidence = _evidence_entries(run_dir)
        raw_findings = _json_or_default(run_dir / "findings.json", [])
        findings = _finding_views(
            run_id,
            raw_findings,
            evidence,
        )
        events = _events(run_dir / "events.jsonl")
        config = _yaml_or_default(run_dir / "chamber.yaml")
        explorer = build_evidence_explorer(
            run_dir,
            run_id=run_id,
            evidence=evidence,
            findings=findings,
            run_events=events,
            config=config,
            query=evidence_query,
        )
        for finding in findings:
            finding["investigation_url"] = _investigation_url(run_id, finding)
        return {
            "run_dir": str(run_dir),
            "run": _json_or_default(run_dir / "run.json", {}),
            "metadata": _json_or_default(run_dir / "run-metadata.json", {}),
            "result": _json_or_default(run_dir / "result.json", {}),
            "findings": findings,
            "evidence": evidence,
            "events": events,
            "config": config,
            "plan": _json_or_default(run_dir / "plan.json", {}),
            "agents": _agents(run_dir / "agent"),
            "report_markdown": _text_or_default(run_dir / "report.md"),
            "prometheus": _prometheus_view(run_dir / "evidence/prometheus-memory.json"),
            "relayna": _relayna_view(run_dir / "evidence/relayna-summary.json"),
            "evidence_explorer": explorer,
        }

    def plan_rerun(
        self,
        run_id: str,
        *,
        kubernetes_context: str = "",
        prometheus_url: str = "",
    ) -> Path:
        """Clone a terminal Kubernetes run with only recoverable setup overrides."""

        source = self.get_run(run_id)
        result = _mapping(source.get("result"))
        status = str(result.get("status", ""))
        if status not in {
            "ready",
            "conditional",
            "not_ready",
            "inconclusive",
            "failed",
            "cancelled",
            "preflight_failed",
        }:
            raise ValueError("fix and rerun requires a terminal Kubernetes assessment")
        config = deepcopy(_mapping(source.get("config")))
        runtime = config.get("runtime")
        if not isinstance(runtime, dict) or runtime.get("provider") != "kubernetes":
            raise ValueError("fix and rerun requires an explicit Kubernetes target")
        changes: dict[str, str] = {}
        if kubernetes_context.strip():
            runtime["kubernetesContext"] = kubernetes_context.strip()
            changes["runtime.kubernetesContext"] = kubernetes_context.strip()
        if prometheus_url.strip():
            runtime["prometheusUrl"] = prometheus_url.strip()
            changes["runtime.prometheusUrl"] = prometheus_url.strip()

        drafts = self.workspace / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        draft = drafts / f"rerun-{uuid4().hex}.yaml"
        draft.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        planned = self.plan(draft)
        record_path = planned / "run.json"
        record = _json_or_default(record_path, {})
        if isinstance(record, dict):
            record["parent_run_id"] = run_id
            record["rerun"] = {
                "kind": "fix_setup",
                "setup_changes": changes,
                "preserved": [
                    "service",
                    "scenario",
                    "traffic",
                    "safety",
                    "runtime.faults",
                ],
            }
            write_json_atomic(record_path, record)
        return planned

    def compare(self, baseline_run_id: str, candidate_run_id: str) -> RunComparison:
        baseline = self.get_run(baseline_run_id)
        candidate = self.get_run(candidate_run_id)
        compatibility_reasons = _compatibility_reasons(baseline, candidate)
        compatible = all(bool(item["compatible"]) for item in compatibility_reasons)
        notes = tuple(
            str(item["detail"]) for item in compatibility_reasons if not item["compatible"]
        )
        baseline_result = _mapping(baseline.get("result"))
        candidate_result = _mapping(candidate.get("result"))
        baseline_score = baseline_result.get("readiness_score")
        candidate_score = candidate_result.get("readiness_score")
        score_delta = (
            int(candidate_score) - int(baseline_score)
            if compatible and isinstance(baseline_score, int) and isinstance(candidate_score, int)
            else None
        )
        baseline_findings = _finding_ids(baseline.get("findings"))
        candidate_findings = _finding_ids(candidate.get("findings"))
        baseline_context = _comparison_context(baseline_run_id, baseline)
        candidate_context = _comparison_context(candidate_run_id, candidate)
        signal_deltas = _signal_deltas(baseline, candidate, compatible=compatible)
        finding_changes = _finding_changes(baseline, candidate) if compatible else ()
        added_finding_ids = (
            tuple(sorted(candidate_findings - baseline_findings)) if compatible else ()
        )
        resolved_finding_ids = (
            tuple(sorted(baseline_findings - candidate_findings)) if compatible else ()
        )
        return RunComparison(
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            compatible=compatible,
            score_delta=score_delta,
            evidence_coverage_delta=(
                int(candidate_result.get("evidence_coverage_percent", 0))
                - int(baseline_result.get("evidence_coverage_percent", 0))
                if compatible
                else None
            ),
            added_finding_ids=added_finding_ids,
            resolved_finding_ids=resolved_finding_ids,
            notes=notes,
            baseline=baseline_context,
            candidate=candidate_context,
            compatibility_reasons=compatibility_reasons,
            signal_deltas=signal_deltas,
            finding_changes=finding_changes,
            config_changes=_config_changes(baseline, candidate),
        )


def _json_or_default(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return read_json_value(path)
    except (OSError, json.JSONDecodeError):
        return default


def _yaml_or_default(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text_or_default(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _events(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return tuple(events)


def _evidence_entries(run_dir: Path) -> tuple[dict[str, Any], ...]:
    return registered_evidence(run_dir)


def _investigation_url(
    run_id: str,
    finding: dict[str, Any],
) -> str:
    query = {
        "tab": "evidence",
        "finding": str(finding.get("finding_id", "")),
    }
    return f"/runs/{run_id}?{urlencode(query)}"


def _finding_views(
    run_id: str,
    value: Any,
    evidence: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    available = {str(item.get("evidence_id")): item for item in evidence}
    findings = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        finding = dict(raw)
        ids = raw.get("evidence_ids")
        links = []
        for evidence_id in ids if isinstance(ids, list) else []:
            public_id = _public_evidence_id(str(evidence_id), available)
            if public_id is None:
                continue
            item = available[public_id]
            links.append(
                {
                    "evidence_id": str(evidence_id),
                    "registered_evidence_id": public_id,
                    "name": str(item.get("relative_path", public_id)),
                    "url": f"/api/v1/runs/{run_id}/evidence/{public_id}",
                }
            )
        finding["evidence_links"] = links
        findings.append(finding)
    return findings


def _public_evidence_id(
    evidence_id: str,
    available: dict[str, dict[str, Any]],
) -> str | None:
    if evidence_id in available:
        return evidence_id
    if evidence_id.startswith("kubernetes-command-") and "kubernetes-commands" in available:
        return "kubernetes-commands"
    if ":k6-summary:" in evidence_id and "k6-summary" in available:
        return "k6-summary"
    return None


def _agents(agent_dir: Path) -> dict[str, Any]:
    if not agent_dir.exists():
        return {}
    return {path.stem: _json_or_default(path, {}) for path in sorted(agent_dir.glob("*.json"))}


def _prometheus_view(path: Path) -> dict[str, Any]:
    artifact = _json_or_default(path, {})
    if not isinstance(artifact, dict) or not artifact:
        return {"available": False, "workloads": []}
    summaries = artifact.get("summaries")
    if isinstance(summaries, list) and summaries:
        workloads = [
            _prometheus_workload_view(item, memory_kind="Peak")
            for item in summaries
            if isinstance(item, dict)
        ]
    else:
        workloads = _legacy_prometheus_workloads(artifact)
    return {
        "available": True,
        "namespace": artifact.get("namespace"),
        "window": artifact.get("window") if isinstance(artifact.get("window"), dict) else {},
        "workloads": workloads,
        "has_range_metrics": isinstance(summaries, list) and bool(summaries),
        "queries": _prometheus_query_views(artifact),
        "artifact_path": "evidence/prometheus-memory.json",
    }


def _prometheus_query_views(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    raw_queries = artifact.get("queries")
    if not isinstance(raw_queries, dict):
        return []
    queries = []
    for name, raw in raw_queries.items():
        if not isinstance(raw, dict):
            continue
        samples = []
        series = raw.get("series")
        for item in series if isinstance(series, list) else []:
            if not isinstance(item, dict):
                continue
            metric = item.get("metric") if isinstance(item.get("metric"), dict) else {}
            sample = item.get("value")
            samples.append(
                {
                    "pod": metric.get("pod"),
                    "container": metric.get("container"),
                    "timestamp": sample[0]
                    if isinstance(sample, list | tuple) and len(sample) >= 2
                    else None,
                    "value": sample[1]
                    if isinstance(sample, list | tuple) and len(sample) >= 2
                    else None,
                }
            )
        queries.append(
            {
                "name": str(name),
                "ok": raw.get("ok") is True,
                "series_count": raw.get("series_count"),
                "error": raw.get("error"),
                "query": raw.get("query"),
                "samples": samples,
            }
        )
    return queries


def _prometheus_workload_view(item: dict[str, Any], *, memory_kind: str) -> dict[str, Any]:
    memory_bytes = _number(item.get("peak_memory_bytes"))
    cpu_cores = _number(item.get("peak_cpu_cores"))
    restarts = _number(item.get("max_restarts"))
    errors = item.get("errors", [])
    return {
        "pod_name": str(item.get("pod_name", "unknown")),
        "role": _workload_role(str(item.get("role", "observed"))),
        "phase": item.get("phase"),
        "memory_kind": memory_kind,
        "memory_mib": round(memory_bytes / (1024 * 1024), 1) if memory_bytes is not None else None,
        "cpu_millicores": round(cpu_cores * 1000, 1) if cpu_cores is not None else None,
        "restarts": int(restarts) if restarts is not None else None,
        "sample_count": int(_number(item.get("sample_count")) or 0),
        "task_ids": _string_values(item.get("task_ids")),
        "correlation": item.get("correlation"),
        "correlation_note": item.get("correlation_note"),
        "errors": [str(value) for value in errors] if isinstance(errors, list) else [],
    }


def _legacy_prometheus_workloads(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    queries = artifact.get("queries", {})
    queries = queries if isinstance(queries, dict) else {}
    memory = _instant_query_totals(queries.get("container_memory_working_set_bytes"))
    restarts = _instant_query_totals(queries.get("kube_pod_container_status_restarts_total"))
    metadata: dict[str, dict[str, Any]] = {}
    raw_workloads = artifact.get("workloads", [])
    for item in raw_workloads if isinstance(raw_workloads, list) else []:
        if isinstance(item, dict) and item.get("pod_name"):
            metadata[str(item["pod_name"])] = item
    pod_names = set(memory) | set(restarts) | set(metadata)
    pod_names.update(_string_values(artifact.get("pod_names")))
    workloads = []
    for pod_name in sorted(pod_names):
        item = metadata.get(pod_name, {})
        workloads.append(
            {
                "pod_name": pod_name,
                "role": _workload_role(str(item.get("role", "api"))),
                "phase": item.get("phase"),
                "memory_kind": "Current",
                "memory_mib": (
                    round(memory[pod_name] / (1024 * 1024), 1) if pod_name in memory else None
                ),
                "cpu_millicores": None,
                "restarts": int(restarts[pod_name]) if pod_name in restarts else None,
                "sample_count": 1 if pod_name in memory or pod_name in restarts else 0,
                "task_ids": _string_values(item.get("task_ids")),
                "correlation": item.get("correlation"),
                "correlation_note": item.get("correlation_note"),
                "errors": [],
            }
        )
    return workloads


def _instant_query_totals(value: Any) -> dict[str, float]:
    query = value if isinstance(value, dict) else {}
    series = query.get("series", [])
    totals: dict[str, float] = {}
    for item in series if isinstance(series, list) else []:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric", {})
        sample = item.get("value", [])
        pod_name = metric.get("pod") if isinstance(metric, dict) else None
        if not isinstance(pod_name, str) or not isinstance(sample, list | tuple) or len(sample) < 2:
            continue
        number = _number(sample[1])
        if number is not None:
            totals[pod_name] = totals.get(pod_name, 0.0) + number
    return totals


def _relayna_view(path: Path) -> dict[str, Any]:
    artifact = _json_or_default(path, {})
    if not isinstance(artifact, dict) or not artifact:
        return {"available": False, "tasks": []}
    tasks = []
    raw_tasks = artifact.get("tasks", [])
    for item in raw_tasks if isinstance(raw_tasks, list) else []:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "unassigned")
        raw_events = item.get("events", [])
        events = [
            _relayna_event_view(event, task_id=task_id, sequence=index)
            for index, event in enumerate(raw_events if isinstance(raw_events, list) else [], 1)
            if isinstance(event, dict)
        ]
        if not events:
            statuses = item.get("statuses", [])
            events = [
                {"sequence": index, "task_id": task_id, "status": str(status)}
                for index, status in enumerate(
                    statuses if isinstance(statuses, list | tuple) else [], 1
                )
            ]
        tasks.append(
            {
                "task_id": task_id,
                "iteration": item.get("iteration"),
                "journey": item.get("journey"),
                "terminal_status": item.get("terminal_status"),
                "success": bool(item.get("success")),
                "total_duration_ms": item.get("total_duration_ms"),
                "event_count": int(_number(item.get("event_count")) or len(events)),
                "events": events,
                "error": item.get("error"),
            }
        )
    return {
        "available": True,
        "success": bool(artifact.get("success")),
        "task_count": int(_number(artifact.get("task_count")) or len(tasks)),
        "tasks": tasks,
    }


def _relayna_event_view(event: dict[str, Any], *, task_id: str, sequence: int) -> dict[str, Any]:
    safe = {"sequence": int(_number(event.get("sequence")) or sequence), "task_id": task_id}
    for key in (
        "status",
        "stage",
        "event",
        "type",
        "timestamp",
        "progress",
        "worker_id",
        "reported_task_id",
        "task_id_match",
        "kind",
    ):
        value = event.get(key)
        if isinstance(value, str | int | float | bool):
            safe[key] = value
    return safe


def _workload_role(value: str) -> str:
    return "Relayna worker" if value == "relayna_worker" else "API / target"


def _string_values(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list | tuple) else []


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _service_name(run: dict[str, Any]) -> str:
    config = _mapping(run.get("config"))
    service = _mapping(config.get("service"))
    return str(service.get("name", "unknown"))


def _finding_ids(value: Any) -> set[str]:
    if not isinstance(value, list | tuple):
        return set()
    return {
        str(item["finding_id"])
        for item in value
        if isinstance(item, dict) and item.get("finding_id")
    }


def _run_list_item(indexed: dict[str, Any]) -> dict[str, Any]:
    row = dict(indexed)
    run_dir = Path(str(row["run_dir"]))
    record = _mapping(_json_or_default(run_dir / "run.json", {}))
    metadata = _mapping(_json_or_default(run_dir / "run-metadata.json", {}))
    result = _mapping(_json_or_default(run_dir / "result.json", {}))
    config = _yaml_or_default(run_dir / "chamber.yaml")
    scenario = _mapping(config.get("scenario"))
    runtime = _mapping(config.get("runtime"))
    service = _mapping(config.get("service"))
    findings = _json_or_default(run_dir / "findings.json", [])
    finding_count = len(findings) if isinstance(findings, list) else 0
    faults = runtime.get("faults")
    fault_types = tuple(
        str(item.get("type"))
        for item in (faults if isinstance(faults, list) else [])
        if isinstance(item, dict) and item.get("type")
    )
    raw_tags = record.get("tags")
    tag_items: list[Any]
    if isinstance(raw_tags, list):
        tag_items = raw_tags
    else:
        scenario_tags = scenario.get("tags")
        tag_items = scenario_tags if isinstance(scenario_tags, list) else []
    tag_values = tuple(str(item) for item in tag_items if isinstance(item, str))
    status = str(result.get("status") or row.get("result_status") or "pending")
    row.update(
        {
            "service_name": str(
                row.get("service_name") or service.get("name") or metadata.get("service_name")
            ),
            "status": status,
            "readiness_score": result.get("readiness_score"),
            "evidence_coverage_percent": int(result.get("evidence_coverage_percent", 0) or 0),
            "cleanup_verified": result.get("cleanup_verified"),
            "rollback_verified": result.get("rollback_verified"),
            "finding_count": finding_count,
            "scenario_id": str(
                scenario.get("id")
                or config.get("scenarioId")
                or record.get("scenario_id")
                or "unrecorded"
            ),
            "scenario_revision": str(
                scenario.get("revision") or record.get("scenario_revision") or "unrecorded"
            ),
            "environment": str(runtime.get("provider") or row.get("mode") or "unknown"),
            "runtime_mode": str(runtime.get("mode") or row.get("runtime_mode") or "unknown"),
            "namespace": str(runtime.get("namespace") or metadata.get("namespace") or ""),
            "context": str(runtime.get("kubernetesContext") or metadata.get("context") or ""),
            "commit": str(service.get("commit") or metadata.get("commit") or "unrecorded"),
            "owner": str(config.get("owner") or metadata.get("owner") or "unassigned"),
            "tags": tag_values,
            "fault_types": fault_types,
            "archived": bool(record.get("archived", False)),
            "archived_at": record.get("archived_at"),
            "retention": "Workspace retained; archive is reversible.",
            "regressed": False,
        }
    )
    return row


def _mark_regressions(rows: list[dict[str, Any]]) -> None:
    previous_by_service: dict[str, dict[str, Any]] = {}
    for row in reversed(rows):
        service = str(row["service_name"])
        previous = previous_by_service.get(service)
        score = row.get("readiness_score")
        previous_score = previous.get("readiness_score") if previous else None
        row["regressed"] = bool(
            previous
            and (
                (
                    isinstance(score, int)
                    and isinstance(previous_score, int)
                    and score < previous_score
                )
                or int(row.get("finding_count", 0)) > int(previous.get("finding_count", 0))
            )
        )
        previous_by_service[service] = row


def _run_summary_cards(rows: list[dict[str, Any]]) -> dict[str, int]:
    terminal = {"completed", "failed", "cancelled"}
    return {
        "active": sum(str(row.get("state")) not in terminal for row in rows),
        "failed": sum(
            str(row.get("status")) in {"failed", "not_ready", "preflight_failed"} for row in rows
        ),
        "regressed": sum(bool(row.get("regressed")) for row in rows),
        "inconclusive": sum(str(row.get("status")) == "inconclusive" for row in rows),
        "cleanup_attention": sum(
            str(row.get("state")) in terminal and row.get("cleanup_verified") is False
            for row in rows
        ),
    }


def _run_matches(
    row: dict[str, Any],
    *,
    search: str,
    state: str,
    outcome: str,
    environment: str,
    coverage: str,
    fault: str,
    date_from: str,
    date_to: str,
    view: str,
) -> bool:
    searchable = " ".join(
        str(value)
        for value in (
            row.get("run_id"),
            row.get("service_name"),
            row.get("scenario_id"),
            row.get("scenario_revision"),
            row.get("commit"),
            row.get("owner"),
            *row.get("tags", ()),
        )
    ).lower()
    if search.strip().lower() not in searchable:
        return False
    if state and row.get("state") != state:
        return False
    if outcome and row.get("status") != outcome:
        return False
    if environment and row.get("environment") != environment:
        return False
    coverage_value = int(row.get("evidence_coverage_percent", 0))
    if coverage == "complete" and coverage_value < 100:
        return False
    if coverage == "partial" and not 0 < coverage_value < 100:
        return False
    if coverage == "missing" and coverage_value != 0:
        return False
    if fault and fault not in row.get("fault_types", ()):
        return False
    created_date = str(row.get("created_at", ""))[:10]
    if date_from and created_date < date_from:
        return False
    if date_to and created_date > date_to:
        return False
    if view == "needs_attention" and not (
        row.get("status") in {"failed", "not_ready", "preflight_failed", "inconclusive"}
        or row.get("cleanup_verified") is False
    ):
        return False
    if view == "recent_regressions" and not row.get("regressed"):
        return False
    if view == "my_services" and row.get("owner") == "unassigned":
        return False
    return True


def _service_trend(rows: list[dict[str, Any]]) -> str:
    if any(row.get("regressed") for row in rows):
        return "regressed"
    scores = [row["readiness_score"] for row in rows if isinstance(row.get("readiness_score"), int)]
    if len(scores) >= 2 and scores[0] > scores[-1]:
        return "improved"
    return "stable" if scores else "unscored"


def _comparison_context(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    config = _mapping(run.get("config"))
    scenario = _mapping(config.get("scenario"))
    runtime = _mapping(config.get("runtime"))
    result = _mapping(run.get("result"))
    metadata = _mapping(run.get("metadata"))
    service = _mapping(config.get("service"))
    return {
        "run_id": run_id,
        "service_name": str(service.get("name", "unknown")),
        "created_at": str(_mapping(run.get("run")).get("created_at", "unrecorded")),
        "commit": str(service.get("commit") or metadata.get("commit") or "unrecorded"),
        "scenario_id": str(scenario.get("id") or config.get("scenarioId") or "unrecorded"),
        "scenario_revision": str(scenario.get("revision") or "unrecorded"),
        "environment": str(runtime.get("provider") or metadata.get("mode") or "unknown"),
        "runtime_mode": str(runtime.get("mode") or metadata.get("runtime_mode") or "unknown"),
        "namespace": str(runtime.get("namespace") or metadata.get("namespace") or "unrecorded"),
        "outcome": str(result.get("status", "pending")),
        "evidence_coverage_percent": int(result.get("evidence_coverage_percent", 0) or 0),
        "readiness_score": result.get("readiness_score"),
        "url": f"/runs/{run_id}",
    }


def _compatibility_reasons(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    left = _comparison_context("baseline", baseline)
    right = _comparison_context("candidate", candidate)
    checks = (
        ("service", left["service_name"], right["service_name"]),
        ("scenario contract", left["scenario_id"], right["scenario_id"]),
        ("scenario revision", left["scenario_revision"], right["scenario_revision"]),
        ("environment provider", left["environment"], right["environment"]),
        ("runtime mode", left["runtime_mode"], right["runtime_mode"]),
    )
    return tuple(
        {
            "dimension": name,
            "compatible": _known_comparison_value(baseline_value)
            and _known_comparison_value(candidate_value)
            and baseline_value == candidate_value,
            "baseline": baseline_value,
            "candidate": candidate_value,
            "detail": (
                f"{name.title()} is unavailable: {baseline_value} vs {candidate_value}."
                if not _known_comparison_value(baseline_value)
                or not _known_comparison_value(candidate_value)
                else f"{name.title()} matches ({baseline_value})."
                if baseline_value == candidate_value
                else f"{name.title()} differs: {baseline_value} vs {candidate_value}."
            ),
        }
        for name, baseline_value, candidate_value in checks
    )


def _known_comparison_value(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "unknown", "unrecorded", "none", "null"}


def _signal_snapshot(run: dict[str, Any]) -> dict[str, float | int | None]:
    run_dir = Path(str(run.get("run_dir", "")))
    k6 = _mapping(_json_or_default(run_dir / "evidence/k6-summary.json", {}))
    metrics = _mapping(k6.get("metrics"))
    latency = _metric_value(metrics, "http_req_duration", "p(95)")
    failure_rate = _metric_value(metrics, "http_req_failed", "value")
    recovery_time = _metric_value(metrics, "recovery_time", "value")
    prometheus = _mapping(run.get("prometheus"))
    workloads = prometheus.get("workloads")
    workload_items = (
        [item for item in workloads if isinstance(item, dict)]
        if isinstance(workloads, list)
        else []
    )
    relayna = _mapping(run.get("relayna"))
    tasks = relayna.get("tasks")
    task_items = (
        [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []
    )
    return {
        "latency_p95_ms": latency,
        "error_rate_percent": failure_rate * 100 if failure_rate is not None else None,
        "peak_memory_mib": _maximum(workload_items, "memory_mib"),
        "peak_cpu_millicores": _maximum(workload_items, "cpu_millicores"),
        "max_restarts": _maximum(workload_items, "restarts"),
        "recovery_time_seconds": recovery_time,
        "successful_tasks": sum(bool(item.get("success")) for item in task_items)
        if task_items
        else None,
        "failed_tasks": sum(not bool(item.get("success")) for item in task_items)
        if task_items
        else None,
    }


def _metric_value(metrics: dict[str, Any], metric: str, key: str) -> float | None:
    item = _mapping(metrics.get(metric))
    values = _mapping(item.get("values"))
    return _number(item.get(key) if key in item else values.get(key) if key in values else None)


def _maximum(items: list[dict[str, Any]], key: str) -> float | int | None:
    values = [_number(item.get(key)) for item in items]
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _signal_deltas(
    baseline: dict[str, Any], candidate: dict[str, Any], *, compatible: bool
) -> tuple[dict[str, Any], ...]:
    left = _signal_snapshot(baseline)
    right = _signal_snapshot(candidate)
    specs = (
        ("latency_p95_ms", "P95 latency", "ms", "lower"),
        ("error_rate_percent", "Error rate", "%", "lower"),
        ("peak_memory_mib", "Peak memory", "MiB", "lower"),
        ("peak_cpu_millicores", "Peak CPU", "m", "lower"),
        ("max_restarts", "Restarts", "", "lower"),
        ("recovery_time_seconds", "Recovery time", "s", "lower"),
        ("successful_tasks", "Successful tasks", "", "higher"),
        ("failed_tasks", "Failed tasks", "", "lower"),
    )
    deltas = []
    for key, label, unit, preferred in specs:
        baseline_value = left[key]
        candidate_value = right[key]
        comparable = compatible and baseline_value is not None and candidate_value is not None
        delta = candidate_value - baseline_value if comparable else None
        direction = "unavailable"
        if comparable and delta is not None:
            direction = (
                "unchanged"
                if delta == 0
                else "improved"
                if (delta < 0) == (preferred == "lower")
                else "worsened"
            )
        deltas.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
                "comparable": comparable,
                "direction": direction,
                "reason": None if comparable else "Both compatible runs need this evidence.",
            }
        )
    return tuple(deltas)


def _finding_changes(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    left = {
        str(item.get("finding_id")): item
        for item in baseline.get("findings", [])
        if isinstance(item, dict) and item.get("finding_id")
    }
    right = {
        str(item.get("finding_id")): item
        for item in candidate.get("findings", [])
        if isinstance(item, dict) and item.get("finding_id")
    }
    ranks = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    baseline_run_id = str(_mapping(baseline.get("run")).get("run_id", ""))
    candidate_run_id = str(_mapping(candidate.get("run")).get("run_id", ""))
    changes = []
    for finding_id in sorted(left.keys() | right.keys()):
        before = left.get(finding_id)
        after = right.get(finding_id)
        if before is None:
            change = "added"
        elif after is None:
            change = "resolved"
        else:
            delta = ranks.get(str(after.get("severity", "info")), 0) - ranks.get(
                str(before.get("severity", "info")), 0
            )
            if delta == 0:
                continue
            change = "worsened" if delta > 0 else "improved"
        item = after or before or {}
        changes.append(
            {
                "finding_id": finding_id,
                "title": str(item.get("title") or item.get("signal_type") or finding_id),
                "change": change,
                "baseline_url": f"/runs/{baseline_run_id}?tab=findings",
                "candidate_url": f"/runs/{candidate_run_id}?tab=findings",
            }
        )
    return tuple(changes)


def _config_changes(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    left = _config_summary(_mapping(baseline.get("config")))
    right = _config_summary(_mapping(candidate.get("config")))
    return tuple(
        {"path": key, "baseline": left.get(key), "candidate": right.get(key)}
        for key in sorted(left.keys() | right.keys())
        if left.get(key) != right.get(key)
    )


def _config_summary(config: dict[str, Any]) -> dict[str, Any]:
    service = _mapping(config.get("service"))
    scenario = _mapping(config.get("scenario"))
    runtime = _mapping(config.get("runtime"))
    traffic = _mapping(config.get("traffic"))
    journeys = traffic.get("journeys")
    journey_items = (
        [item for item in journeys if isinstance(item, dict)] if isinstance(journeys, list) else []
    )
    faults = runtime.get("faults")
    fault_items = (
        [item for item in faults if isinstance(item, dict)] if isinstance(faults, list) else []
    )
    return {
        "service.name": service.get("name"),
        "scenario.id": scenario.get("id") or config.get("scenarioId"),
        "scenario.revision": scenario.get("revision"),
        "runtime.provider": runtime.get("provider"),
        "runtime.mode": runtime.get("mode"),
        "runtime.namespace": runtime.get("namespace"),
        "traffic.journeyNames": tuple(str(item.get("name", "unnamed")) for item in journey_items),
        "runtime.faultTypes": tuple(str(item.get("type", "unknown")) for item in fault_items),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
