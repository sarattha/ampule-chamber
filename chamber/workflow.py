"""Guided Ampule Chamber workflow CLI and run-directory persistence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from chamber.agents import (
    AgentValidationError,
    ChamberAgentContext,
    ReportNarrative,
    deterministic_evidence_analyst_brief,
    deterministic_onboarding_draft,
    deterministic_report_narrative,
    deterministic_run_supervisor_brief,
    deterministic_scenario_planner_brief,
    deterministic_traffic_chaos_recommendation,
    validate_evidence_bound_output,
)
from chamber.agents.sdk import OpenAIAgentsSdkRunner
from chamber.onboarding import (
    ExternalDependencyPolicy,
    FollowUpCheck,
    ImageBuildSpec,
    ImageReplacement,
    OnboardingSpec,
    TrafficJourney,
    WorkloadSpec,
    build_onboarding_plan,
)
from chamber.report import (
    EvidenceReference,
    ReportInput,
    ReportSection,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)

WORKSPACE_DIR = ".chamber"
RUNS_DIR = "runs"
DEFAULT_AGENT_MODE = "offline"
AGENT_MODES = {"off", "offline", "live"}
SECRET_NAME_FRAGMENTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "KEY")
PRODUCTION_CONTEXT_FRAGMENTS = ("prod", "production", "aks-prod", "prd", "live")


class WorkflowError(RuntimeError):
    """Raised when the guided workflow cannot continue."""


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    """CLI entrypoint for the canonical `ampule-chamber` command."""

    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args[:1] == ["run"]:
        from chamber.orchestrator.live import run_cli

        return run_cli(raw_args)

    parser = _parser()
    args = parser.parse_args(raw_args)
    try:
        if args.command == "init":
            workspace = init_workspace(Path(args.workspace))
            print(f"initialized {workspace}")
            return 0
        if args.command == "onboard":
            config = onboard_repository(Path(args.repo), output=Path(args.output))
            print(f"wrote {config}")
            return 0
        if args.command == "plan":
            run_dir = plan_config(
                Path(args.config),
                run_dir=Path(args.run_dir) if args.run_dir else None,
            )
            print(f"planned {run_dir}")
            return 0
        if args.command == "assess":
            run_dir = assess(
                repo=Path(args.repo) if args.repo else None,
                config=Path(args.config) if args.config else None,
                resume=Path(args.resume) if args.resume else None,
                mode=args.mode,
                agents_mode=args.agents_mode,
            )
            print(f"report {run_dir / 'report.md'}")
            return 0
        if args.command == "report":
            report_path = render_report_from_run(Path(args.run))
            print(f"report {report_path}")
            return 0
    except (WorkflowError, AgentValidationError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    raise WorkflowError(f"unsupported command {args.command!r}")


def init_workspace(workspace: Path = Path(WORKSPACE_DIR)) -> Path:
    """Create the local chamber workspace skeleton."""

    (workspace / RUNS_DIR).mkdir(parents=True, exist_ok=True)
    defaults_path = workspace / "defaults.json"
    if not defaults_path.exists():
        defaults_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "chamber.ampule.dev/workspace/v1",
                    "runsDir": RUNS_DIR,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return workspace


def onboard_repository(repo: Path, *, output: Path = Path("chamber.yaml")) -> Path:
    """Inspect a repository read-only and write a reviewable chamber config."""

    if not repo.exists() or not repo.is_dir():
        raise WorkflowError(f"repository path does not exist or is not a directory: {repo}")
    config = infer_config(repo)
    save_config(config, output)
    return output


def infer_config(repo: Path) -> dict[str, Any]:
    """Infer a conservative first-pass config from manifests and Dockerfiles."""

    service_name = repo.name.replace("_", "-")
    manifests = _manifest_paths(repo)
    workloads = _workloads_from_manifests(repo, manifests)
    images = _image_config(repo, service_name, workloads)
    return {
        "apiVersion": "chamber.ampule.dev/v1alpha1",
        "kind": "ChamberConfig",
        "service": {"name": service_name, "repo": str(repo)},
        "deployment": {
            "manifests": manifests,
            "images": images,
            "workloads": workloads
            or [{"name": service_name, "role": "target", "kind": "Deployment"}],
        },
        "traffic": {
            "entrypoint": service_name,
            "journeys": [
                {
                    "name": "baseline",
                    "method": "GET",
                    "path": "/health",
                    "expectedStatus": 200,
                }
            ],
        },
        "dependencies": {"internal": [], "external": []},
        "runtime": {"requiredEnv": [], "secretEnv": [], "config": {}},
        "agents": {"mode": DEFAULT_AGENT_MODE},
        "assumptions": (
            "Generated by read-only repository inspection.",
            "Review manifests, image mappings, traffic, and dependencies before live execution.",
        ),
    }


def save_config(config: dict[str, Any], path: Path) -> None:
    """Write a stable YAML config."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def load_config(path: Path) -> dict[str, Any]:
    """Load and validate a user-facing chamber config."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowError(f"{path}: config must be a YAML mapping")
    validate_config(raw, source=str(path))
    return raw


def validate_config(config: dict[str, Any], *, source: str = "<memory>") -> None:
    """Validate the minimum stable ChamberConfig contract."""

    _require(config.get("kind") == "ChamberConfig", f"{source}.kind must be ChamberConfig")
    service = _mapping(config.get("service"), f"{source}.service")
    _non_empty(service.get("name"), f"{source}.service.name")
    repo = _non_empty(service.get("repo"), f"{source}.service.repo")
    if not Path(repo).exists():
        raise WorkflowError(f"{source}.service.repo does not exist: {repo}")
    deployment = _mapping(config.get("deployment"), f"{source}.deployment")
    manifests = _string_list(deployment.get("manifests"), f"{source}.deployment.manifests")
    if not manifests:
        raise WorkflowError(f"{source}.deployment.manifests must not be empty")
    workloads = _list(deployment.get("workloads", []), f"{source}.deployment.workloads")
    if not workloads:
        raise WorkflowError(f"{source}.deployment.workloads must not be empty")
    traffic = _mapping(config.get("traffic"), f"{source}.traffic")
    journeys = _list(traffic.get("journeys", []), f"{source}.traffic.journeys")
    if not journeys:
        raise WorkflowError(f"{source}.traffic.journeys must not be empty")
    mode = str(
        _mapping(config.get("agents", {}), f"{source}.agents").get(
            "mode",
            DEFAULT_AGENT_MODE,
        )
    )
    if mode not in AGENT_MODES:
        raise WorkflowError(
            f"{source}.agents.mode must be one of: {', '.join(sorted(AGENT_MODES))}"
        )


def config_to_onboarding_spec(config: dict[str, Any]) -> OnboardingSpec:
    """Convert a user config into the Phase 10 onboarding contract."""

    service = _mapping(config["service"], "service")
    deployment = _mapping(config["deployment"], "deployment")
    traffic = _mapping(config["traffic"], "traffic")
    runtime = _mapping(config.get("runtime", {}), "runtime")
    dependencies = _mapping(config.get("dependencies", {}), "dependencies")
    journey = _mapping(_list(traffic["journeys"], "traffic.journeys")[0], "traffic.journeys[0]")
    image_builds, replacements = _image_specs(
        _mapping(deployment.get("images", {}), "deployment.images")
    )
    return OnboardingSpec(
        service_name=str(service["name"]),
        repo_path=str(service["repo"]),
        manifest_paths=tuple(_string_list(deployment["manifests"], "deployment.manifests")),
        workload_roles=tuple(
            _workload_spec(item) for item in _list(deployment["workloads"], "deployment.workloads")
        ),
        image_builds=image_builds,
        image_replacements=replacements,
        required_env=tuple(_string_list(runtime.get("requiredEnv", []), "runtime.requiredEnv")),
        secret_env=tuple(_string_list(runtime.get("secretEnv", []), "runtime.secretEnv")),
        config_overrides=_mapping(runtime.get("config", {}), "runtime.config"),
        external_dependencies=tuple(
            _external_dependency(item)
            for item in _list(dependencies.get("external", []), "dependencies.external")
        ),
        traffic=TrafficJourney(
            tool=str(journey.get("tool", "k6")),
            method=str(journey.get("method", "GET")),
            entrypoint=str(journey.get("path", "/health")),
            expected_status=int(journey.get("expectedStatus", 200)),
            body=journey.get("body") if isinstance(journey.get("body"), dict) else None,
        ),
        follow_up_checks=tuple(
            _follow_up(item)
            for item in _list(
                journey.get("followUps", []),
                "traffic.journeys[0].followUps",
            )
        ),
        scenario_id=str(config.get("scenarioId", f"{service['name']}-assessment")),
        namespace_base=str(config.get("namespaceBase", f"chamber-{service['name']}")),
    )


def plan_config(config_path: Path, *, run_dir: Path | None = None) -> Path:
    """Validate a config and write a standard run directory plan."""

    config = load_config(config_path)
    target_run_dir = run_dir or _new_run_dir(str(config["service"]["name"]))
    target_run_dir.mkdir(parents=True, exist_ok=True)
    config_copy = target_run_dir / "chamber.yaml"
    save_config(config, config_copy)
    _ensure_run_subdirs(target_run_dir)
    plan = build_onboarding_plan(config_to_onboarding_spec(config), run_id=target_run_dir.name)
    _write_plan(target_run_dir, plan)
    _write_metadata(
        target_run_dir,
        {
            "run_id": target_run_dir.name,
            "stage": "planned",
            "config": str(config_copy),
            "cleanup_performed": False,
            "agent_mode": _agent_mode(config, None),
        },
    )
    _write_agents(target_run_dir, config, evidence_ids=("plan",), stage="plan")
    return target_run_dir


def assess(
    *,
    repo: Path | None = None,
    config: Path | None = None,
    resume: Path | None = None,
    mode: str = "local",
    agents_mode: str | None = None,
) -> Path:
    """Run the guided assessment workflow in local artifact-producing mode."""

    if mode != "local":
        raise WorkflowError("only --mode local is supported by the guided workflow")
    if resume is not None:
        return _resume_assessment(resume)
    if repo is None and config is None:
        raise WorkflowError("assess requires --repo, --config, or --resume")
    init_workspace()
    run_dir = _new_run_dir((repo or config or Path("assessment")).stem)
    run_dir.mkdir(parents=True, exist_ok=True)
    if repo is not None:
        generated = infer_config(repo)
        if agents_mode:
            generated.setdefault("agents", {})["mode"] = agents_mode
        config_path = run_dir / "chamber.yaml"
        save_config(generated, config_path)
    else:
        assert config is not None
        loaded = load_config(config)
        if agents_mode:
            loaded.setdefault("agents", {})["mode"] = agents_mode
        config_path = run_dir / "chamber.yaml"
        save_config(loaded, config_path)
    plan_config(config_path, run_dir=run_dir)
    config_data = load_config(config_path)
    _validate_assessment_safety()
    _write_local_evidence(run_dir)
    _write_json(run_dir / "findings.json", [])
    _write_agents(run_dir, config_data, evidence_ids=("plan", "local-assessment"), stage="assess")
    _write_metadata(
        run_dir,
        {
            "run_id": run_dir.name,
            "stage": "assessed",
            "mode": mode,
            "config": str(config_path),
            "cleanup_performed": True,
            "cleanup_notes": ["No live Kubernetes resources were created by local assessment."],
            "agent_mode": _agent_mode(config_data, agents_mode),
        },
    )
    render_report_from_run(run_dir)
    return run_dir


def render_report_from_run(run_dir: Path) -> Path:
    """Render `report.md` from a standard run directory."""

    config = load_config(run_dir / "chamber.yaml")
    plan = _read_json(run_dir / "plan.json")
    metadata = _read_json(run_dir / "run-metadata.json")
    report = _report_input(run_dir, config=config, plan=plan, metadata=metadata)
    report_path = run_dir / "report.md"
    report_path.write_text(render_markdown_report(report), encoding="utf-8")
    return report_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ampule Chamber guided workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser(
        "init",
        description="Create local chamber workspace defaults.",
    )
    init_parser.add_argument("--workspace", default=WORKSPACE_DIR)
    onboard_parser = subparsers.add_parser(
        "onboard",
        description="Generate chamber.yaml from a repository.",
    )
    onboard_parser.add_argument("--repo", required=True)
    onboard_parser.add_argument("--output", default="chamber.yaml")
    plan_parser = subparsers.add_parser(
        "plan",
        description="Validate chamber.yaml and write a run plan.",
    )
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--run-dir")
    assess_parser = subparsers.add_parser("assess", description="Run a local guided assessment.")
    source = assess_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo")
    source.add_argument("--config")
    source.add_argument("--resume")
    assess_parser.add_argument("--mode", default="local")
    assess_parser.add_argument("--agents-mode", choices=sorted(AGENT_MODES))
    report_parser = subparsers.add_parser(
        "report",
        description="Render a report from a run directory.",
    )
    report_parser.add_argument("--run", required=True)
    run_parser = subparsers.add_parser("run", description="Run live local kind scenarios.")
    run_parser.add_argument("run_args", nargs=argparse.REMAINDER)
    return parser


def _write_plan(run_dir: Path, plan: Any) -> None:
    _write_json(run_dir / "plan.json", _jsonable(plan))
    adapted_dir = run_dir / "adapted-manifests"
    shutil.rmtree(adapted_dir, ignore_errors=True)
    adapted_dir.mkdir(parents=True, exist_ok=True)
    for index, manifest in enumerate(plan.manifests, start=1):
        kind = str(manifest.get("kind", "resource")).lower()
        name = str(manifest.get("metadata", {}).get("name", index))
        (adapted_dir / f"{index:02d}-{kind}-{name}.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False),
            encoding="utf-8",
        )


def _write_agents(
    run_dir: Path,
    config: dict[str, Any],
    *,
    evidence_ids: tuple[str, ...],
    stage: str,
) -> tuple[ReportSection, ...]:
    mode = _agent_mode(config, None)
    agent_dir = run_dir / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    if mode == "off":
        return ()
    if mode == "live" and not os.environ.get("OPENAI_API_KEY"):
        raise AgentValidationError("OPENAI_API_KEY is required for live agent execution")
    service = _mapping(config["service"], "service")
    deployment = _mapping(config["deployment"], "deployment")
    context = ChamberAgentContext(
        scenario_id=str(config.get("scenarioId", f"{service['name']}-assessment")),
        scenario_path=str(run_dir / "chamber.yaml"),
        service_name=str(service["name"]),
        run_id=run_dir.name,
        evidence_ids=evidence_ids,
        finding_ids=(),
        artifact_paths=tuple(_string_list(deployment.get("manifests", []), "deployment.manifests")),
        missing_signals=("live Kubernetes execution",) if stage in {"plan", "assess"} else (),
    )
    outputs: dict[str, object] = {
        "onboarding-agent.json": deterministic_onboarding_draft(context),
        "scenario-planner-agent.json": deterministic_scenario_planner_brief(context),
        "run-supervisor-agent.json": deterministic_run_supervisor_brief(context),
        "traffic-chaos-agent.json": deterministic_traffic_chaos_recommendation(context),
        "evidence-analyst-agent.json": deterministic_evidence_analyst_brief(context),
        "report-writer-agent.json": _report_agent_output(context, mode),
    }
    available = set(evidence_ids)
    sections = []
    for filename, output in outputs.items():
        validate_evidence_bound_output(output, available_evidence_ids=available)
        _write_json(agent_dir / filename, _jsonable(output))
        sections.append(
            ReportSection(
                heading=filename.removesuffix(".json").replace("-", " ").title(),
                lines=_agent_lines(output),
            )
        )
    return tuple(sections)


def _report_agent_output(context: ChamberAgentContext, mode: str) -> ReportNarrative:
    if mode != "live":
        return deterministic_report_narrative(context)
    runner = OpenAIAgentsSdkRunner()
    return runner.run_structured(
        name="report-writer",
        instructions="Write an evidence-bound reliability report narrative.",
        input_text=json.dumps(_jsonable(context)),
        output_type=ReportNarrative,
    )


def _report_input(
    run_dir: Path,
    *,
    config: dict[str, Any],
    plan: dict[str, Any],
    metadata: dict[str, Any],
) -> ReportInput:
    service = _mapping(config["service"], "service")
    traffic = _mapping(config["traffic"], "traffic")
    journey = _mapping(_list(traffic["journeys"], "traffic.journeys")[0], "traffic.journeys[0]")
    cleanup_notes = tuple(metadata.get("cleanup_notes") or ["Cleanup status was not recorded."])
    agent_sections = _agent_sections(run_dir)
    evidence = [
        EvidenceReference(
            "plan",
            "ampule-chamber",
            "run_plan",
            str(run_dir),
            "from-file",
            str(run_dir / "plan.json"),
        )
    ]
    if (run_dir / "evidence/local-assessment.json").exists():
        evidence.append(
            EvidenceReference(
                "local-assessment",
                "ampule-chamber",
                "local_assessment",
                str(run_dir),
                "from-file",
                str(run_dir / "evidence/local-assessment.json"),
            )
        )
    return ReportInput(
        title="Ampule Chamber Reliability Report",
        service=ServiceMetadata(
            name=str(service["name"]),
            owner=str(config.get("owner", "unknown")),
            repository=str(service["repo"]),
            commit=_git_commit(Path(service["repo"])),
        ),
        run=RunMetadata(
            run_id=str(metadata.get("run_id", run_dir.name)),
            test_date=datetime.now(UTC).date().isoformat(),
            duration_seconds=int(metadata.get("duration_seconds", 1)),
            namespace=str(plan.get("namespace", "")),
            provider="kind",
            lifecycle_state=str(metadata.get("stage", "planned")),
        ),
        scenario=TestedScenario(
            scenario_id=str(config.get("scenarioId", f"{service['name']}-assessment")),
            name=str(journey.get("name", "guided assessment")),
            path=str(run_dir / "chamber.yaml"),
            traffic_tool=str(journey.get("tool", "k6")),
            max_virtual_users=1,
            fault_summary="none",
        ),
        findings=(),
        evidence=tuple(evidence),
        reproduction=ReproductionDetails(
            commands=(
                f"uv run ampule-chamber assess --resume {run_dir}",
                f"uv run ampule-chamber report --run {run_dir}",
            ),
            artifacts=tuple(str(path) for path in _expected_run_files(run_dir) if path.exists()),
        ),
        retest_plan=("Rerun the same chamber config after remediation or config review.",),
        cleanup_notes=cleanup_notes,
        limitations=tuple(
            plan.get("limitations")
            or ["Local guided workflow may not include live Kubernetes telemetry."]
        ),
        onboarding_summary=tuple(str(item) for item in config.get("assumptions", ())),
        adapted_workloads=tuple(_workload_line(item) for item in plan.get("workloads", [])),
        redacted_config=tuple(_config_line(item) for item in plan.get("redacted_config", [])),
        external_dependencies=tuple(
            _external_line(item) for item in plan.get("external_dependencies", [])
        ),
        agent_sections=agent_sections,
    )


def _agent_sections(run_dir: Path) -> tuple[ReportSection, ...]:
    sections = []
    for path in sorted((run_dir / "agent").glob("*.json")):
        payload = _read_json(path)
        sections.append(
            ReportSection(
                heading=path.stem.replace("-", " ").title(),
                lines=_agent_payload_lines(payload),
            )
        )
    return tuple(sections)


def _resume_assessment(run_dir: Path) -> Path:
    if not (run_dir / "chamber.yaml").exists() or not (run_dir / "plan.json").exists():
        raise WorkflowError(f"{run_dir} is not a resumable chamber run directory")
    render_report_from_run(run_dir)
    metadata = _read_json(run_dir / "run-metadata.json")
    metadata["stage"] = metadata.get("stage", "resumed")
    metadata["resumed_at"] = datetime.now(UTC).isoformat()
    _write_metadata(run_dir, metadata)
    return run_dir


def _write_local_evidence(run_dir: Path) -> None:
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        evidence_dir / "local-assessment.json",
        {
            "evidence_id": "local-assessment",
            "source": "ampule-chamber",
            "signal_type": "local_assessment",
            "resource": str(run_dir),
            "observed_facts": [
                "Config, plan, adapted manifests, and agent outputs were generated."
            ],
        },
    )


def _validate_assessment_safety() -> None:
    context = _current_kube_context()
    if context and any(fragment in context.lower() for fragment in PRODUCTION_CONTEXT_FRAGMENTS):
        raise WorkflowError(f"refusing unsafe Kubernetes context {context!r}")


def _current_kube_context() -> str | None:
    if shutil.which("kubectl") is None:
        return None
    completed = subprocess.run(
        ("kubectl", "config", "current-context"),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _new_run_dir(name: str) -> Path:
    init_workspace()
    safe = _dns_fragment(name or "assessment")
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return Path(WORKSPACE_DIR) / RUNS_DIR / f"chamber-{safe}-{timestamp}"


def _ensure_run_subdirs(run_dir: Path) -> None:
    for child in ("adapted-manifests", "evidence", "agent"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)


def _write_metadata(run_dir: Path, payload: dict[str, Any]) -> None:
    _write_json(run_dir / "run-metadata.json", payload)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{path} must contain a JSON object")
    return payload


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _manifest_paths(repo: Path) -> list[str]:
    candidates = []
    for base in ("k8s", "manifests", "deployment"):
        root = repo / base
        if root.exists():
            candidates.extend(root.rglob("*.yaml"))
            candidates.extend(root.rglob("*.yml"))
    if not candidates:
        candidates.extend(repo.glob("*.yaml"))
        candidates.extend(repo.glob("*.yml"))
    return [
        str(path.relative_to(repo))
        for path in sorted(set(candidates))
        if WORKSPACE_DIR not in path.parts
    ]


def _workloads_from_manifests(repo: Path, manifests: list[str]) -> list[dict[str, Any]]:
    workloads = []
    for manifest_path in manifests:
        for item in _yaml_documents(repo / manifest_path):
            kind = str(item.get("kind", ""))
            if kind not in {
                "Deployment",
                "StatefulSet",
                "DaemonSet",
                "Job",
                "CronJob",
                "ScaledJob",
            }:
                continue
            name = str(item.get("metadata", {}).get("name", ""))
            if not name:
                continue
            role = _inferred_workload_role(name, has_target=bool(workloads))
            workloads.append({"name": name, "role": role, "kind": kind, "readiness": ["ready"]})
    return workloads


def _image_config(
    repo: Path,
    service_name: str,
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    dockerfiles = sorted(
        path for path in repo.rglob("Dockerfile*") if WORKSPACE_DIR not in path.parts
    )
    if not dockerfiles:
        return {}
    images = {}
    for index, dockerfile in enumerate(dockerfiles):
        name = workloads[index]["name"] if index < len(workloads) else service_name
        images[name] = {
            "context": str(dockerfile.parent.relative_to(repo)),
            "dockerfile": str(dockerfile.relative_to(repo)),
            "image": f"ampule/{_dns_fragment(name)}:local",
            "sourceImage": _first_container_image(
                repo,
                workloads[index]["name"],
                workloads[index]["kind"],
                _manifest_paths(repo),
            )
            if index < len(workloads)
            else None,
        }
    return images


def _inferred_workload_role(name: str, *, has_target: bool) -> str:
    if any(part in name.lower() for part in ("redis", "rabbit", "postgres", "mysql")):
        return "dependency"
    if not has_target:
        return "target"
    return "worker"


def _first_container_image(repo: Path, name: str, kind: str, manifests: list[str]) -> str | None:
    for manifest_path in manifests:
        for item in _yaml_documents(repo / manifest_path):
            if item.get("kind") != kind or item.get("metadata", {}).get("name") != name:
                continue
            template = _pod_template(item)
            containers = template.get("spec", {}).get("containers", [])
            if containers and isinstance(containers[0], dict):
                image = containers[0].get("image")
                return str(image) if image else None
    return None


def _pod_template(manifest: dict[str, Any]) -> dict[str, Any]:
    kind = manifest.get("kind")
    if kind == "CronJob":
        return manifest.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {})
    if kind == "ScaledJob":
        return manifest.get("spec", {}).get("jobTargetRef", {}).get("template", {})
    if kind == "Job":
        return manifest.get("spec", {}).get("template", {})
    return manifest.get("spec", {}).get("template", {})


def _yaml_documents(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        raw = yaml.safe_load_all(path.read_text(encoding="utf-8"))
        return tuple(item for item in raw if isinstance(item, dict))
    except (OSError, yaml.YAMLError):
        return ()


def _image_specs(
    images: dict[str, Any],
) -> tuple[tuple[ImageBuildSpec, ...], tuple[ImageReplacement, ...]]:
    builds = []
    replacements = []
    for name, value in images.items():
        if isinstance(value, str):
            builds.append(ImageBuildSpec(str(name), "Dockerfile", value))
            continue
        if not isinstance(value, dict):
            continue
        image = str(value.get("image", f"ampule/{_dns_fragment(str(name))}:local"))
        builds.append(
            ImageBuildSpec(
                name=str(name),
                dockerfile=str(value.get("dockerfile", "Dockerfile")),
                image=image,
                context=str(value.get("context", ".")),
            )
        )
        if value.get("sourceImage"):
            replacements.append(ImageReplacement(str(value["sourceImage"]), image))
    return tuple(builds), tuple(replacements)


def _workload_spec(item: Any) -> WorkloadSpec:
    data = _mapping(item, "deployment.workloads[]")
    return WorkloadSpec(
        name=str(data["name"]),
        role=str(data.get("role", "supporting")),
        kind=str(data.get("kind", "Deployment")),
        readiness=tuple(str(value) for value in data.get("readiness", ())),
    )


def _external_dependency(item: Any) -> ExternalDependencyPolicy:
    data = _mapping(item, "dependencies.external[]")
    return ExternalDependencyPolicy(
        name=str(data["name"]),
        provider=str(data.get("provider", "http")),
        endpoint=str(data["endpoint"]),
        required_env=tuple(str(value) for value in data.get("requiredEnv", ())),
        timeout_seconds=int(data.get("timeoutSeconds", 30)),
        redaction=str(data.get("redaction", "Only presence or absence is recorded.")),
    )


def _follow_up(item: Any) -> FollowUpCheck:
    data = _mapping(item, "traffic.journeys[].followUps[]")
    return FollowUpCheck(
        name=str(data["name"]),
        check_type=str(data.get("type", "manual")),
        target=str(data.get("target", "")),
        expected=str(data.get("expected", "")),
        service_name=str(data["serviceName"]) if data.get("serviceName") else None,
    )


def _agent_mode(config: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    return str(_mapping(config.get("agents", {}), "agents").get("mode", DEFAULT_AGENT_MODE))


def _agent_lines(output: object) -> tuple[str, ...]:
    payload = _jsonable(output)
    return _agent_payload_lines(payload)


def _agent_payload_lines(payload: dict[str, Any]) -> tuple[str, ...]:
    lines = []
    for key in (
        "summary",
        "status",
        "service_name",
        "scenario_id",
        "observed_facts",
        "recommendations",
        "limitations",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            lines.extend(f"{key}: {item}" for item in value)
    return tuple(lines or ("No agent details recorded.",))


def _expected_run_files(run_dir: Path) -> tuple[Path, ...]:
    return (
        run_dir / "chamber.yaml",
        run_dir / "plan.json",
        run_dir / "run-metadata.json",
        run_dir / "findings.json",
        run_dir / "report.md",
    )


def _workload_line(item: dict[str, Any]) -> str:
    return (
        f"{item.get('kind')} {item.get('name')} role={item.get('role')} image={item.get('image')}"
    )


def _config_line(item: dict[str, Any]) -> str:
    return (
        f"{item.get('name')}={item.get('value')} "
        f"required={item.get('required')} secret={item.get('secret')}"
    )


def _external_line(item: dict[str, Any]) -> str:
    return f"{item.get('name')} -> {item.get('endpoint')} ({item.get('provider')})"


def _git_commit(repo: Path) -> str:
    if not repo.exists():
        return "unknown"
    completed = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "external-working-tree"


def _dns_fragment(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value]
    cleaned = "".join(chars).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "service"


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{name} must be a mapping")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise WorkflowError(f"{name} must be a list")
    return list(value)


def _string_list(value: Any, name: str) -> list[str]:
    return [str(item) for item in _list(value, name)]


def _non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{name} must be a non-empty string")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowError(message)
