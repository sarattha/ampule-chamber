"""Shared use-case facade for CLI and control-plane adapters."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
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
    evidence_coverage_delta: int
    added_finding_ids: tuple[str, ...]
    resolved_finding_ids: tuple[str, ...]
    notes: tuple[str, ...]


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
        rows = []
        for indexed in index.list_runs(limit=limit):
            row = dict(indexed)
            run_dir = Path(str(row["run_dir"]))
            result = _json_or_default(run_dir / "result.json", {})
            if isinstance(result, dict):
                row["status"] = result.get("status")
                row["readiness_score"] = result.get("readiness_score")
                row["evidence_coverage_percent"] = result.get("evidence_coverage_percent", 0)
            rows.append(row)
        return tuple(rows)

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
        baseline_service = _service_name(baseline)
        candidate_service = _service_name(candidate)
        compatible = baseline_service == candidate_service
        notes = () if compatible else ("Run services differ; score delta is not comparable.",)
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
        return RunComparison(
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            compatible=compatible,
            score_delta=score_delta,
            evidence_coverage_delta=int(candidate_result.get("evidence_coverage_percent", 0))
            - int(baseline_result.get("evidence_coverage_percent", 0)),
            added_finding_ids=tuple(sorted(candidate_findings - baseline_findings)),
            resolved_finding_ids=tuple(sorted(baseline_findings - candidate_findings)),
            notes=notes,
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
    }


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


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
