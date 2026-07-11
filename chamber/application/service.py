"""Shared use-case facade for CLI and control-plane adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chamber.runs import RunIndex, new_run_directory, read_json_value


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

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_path(run_id)
        return {
            "run_dir": str(run_dir),
            "run": _json_or_default(run_dir / "run.json", {}),
            "metadata": _json_or_default(run_dir / "run-metadata.json", {}),
            "result": _json_or_default(run_dir / "result.json", {}),
            "findings": _json_or_default(run_dir / "findings.json", []),
            "evidence": _evidence_entries(run_dir),
            "events": _events(run_dir / "events.jsonl"),
            "config": _yaml_or_default(run_dir / "chamber.yaml"),
            "plan": _json_or_default(run_dir / "plan.json", {}),
            "agents": _agents(run_dir / "agent"),
            "report_markdown": _text_or_default(run_dir / "report.md"),
        }

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
    value = _json_or_default(run_dir / "evidence/manifest.json", {})
    entries = value.get("entries") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        return ()
    return tuple(item for item in entries if isinstance(item, dict))


def _agents(agent_dir: Path) -> dict[str, Any]:
    if not agent_dir.exists():
        return {}
    return {path.stem: _json_or_default(path, {}) for path in sorted(agent_dir.glob("*.json"))}


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
