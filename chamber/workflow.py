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
from urllib.parse import urlparse

import yaml

from chamber.agents import (
    AgentValidationError,
    ChamberAgentContext,
    EvidenceAnalystBrief,
    OnboardingAgentDraft,
    ReportNarrative,
    RunSupervisorBrief,
    ScenarioPlannerBrief,
    TrafficChaosRecommendation,
    deterministic_evidence_analyst_brief,
    deterministic_onboarding_draft,
    deterministic_report_narrative,
    deterministic_run_supervisor_brief,
    deterministic_scenario_planner_brief,
    deterministic_traffic_chaos_recommendation,
    validate_evidence_bound_output,
)
from chamber.agents.sdk import OpenAIAgentsSdkRunner
from chamber.environment import preflight_to_evidence, run_kubernetes_preflight
from chamber.environment.preflight import (
    CommandRunner as KubernetesCommandRunner,
)
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
AGENT_NAMES = (
    "onboarding-agent",
    "scenario-planner-agent",
    "run-supervisor-agent",
    "traffic-chaos-agent",
    "evidence-analyst-agent",
    "report-writer-agent",
)
SECRET_NAME_FRAGMENTS = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "KEY")
PRODUCTION_CONTEXT_FRAGMENTS = ("prod", "production", "aks-prod", "prd", "live")
RUNTIME_PROVIDERS = {"local", "kind", "kubernetes"}
TRAFFIC_ACCESS_MODES = {"port-forward", "endpoint"}
AGENT_DETAIL_LIMIT = 1200
AGENT_DETAIL_TOTAL_LIMIT = 12000
_KUBERNETES_API_GROUPS = {
    "v1",
    "apps",
    "batch",
    "autoscaling",
    "networking.k8s.io",
    "policy",
    "rbac.authorization.k8s.io",
    "apiextensions.k8s.io",
    "keda.sh",
}

AGENT_OUTPUT_TYPES = (
    OnboardingAgentDraft
    | ScenarioPlannerBrief
    | RunSupervisorBrief
    | TrafficChaosRecommendation
    | EvidenceAnalystBrief
    | ReportNarrative
)


class WorkflowError(RuntimeError):
    """Raised when the guided workflow cannot continue."""


class WorkflowSubprocessRunner:
    """Default command runner for guided live Kubernetes assessment."""

    def run(
        self, command: tuple[str, ...], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:  # pragma: no cover
        return subprocess.run(
            command,
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
        )

    def popen(
        self,
        command: tuple[str, ...],
        *,
        stdout: Any | None = subprocess.PIPE,
        stderr: Any | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:  # pragma: no cover
        return subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)


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
                agents_exclude=tuple(args.agents_exclude or ()),
                context=args.context,
                prometheus_url=args.prometheus_url,
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


def load_config(path: Path, *, require_repo: bool = True) -> dict[str, Any]:
    """Load and validate a user-facing chamber config."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkflowError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowError(f"{path}: config must be a YAML mapping")
    validate_config(raw, source=str(path), require_repo=require_repo)
    return raw


def validate_config(
    config: dict[str, Any],
    *,
    source: str = "<memory>",
    require_repo: bool = True,
) -> None:
    """Validate the minimum stable ChamberConfig contract."""

    _require(config.get("kind") == "ChamberConfig", f"{source}.kind must be ChamberConfig")
    service = _mapping(config.get("service"), f"{source}.service")
    _non_empty(service.get("name"), f"{source}.service.name")
    repo = _non_empty(service.get("repo"), f"{source}.service.repo")
    if require_repo and not Path(repo).exists():
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
    agents = _mapping(config.get("agents", {}), f"{source}.agents")
    mode = str(
        agents.get(
            "mode",
            DEFAULT_AGENT_MODE,
        )
    )
    if mode not in AGENT_MODES:
        raise WorkflowError(
            f"{source}.agents.mode must be one of: {', '.join(sorted(AGENT_MODES))}"
        )
    _validate_agent_names(
        _normalized_agent_names(_optional_string_tuple(agents, "exclude")),
        source=f"{source}.agents.exclude",
    )
    runtime = _mapping(config.get("runtime", {}), f"{source}.runtime")
    _validate_runtime(runtime, source=f"{source}.runtime")


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
        namespace_base=str(
            runtime.get("namespaceBase", config.get("namespaceBase", f"chamber-{service['name']}"))
        ),
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
    runtime_plan = _runtime_plan(config, plan)
    _write_plan(target_run_dir, plan, runtime=runtime_plan)
    _write_metadata(
        target_run_dir,
        {
            "run_id": target_run_dir.name,
            "stage": "planned",
            "config": str(config_copy),
            "cleanup_performed": False,
            "agent_mode": _agent_mode(config, None),
            "agent_exclude": _agent_exclusions(config, ()),
            "runtime": runtime_plan,
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
    agents_exclude: tuple[str, ...] = (),
    context: str | None = None,
    prometheus_url: str | None = None,
) -> Path:
    """Run the guided assessment workflow in local artifact-producing mode."""

    if mode == "kubernetes":
        if config is None or repo is not None or resume is not None:
            raise WorkflowError("assess --mode kubernetes requires --config only")
        return _assess_kubernetes_config(
            config,
            agents_mode=agents_mode,
            agents_exclude=agents_exclude,
            context=context,
            prometheus_url=prometheus_url,
            runner=WorkflowSubprocessRunner(),
        )
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
        _apply_agent_exclude_override(generated, agents_exclude)
        config_path = run_dir / "chamber.yaml"
        save_config(generated, config_path)
    else:
        assert config is not None
        loaded = load_config(config)
        if agents_mode:
            loaded.setdefault("agents", {})["mode"] = agents_mode
        _apply_agent_exclude_override(loaded, agents_exclude)
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
            "agent_exclude": _agent_exclusions(config_data, ()),
        },
    )
    render_report_from_run(run_dir)
    return run_dir


def _assess_kubernetes_config(
    config_path: Path,
    *,
    agents_mode: str | None,
    agents_exclude: tuple[str, ...] = (),
    context: str | None,
    prometheus_url: str | None,
    runner: KubernetesCommandRunner,
) -> Path:
    """Run a config-driven live Kubernetes assessment."""

    config = load_config(config_path)
    if agents_mode:
        config.setdefault("agents", {})["mode"] = agents_mode
    _apply_agent_exclude_override(config, agents_exclude)
    runtime = _mapping(config.get("runtime", {}), "runtime")
    if str(runtime.get("provider", "local")) != "kubernetes":
        raise WorkflowError("assess --mode kubernetes requires runtime.provider: kubernetes")
    selected_context = context or str(runtime["kubernetesContext"])
    selected_prometheus = prometheus_url or runtime.get("prometheusUrl")
    init_workspace()
    run_dir = _new_run_dir(str(config["service"]["name"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    config_copy = run_dir / "chamber.yaml"
    save_config(config, config_copy)
    _ensure_run_subdirs(run_dir)
    plan = build_onboarding_plan(config_to_onboarding_spec(config), run_id=run_dir.name)
    runtime_plan = _runtime_plan(config, plan)
    runtime_plan["kubernetes_context"] = selected_context
    if selected_prometheus:
        runtime_plan["prometheus_url"] = str(selected_prometheus)
    _write_plan(run_dir, plan, runtime=runtime_plan)
    _write_agents(run_dir, config, evidence_ids=("plan",), stage="plan")

    preflight = run_kubernetes_preflight(
        context=selected_context,
        namespace=plan.namespace,
        runner=runner,
    )
    _write_json(run_dir / "evidence/preflight.json", preflight_to_evidence(preflight))
    if not preflight.ready:
        _write_metadata(
            run_dir,
            {
                "run_id": run_dir.name,
                "stage": "preflight_failed",
                "mode": "kubernetes",
                "config": str(config_copy),
                "runtime": runtime_plan,
                "cleanup_performed": False,
                "cleanup_notes": ["No Kubernetes resources were applied after failed preflight."],
                "preflight": preflight_to_evidence(preflight),
                "agent_mode": _agent_mode(config, agents_mode),
                "agent_exclude": _agent_exclusions(config, ()),
            },
        )
        raise WorkflowError("Kubernetes preflight failed: " + "; ".join(preflight.blockers))

    commands: list[dict[str, object]] = []
    traffic_result: dict[str, object] = {
        "success": False,
        "command": [],
        "exit_status": None,
        "stdout": "",
        "stderr": "",
        "summary_path": "",
    }
    cleanup_performed = False
    success = False
    failure: Exception | None = None
    try:
        _apply_kubernetes_plan(
            plan.manifests, context=selected_context, runner=runner, commands=commands
        )
        _wait_kubernetes_readiness(plan, context=selected_context, runner=runner, commands=commands)
        traffic_result = _execute_kubernetes_traffic(
            config=config,
            run_dir=run_dir,
            context=selected_context,
            namespace=plan.namespace,
            runner=runner,
        )
        _collect_kubernetes_command_evidence(
            plan,
            context=selected_context,
            runner=runner,
            commands=commands,
        )
        success = bool(traffic_result.get("success"))
    except Exception as exc:
        failure = exc
    finally:
        if bool(runtime.get("cleanup", True)):
            _cleanup_kubernetes(plan, context=selected_context, runner=runner, commands=commands)
            cleanup_performed = True

    _write_json(run_dir / "evidence/kubernetes-commands.json", {"commands": commands})
    _write_json(run_dir / "findings.json", [])
    metadata = {
        "run_id": run_dir.name,
        "stage": "assessed" if failure is None else "failed",
        "mode": "kubernetes",
        "config": str(config_copy),
        "runtime": runtime_plan,
        "provider": "kubernetes",
        "context": selected_context,
        "namespace": plan.namespace,
        "traffic_result": traffic_result,
        "cleanup_performed": cleanup_performed,
        "cleanup_notes": [
            "Deleted chamber-owned Kubernetes resources and namespace."
            if cleanup_performed
            else "Cleanup was disabled by runtime.cleanup."
        ],
        "preflight": preflight_to_evidence(preflight),
        "success": success,
        "agent_mode": _agent_mode(config, agents_mode),
        "agent_exclude": _agent_exclusions(config, ()),
    }
    if failure is not None:
        metadata["error"] = str(failure)
    _write_metadata(run_dir, metadata)
    _write_agents(
        run_dir,
        config,
        evidence_ids=_kubernetes_assess_evidence_ids(traffic_result),
        stage="assess",
    )
    render_report_from_run(run_dir)
    if failure is not None:
        raise failure
    return run_dir


def render_report_from_run(run_dir: Path) -> Path:
    """Render `report.md` from a standard run directory."""

    config = load_config(run_dir / "chamber.yaml", require_repo=False)
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
    assess_parser.add_argument(
        "--agents-exclude",
        action="append",
        default=[],
        metavar="AGENT",
        help="Skip an agent role for this run, for example onboarding-agent.",
    )
    assess_parser.add_argument("--context")
    assess_parser.add_argument("--prometheus-url")
    report_parser = subparsers.add_parser(
        "report",
        description="Render a report from a run directory.",
    )
    report_parser.add_argument("--run", required=True)
    run_parser = subparsers.add_parser("run", description="Run live local kind scenarios.")
    run_parser.add_argument("run_args", nargs=argparse.REMAINDER)
    return parser


def _write_plan(run_dir: Path, plan: Any, *, runtime: dict[str, Any] | None = None) -> None:
    payload = _jsonable(plan)
    if runtime is not None:
        payload["runtime"] = runtime
    _write_json(run_dir / "plan.json", payload)
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


def _apply_kubernetes_plan(
    manifests: tuple[dict[str, Any], ...],
    *,
    context: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> None:
    for manifest in manifests:
        completed = _run_kubernetes_recorded(
            runner,
            ("kubectl", "--context", context, "apply", "-f", "-"),
            commands,
            input_text=yaml.safe_dump(manifest, sort_keys=False),
        )
        _require_command_success(completed, "apply adapted manifest")


def _wait_kubernetes_readiness(
    plan: Any,
    *,
    context: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> None:
    namespace = str(plan.namespace)
    for workload in plan.workloads:
        if workload.kind != "Deployment":
            continue
        completed = _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "rollout",
                "status",
                f"deployment/{workload.name}",
                "--timeout=180s",
            ),
            commands,
        )
        _require_command_success(completed, f"wait for deployment/{workload.name}")
    for workload in plan.workloads:
        if workload.kind != "Service":
            continue
        completed = _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "endpoints",
                workload.name,
                "-o",
                "json",
            ),
            commands,
        )
        _require_command_success(completed, f"read endpoints/{workload.name}")


def _execute_kubernetes_traffic(
    *,
    config: dict[str, Any],
    run_dir: Path,
    context: str,
    namespace: str,
    runner: Any,
) -> dict[str, object]:  # pragma: no cover - covered by real/kind exercise or patched tests
    runtime = _mapping(config["runtime"], "runtime")
    access = _mapping(runtime["trafficAccess"], "runtime.trafficAccess")
    traffic = _mapping(config["traffic"], "traffic")
    journey = _mapping(_list(traffic["journeys"], "traffic.journeys")[0], "traffic.journeys[0]")
    method = str(journey.get("method", "GET"))
    expected_status = int(journey.get("expectedStatus", 200))
    summary_path = run_dir / "evidence/k6-summary.json"
    script_path = run_dir / "evidence/k6.js"
    if access["mode"] == "endpoint":
        target_url = str(access["url"]).rstrip("/") + str(journey.get("path", "/health"))
        port_forward = None
    else:
        service = str(access["service"])
        service_port = int(access["servicePort"])
        local_port = int(access.get("localPort", 18080))
        target_url = f"http://127.0.0.1:{local_port}{journey.get('path', '/health')}"
        port_forward = runner.popen(
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "port-forward",
                f"service/{service}",
                f"{local_port}:{service_port}",
            )
        )
    script_path.write_text(
        "\n".join(
            (
                "import http from 'k6/http';",
                "import { check } from 'k6';",
                "export const options = { vus: 1, iterations: 1 };",
                "export default function () {",
                f"  const res = http.request({method!r}, {target_url!r});",
                f"  check(res, {{ 'status is expected': r => r.status === {expected_status} }});",
                "}",
                "",
            )
        ),
        encoding="utf-8",
    )
    try:
        completed = runner.run(
            ("k6", "run", "--summary-export", str(summary_path), str(script_path))
        )
    finally:
        if port_forward is not None:
            _stop_kubernetes_process(port_forward)
    return {
        "success": completed.returncode == 0,
        "command": list(completed.args) if isinstance(completed.args, tuple | list) else [],
        "exit_status": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "summary_path": str(summary_path),
    }


def _collect_kubernetes_command_evidence(
    plan: Any,
    *,
    context: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> None:
    namespace = str(plan.namespace)
    selector = ",".join(f"{key}={value}" for key, value in sorted(plan.labels.items()))
    evidence_commands = (
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            selector,
            "-o",
            "json",
        ),
        ("kubectl", "--context", context, "-n", namespace, "get", "events", "-o", "json"),
    )
    for command in evidence_commands:
        _run_kubernetes_recorded(runner, command, commands)
    for workload in plan.workloads:
        if workload.kind != "Deployment":
            continue
        _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "logs",
                f"deployment/{workload.name}",
                "--all-containers=true",
                "--tail=200",
            ),
            commands,
        )


def _cleanup_kubernetes(
    plan: Any,
    *,
    context: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> None:
    namespace = str(plan.namespace)
    selector = ",".join(f"{key}={value}" for key, value in sorted(plan.labels.items()))
    cleanup_commands = (
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "delete",
            "all,configmap,secret,networkpolicy",
            "-l",
            selector,
            "--ignore-not-found=true",
        ),
        (
            "kubectl",
            "--context",
            context,
            "delete",
            "namespace",
            namespace,
            "--ignore-not-found=true",
        ),
    )
    for command in cleanup_commands:
        completed = _run_kubernetes_recorded(runner, command, commands)
        _require_command_success(completed, "cleanup chamber-owned resources")


def _run_kubernetes_recorded(
    runner: KubernetesCommandRunner,
    command: tuple[str, ...],
    commands: list[dict[str, object]],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = runner.run(command, input_text=input_text)
    commands.append(
        {
            "command": list(command),
            "exit_status": completed.returncode,
            "stdout": _redact_evidence_text(completed.stdout),
            "stderr": _redact_evidence_text(completed.stderr),
        }
    )
    return completed


def _require_command_success(completed: subprocess.CompletedProcess[str], description: str) -> None:
    if completed.returncode != 0:
        raise WorkflowError(
            f"failed to {description}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def _stop_kubernetes_process(process: subprocess.Popen[str]) -> None:  # pragma: no cover
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _redact_evidence_text(value: str) -> str:
    lines = []
    for line in value.splitlines():
        if any(marker in line.upper() for marker in SECRET_NAME_FRAGMENTS):
            lines.append("<redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)


def _write_agents(
    run_dir: Path,
    config: dict[str, Any],
    *,
    evidence_ids: tuple[str, ...],
    stage: str,
) -> tuple[ReportSection, ...]:
    mode = _agent_mode(config, None)
    agent_dir = run_dir / "agent"
    shutil.rmtree(agent_dir, ignore_errors=True)
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
        missing_signals=_agent_missing_signals(stage=stage, evidence_ids=evidence_ids),
        evidence_summaries=_agent_evidence_summaries(run_dir, evidence_ids),
        evidence_details=_agent_evidence_details(run_dir, evidence_ids),
    )
    excluded_agents = _agent_exclusions(config, ())
    outputs = _agent_outputs(context, mode=mode, excluded_agents=excluded_agents)
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


def _kubernetes_assess_evidence_ids(traffic_result: dict[str, object]) -> tuple[str, ...]:
    evidence_ids = ["plan", "preflight", "kubernetes-commands"]
    if traffic_result.get("summary_path"):
        evidence_ids.append("k6-summary")
    return tuple(evidence_ids)


def _agent_missing_signals(*, stage: str, evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    if stage == "plan":
        return ("live Kubernetes execution",)
    if stage == "assess" and not (
        "local-assessment" in evidence_ids
        or "kubernetes-commands" in evidence_ids
        or "k6-summary" in evidence_ids
    ):
        return ("live Kubernetes execution",)
    return ()


def _agent_evidence_summaries(run_dir: Path, evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    summaries: list[str] = []
    metadata_path = run_dir / "run-metadata.json"
    if metadata_path.exists():
        metadata = _read_json(metadata_path)
        if metadata.get("stage"):
            summaries.append(f"run stage: {metadata['stage']}")
        if "success" in metadata:
            summaries.append(f"run success: {bool(metadata['success'])}")
        traffic = metadata.get("traffic_result")
        if isinstance(traffic, dict):
            if "success" in traffic:
                summaries.append(f"traffic success: {bool(traffic['success'])}")
            if traffic.get("exit_status") is not None:
                summaries.append(f"traffic exit status: {traffic['exit_status']}")
        if "cleanup_performed" in metadata:
            summaries.append(f"cleanup performed: {bool(metadata['cleanup_performed'])}")
    if "preflight" in evidence_ids:
        preflight_path = run_dir / "evidence/preflight.json"
        if preflight_path.exists():
            preflight = _read_json(preflight_path)
            summaries.append(f"preflight ready: {bool(preflight.get('ready'))}")
            blockers = preflight.get("blockers")
            if isinstance(blockers, list) and blockers:
                summaries.append(f"preflight blockers: {len(blockers)}")
    if "kubernetes-commands" in evidence_ids:
        commands_path = run_dir / "evidence/kubernetes-commands.json"
        if commands_path.exists():
            commands = _read_json(commands_path).get("commands")
            if isinstance(commands, list):
                failed = sum(
                    1
                    for item in commands
                    if isinstance(item, dict) and item.get("exit_status") not in {0, None}
                )
                summaries.append(f"kubernetes commands recorded: {len(commands)}")
                summaries.append(f"kubernetes command failures: {failed}")
    if "k6-summary" in evidence_ids:
        k6_path = run_dir / "evidence/k6-summary.json"
        if k6_path.exists():
            metrics = _read_json(k6_path).get("metrics")
            if isinstance(metrics, dict):
                k6_summary = _k6_summary_metrics(metrics)
                checks = k6_summary.get("checks")
                if isinstance(checks, dict):
                    summaries.append(
                        "k6 checks: "
                        f"passes={checks.get('passes', 0)} fails={checks.get('fails', 0)}"
                    )
                summaries.append(
                    "k6 http requests: "
                    f"count={k6_summary.get('http_request_count', 'unknown')}"
                )
                summaries.append(
                    "k6 http request failure rate: "
                    f"{k6_summary.get('http_failure_rate', 'unknown')}"
                )
                summaries.append(
                    "k6 derived failed http requests: "
                    f"{k6_summary.get('derived_failed_http_requests', 'unknown')}"
                )
    if "local-assessment" in evidence_ids:
        local_path = run_dir / "evidence/local-assessment.json"
        if local_path.exists():
            observed = _read_json(local_path).get("observed_facts")
            if isinstance(observed, list):
                summaries.extend(str(item) for item in observed if item)
    return tuple(summaries)


def _agent_evidence_details(run_dir: Path, evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    details: list[str] = []
    if "plan" in evidence_ids and (run_dir / "plan.json").exists():
        plan = _read_json(run_dir / "plan.json")
        details.extend(_plan_agent_details(plan))
    if "preflight" in evidence_ids:
        path = run_dir / "evidence/preflight.json"
        if path.exists():
            details.extend(_preflight_agent_details(_read_json(path)))
    if "kubernetes-commands" in evidence_ids:
        path = run_dir / "evidence/kubernetes-commands.json"
        if path.exists():
            details.extend(_kubernetes_command_agent_details(_read_json(path)))
    if "k6-summary" in evidence_ids:
        path = run_dir / "evidence/k6-summary.json"
        if path.exists():
            details.extend(_k6_agent_details(_read_json(path)))
    if "local-assessment" in evidence_ids:
        path = run_dir / "evidence/local-assessment.json"
        if path.exists():
            details.append(_agent_detail("local-assessment", _read_json(path)))
    return _bounded_agent_details(details)


def _plan_agent_details(plan: dict[str, Any]) -> list[str]:
    workloads = [
        {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "role": item.get("role"),
            "image": item.get("image"),
            "ports": item.get("ports"),
        }
        for item in plan.get("workloads", [])
        if isinstance(item, dict)
    ]
    readiness = [
        {
            "name": item.get("name"),
            "target": item.get("target"),
            "command": item.get("command"),
        }
        for item in plan.get("readiness_checks", [])
        if isinstance(item, dict)
    ]
    external = [
        {
            "name": item.get("name"),
            "provider": item.get("provider"),
            "endpoint": item.get("endpoint"),
        }
        for item in plan.get("external_dependencies", [])
        if isinstance(item, dict)
    ]
    payload = {
        "service_name": plan.get("service_name"),
        "namespace": plan.get("namespace"),
        "workloads": workloads,
        "readiness_checks": readiness,
        "traffic_journey": plan.get("traffic_journey"),
        "external_dependencies": external,
        "runtime": plan.get("runtime"),
        "limitations": plan.get("limitations"),
    }
    return [_agent_detail("plan", payload)]


def _preflight_agent_details(preflight: dict[str, Any]) -> list[str]:
    checks = preflight.get("checks")
    if isinstance(checks, list):
        compact_checks = [
            {
                "name": item.get("name"),
                "passed": item.get("passed"),
                "detail": item.get("detail"),
            }
            for item in checks[:20]
            if isinstance(item, dict)
        ]
    else:
        compact_checks = []
    return [
        _agent_detail(
            "preflight",
            {
                "ready": preflight.get("ready"),
                "namespace": preflight.get("namespace"),
                "context": preflight.get("context"),
                "blockers": preflight.get("blockers"),
                "checks": compact_checks,
            },
        )
    ]


def _kubernetes_command_agent_details(payload: dict[str, Any]) -> list[str]:
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return []
    compact = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        command_text = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        stdout = str(item.get("stdout", ""))
        stderr = str(item.get("stderr", ""))
        if any(
            token in command_text
            for token in ("rollout", "get endpoints", "get events", "logs")
        ):
            compact.append(
                {
                    "command": command_text,
                    "exit_status": item.get("exit_status"),
                    "stdout_excerpt": stdout[:700],
                    "stderr_excerpt": stderr[:300],
                }
            )
    failed = [
        item
        for item in commands
        if isinstance(item, dict) and item.get("exit_status") not in {0, None}
    ]
    return [
        _agent_detail(
            "kubernetes-commands",
            {
                "command_count": len(commands),
                "failed_command_count": len(failed),
                "selected_commands": compact[:10],
            },
        )
    ]


def _k6_agent_details(payload: dict[str, Any]) -> list[str]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return []
    return [_agent_detail("k6-summary", {"metrics": _k6_summary_metrics(metrics)})]


def _k6_summary_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    checks = metrics.get("checks")
    http_reqs = metrics.get("http_reqs")
    http_failed = metrics.get("http_req_failed")
    duration = metrics.get("http_req_duration")
    request_count = _metric_number(http_reqs, "count")
    failure_rate = _metric_number(http_failed, "value")
    failed_count = None
    if request_count is not None and failure_rate is not None:
        failed_count = round(request_count * failure_rate, 6)
    summary: dict[str, Any] = {
        "http_request_count": request_count,
        "http_failure_rate": failure_rate,
        "derived_failed_http_requests": failed_count,
    }
    if isinstance(checks, dict):
        summary["checks"] = {
            "passes": checks.get("passes", 0),
            "fails": checks.get("fails", 0),
            "value": checks.get("value"),
        }
    if isinstance(duration, dict):
        summary["http_req_duration_ms"] = {
            key: duration.get(key)
            for key in ("avg", "min", "med", "max", "p(90)", "p(95)")
            if key in duration
        }
    return summary


def _metric_number(metrics: object, key: str) -> int | float | None:
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _agent_detail(source: str, payload: Any) -> str:
    rendered = json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=True)
    redacted = _redact_evidence_text(rendered)
    if len(redacted) > AGENT_DETAIL_LIMIT:
        redacted = redacted[: AGENT_DETAIL_LIMIT - 15] + "...<truncated>"
    return f"{source}: {redacted}"


def _bounded_agent_details(details: list[str]) -> tuple[str, ...]:
    bounded: list[str] = []
    total = 0
    for detail in details:
        if total + len(detail) > AGENT_DETAIL_TOTAL_LIMIT:
            break
        bounded.append(detail)
        total += len(detail)
    return tuple(bounded)


def _agent_outputs(
    context: ChamberAgentContext,
    *,
    mode: str,
    excluded_agents: tuple[str, ...] = (),
) -> dict[str, AGENT_OUTPUT_TYPES]:
    runner = OpenAIAgentsSdkRunner() if mode == "live" else None
    outputs: dict[str, AGENT_OUTPUT_TYPES] = {}
    excluded = set(excluded_agents)
    for spec in _agent_specs():
        if spec["name"] in excluded:
            continue
        if runner is None:
            output = spec["offline"](context)
        else:
            output = runner.run_structured(
                name=spec["name"],
                instructions=spec["instructions"],
                input_text=_agent_input(context, spec["name"]),
                output_type=spec["output_type"],
            )
        outputs[spec["filename"]] = output
    return outputs


def _agent_input(context: ChamberAgentContext, agent_name: str) -> str:
    payload = {
        "agent": agent_name,
        "feature_request_constraints": {
            "evidence_bound": True,
            "available_evidence_ids": context.evidence_ids,
            "evidence_summaries_are_authoritative": True,
            "must_not_invent_secrets": True,
            "must_not_mutate_target_repo": True,
            "must_not_bypass_safety_checks": True,
            "must_stay_inside_chamber_owned_resources": True,
            "external_dependencies_require_opt_in": True,
        },
        "context": _jsonable(context),
    }
    return json.dumps(payload, sort_keys=True)


def _agent_specs() -> tuple[dict[str, Any], ...]:
    return (
        {
            "filename": "onboarding-agent.json",
            "name": "onboarding-agent",
            "output_type": OnboardingAgentDraft,
            "offline": deterministic_onboarding_draft,
            "instructions": _agent_instructions(
                "Inspect repository layout, manifests, Dockerfiles, ports, env vars, "
                "and dependency hints. Produce a draft chamber.yaml summary. Do not "
                "invent secrets, print secret values, or mutate the target repository."
            ),
        },
        {
            "filename": "scenario-planner-agent.json",
            "name": "scenario-planner-agent",
            "output_type": ScenarioPlannerBrief,
            "offline": deterministic_scenario_planner_brief,
            "instructions": _agent_instructions(
                "Convert the service shape and chamber config into baseline, traffic, "
                "dependency, recovery, and fault scenario intent. Produce only bounded "
                "plans that can be executed by Ampule Chamber safety checks."
            ),
        },
        {
            "filename": "run-supervisor-agent.json",
            "name": "run-supervisor-agent",
            "output_type": RunSupervisorBrief,
            "offline": deterministic_run_supervisor_brief,
            "instructions": _agent_instructions(
                "Explain blocked readiness, missing endpoints, failed probes, unsafe "
                "preconditions, and cleanup status from supplied run state. Do not run "
                "commands or bypass safety checks."
            ),
        },
        {
            "filename": "traffic-chaos-agent.json",
            "name": "traffic-chaos-agent",
            "output_type": TrafficChaosRecommendation,
            "offline": deterministic_traffic_chaos_recommendation,
            "instructions": _agent_instructions(
                "Recommend load and fault profiles from the approved plan. Stay within "
                "chamber-owned resources, configured policies, explicit dependency "
                "opt-ins, and available evidence."
            ),
        },
        {
            "filename": "evidence-analyst-agent.json",
            "name": "evidence-analyst-agent",
            "output_type": EvidenceAnalystBrief,
            "offline": deterministic_evidence_analyst_brief,
            "instructions": _agent_instructions(
                "Review Kubernetes events, logs, metrics, k6 summaries, timelines, "
                "and findings supplied in context. Separate observed facts from "
                "hypotheses and cite only available evidence IDs."
            ),
        },
        {
            "filename": "report-writer-agent.json",
            "name": "report-writer-agent",
            "output_type": ReportNarrative,
            "offline": deterministic_report_narrative,
            "instructions": _agent_instructions(
                "Improve report narrative using only validated evidence, findings, "
                "and limitations. Cite only supplied evidence IDs and preserve missing "
                "signals as limitations."
            ),
        },
    )


def _agent_instructions(role: str) -> str:
    return (
        "You are a bounded Ampule Chamber reliability-testing agent. "
        "Return only the requested structured output. "
        "Every evidence citation or evidence_ids entry must come from the supplied "
        "available_evidence_ids. If evidence is missing, record a limitation instead "
        "of inventing facts. " + role
    )


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
    if (run_dir / "evidence/preflight.json").exists():
        evidence.append(
            EvidenceReference(
                "preflight",
                "kubectl",
                "kubernetes_preflight",
                str(metadata.get("namespace", plan.get("namespace", ""))),
                "from-file",
                str(run_dir / "evidence/preflight.json"),
            )
        )
    if (run_dir / "evidence/kubernetes-commands.json").exists():
        evidence.append(
            EvidenceReference(
                "kubernetes-commands",
                "kubectl",
                "kubernetes_runtime",
                str(metadata.get("namespace", plan.get("namespace", ""))),
                "from-file",
                str(run_dir / "evidence/kubernetes-commands.json"),
            )
        )
    traffic_result = _mapping(metadata.get("traffic_result", {}), "metadata.traffic_result")
    summary_path = traffic_result.get("summary_path")
    if summary_path:
        evidence.append(
            EvidenceReference(
                "k6-summary",
                "k6",
                "traffic_summary",
                str(metadata.get("namespace", plan.get("namespace", ""))),
                "from-file",
                str(summary_path),
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
            provider=str(
                metadata.get(
                    "provider", _mapping(plan.get("runtime", {}), "runtime").get("provider", "kind")
                )
            ),
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
        limitations=_unique_strings(
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
        if WORKSPACE_DIR not in path.parts and _contains_kubernetes_resource(path)
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


def _contains_kubernetes_resource(path: Path) -> bool:
    for item in _yaml_documents(path):
        if not isinstance(item, dict):
            continue
        api_version = str(item.get("apiVersion", ""))
        kind = str(item.get("kind", ""))
        if api_version and kind and api_version.split("/", 1)[0] in _KUBERNETES_API_GROUPS:
            return True
    return False


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
    replacements = list(_explicit_image_replacements(images))
    explicit_sources = {item.source for item in replacements}
    for name, value in images.items():
        if name == "replacements":
            continue
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
        if value.get("sourceImage") and str(value["sourceImage"]) not in explicit_sources:
            replacements.append(ImageReplacement(str(value["sourceImage"]), image))
    return tuple(builds), tuple(replacements)


def _explicit_image_replacements(images: dict[str, Any]) -> tuple[ImageReplacement, ...]:
    replacements = []
    for item in _list(images.get("replacements", []), "deployment.images.replacements"):
        replacement = _mapping(item, "deployment.images.replacements[]")
        replacements.append(
            ImageReplacement(
                source=_non_empty(
                    replacement.get("source"),
                    "deployment.images.replacements[].source",
                ),
                target=_non_empty(
                    replacement.get("target"),
                    "deployment.images.replacements[].target",
                ),
            )
        )
    return tuple(replacements)


def _validate_runtime(runtime: dict[str, Any], *, source: str) -> None:
    provider = str(runtime.get("provider", "local"))
    if provider not in RUNTIME_PROVIDERS:
        raise WorkflowError(
            f"{source}.provider must be one of: {', '.join(sorted(RUNTIME_PROVIDERS))}"
        )
    for key, value in runtime.items():
        if key in {
            "provider",
            "kubernetesContext",
            "namespaceBase",
            "cleanup",
            "prometheusUrl",
            "trafficAccess",
            "requiredEnv",
            "secretEnv",
            "config",
        }:
            continue
        if isinstance(value, str) and _secret_like_name(str(key)):
            raise WorkflowError(
                f"{source}.{key} looks secret-like; move it to runtime.secretEnv "
                "so run artifacts record only presence or absence"
            )
    _validate_runtime_config(
        _mapping(runtime.get("config", {}), f"{source}.config"),
        source=f"{source}.config",
    )
    if provider != "kubernetes":
        return
    _non_empty(runtime.get("kubernetesContext"), f"{source}.kubernetesContext")
    if "trafficAccess" not in runtime:
        raise WorkflowError(f"{source}.trafficAccess is required for Kubernetes runtime")
    _validate_traffic_access(
        _mapping(runtime.get("trafficAccess"), f"{source}.trafficAccess"),
        source=f"{source}.trafficAccess",
    )
    prometheus_url = runtime.get("prometheusUrl")
    if prometheus_url is not None:
        _require_http_url(str(prometheus_url), f"{source}.prometheusUrl")


def _validate_traffic_access(access: dict[str, Any], *, source: str) -> None:
    mode = str(access.get("mode", ""))
    if mode not in TRAFFIC_ACCESS_MODES:
        raise WorkflowError(
            f"{source}.mode must be one of: {', '.join(sorted(TRAFFIC_ACCESS_MODES))}"
        )
    if mode == "port-forward":
        _non_empty(access.get("service"), f"{source}.service")
        port = access.get("servicePort")
        if not isinstance(port, int) or port <= 0:
            raise WorkflowError(f"{source}.servicePort must be a positive integer")
    if mode == "endpoint":
        _require_http_url(_non_empty(access.get("url"), f"{source}.url"), f"{source}.url")


def _runtime_plan(config: dict[str, Any], plan: Any) -> dict[str, Any]:
    runtime = _mapping(config.get("runtime", {}), "runtime")
    provider = str(runtime.get("provider", "local"))
    namespace_base = str(runtime.get("namespaceBase", config.get("namespaceBase", "")))
    result: dict[str, Any] = {
        "provider": provider,
        "namespace": str(getattr(plan, "namespace", "")),
        "namespace_base": namespace_base or None,
        "cleanup": bool(runtime.get("cleanup", True)),
        "traffic_access": _jsonable(runtime.get("trafficAccess", {})),
        "image_replacements": [
            {"source": item.source, "target": item.target}
            for item in _explicit_image_replacements(
                _mapping(config.get("deployment", {}).get("images", {}), "deployment.images")
            )
        ],
    }
    if provider == "kubernetes":
        result["kubernetes_context"] = str(runtime["kubernetesContext"])
        if runtime.get("prometheusUrl"):
            result["prometheus_url"] = str(runtime["prometheusUrl"])
    return result


def _require_http_url(url: str, name: str) -> None:
    scheme = urlparse(url).scheme
    if scheme not in {"http", "https"}:
        raise WorkflowError(f"{name} must be an HTTP(S) URL")


def _validate_runtime_config(config: dict[str, Any], *, source: str) -> None:
    for key in config:
        if _secret_like_name(str(key)):
            raise WorkflowError(
                f"{source}.{key} looks secret-like; move it to runtime.secretEnv "
                "so run artifacts record only presence or absence"
            )


def _secret_like_name(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    return any(fragment in normalized for fragment in SECRET_NAME_FRAGMENTS)


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


def _agent_exclusions(config: dict[str, Any], override: tuple[str, ...]) -> tuple[str, ...]:
    configured = _optional_string_tuple(_mapping(config.get("agents", {}), "agents"), "exclude")
    exclusions = _normalized_agent_names((*configured, *override))
    _validate_agent_names(exclusions, source="agents.exclude")
    return exclusions


def _apply_agent_exclude_override(config: dict[str, Any], override: tuple[str, ...]) -> None:
    if not override:
        return
    agents = config.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise WorkflowError("agents must be a mapping")
    agents["exclude"] = list(_agent_exclusions(config, override))


def _validate_agent_names(values: tuple[str, ...], *, source: str) -> None:
    unknown = tuple(value for value in values if value not in AGENT_NAMES)
    if unknown:
        allowed = ", ".join(AGENT_NAMES)
        raise WorkflowError(
            f"{source} contains unknown agent role(s): {', '.join(unknown)}; "
            f"allowed: {allowed}"
        )


def _normalized_agent_names(values: tuple[str, ...]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        names.extend(item.strip() for item in value.split(",") if item.strip())
    return tuple(dict.fromkeys(names))


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
            lines.extend(f"{key}: {item}" for item in _unique_strings(value))
    return tuple(lines or ("No agent details recorded.",))


def _unique_strings(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


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


def _optional_string_tuple(value: dict[str, Any], key: str) -> tuple[str, ...]:
    if key not in value:
        return ()
    return tuple(_string_list(value[key], key))


def _non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{name} must be a non-empty string")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowError(message)
