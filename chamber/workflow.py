"""Guided Ampule Chamber workflow CLI and run-directory persistence."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

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
from chamber.application import analyze_guided_run, build_assessment_result
from chamber.environment import preflight_to_evidence, run_kubernetes_preflight
from chamber.environment.preflight import (
    CommandRunner as KubernetesCommandRunner,
)
from chamber.environment.preflight import (
    run_kubernetes_attach_preflight,
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
    ReportFinding,
    ReportInput,
    ReportSection,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)
from chamber.runs import (
    append_run_event,
    initialize_run_record,
    new_run_directory,
    read_json_value,
    refresh_evidence_manifest,
    registered_evidence,
    sync_run_record,
    write_json_atomic,
)

WORKSPACE_DIR = os.environ.get("AMPULE_CHAMBER_WORKSPACE", ".chamber")
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
RUNTIME_MODES = {"deploy", "attach"}
ATTACH_FAULT_TYPES = {"pod_kill", "deployment_scale"}
ATTACH_ALLOW_FAULTS_KEY = "chamber.ampule.dev/allow-faults"
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
            from chamber.application.service import ChamberApplication

            workspace = ChamberApplication(Path(args.workspace)).initialize()
            print(f"initialized {workspace}")
            return 0
        if args.command == "onboard":
            from chamber.application.service import ChamberApplication

            config = ChamberApplication().onboard(Path(args.repo), output=Path(args.output))
            print(f"wrote {config}")
            return 0
        if args.command == "plan":
            from chamber.application.service import ChamberApplication

            run_dir = ChamberApplication().plan(
                Path(args.config),
                run_dir=Path(args.run_dir) if args.run_dir else None,
            )
            print(f"planned {run_dir}")
            return 0
        if args.command == "assess":
            from chamber.application.service import ChamberApplication

            run_dir = ChamberApplication().assess(
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
            from chamber.application.service import ChamberApplication

            report_path = ChamberApplication().report(Path(args.run))
            print(f"report {report_path}")
            return 0
        if args.command == "ui":
            from chamber.control_plane.server import run_server

            return run_server(
                host=args.host,
                port=args.port,
                workspace=Path(args.workspace),
                open_browser=not args.no_open,
                allow_remote=args.allow_remote,
            )
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
    runtime = _mapping(config.get("runtime", {}), f"{source}.runtime")
    runtime_mode = str(runtime.get("mode", "deploy"))
    if require_repo and runtime_mode != "attach" and not Path(repo).exists():
        raise WorkflowError(f"{source}.service.repo does not exist: {repo}")
    deployment = _mapping(config.get("deployment"), f"{source}.deployment")
    manifests = _string_list(deployment.get("manifests"), f"{source}.deployment.manifests")
    if runtime_mode != "attach" and not manifests:
        raise WorkflowError(f"{source}.deployment.manifests must not be empty")
    workloads = _list(deployment.get("workloads", []), f"{source}.deployment.workloads")
    if not workloads:
        raise WorkflowError(f"{source}.deployment.workloads must not be empty")
    if runtime_mode == "attach":
        services = _list(deployment.get("services", []), f"{source}.deployment.services")
        if not services:
            raise WorkflowError(f"{source}.deployment.services must not be empty in attach mode")
        for index, service_item in enumerate(services):
            service_doc = _mapping(service_item, f"{source}.deployment.services[{index}]")
            _non_empty(service_doc.get("name"), f"{source}.deployment.services[{index}].name")
            port = service_doc.get("port")
            if not isinstance(port, int) or port <= 0:
                raise WorkflowError(
                    f"{source}.deployment.services[{index}].port must be a positive integer"
                )
    for index, workload_item in enumerate(workloads):
        workload_doc = _mapping(workload_item, f"{source}.deployment.workloads[{index}]")
        _non_empty(workload_doc.get("name"), f"{source}.deployment.workloads[{index}].name")
        _non_empty(
            workload_doc.get("kind", "Deployment"),
            f"{source}.deployment.workloads[{index}].kind",
        )
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
    runtime = _mapping(config.get("runtime", {}), "runtime")
    if (
        str(runtime.get("provider", "local")) == "kubernetes"
        and str(runtime.get("mode", "deploy")) == "attach"
    ):
        deployment = _mapping(config["deployment"], "deployment")
        namespace = str(runtime["namespace"])
        runtime_plan = _attach_runtime_plan(config, namespace=namespace)
        _write_attach_plan(
            target_run_dir,
            config=config,
            namespace=namespace,
            workloads=tuple(_attach_workload(item) for item in deployment["workloads"]),
            services=tuple(_attach_service(item) for item in deployment["services"]),
            runtime=runtime_plan,
        )
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
    metadata = {
        "run_id": run_dir.name,
        "stage": "assessed",
        "mode": mode,
        "config": str(config_path),
        "cleanup_performed": True,
        "cleanup_notes": ["No live Kubernetes resources were created by local assessment."],
        "agent_mode": _agent_mode(config_data, agents_mode),
        "agent_exclude": _agent_exclusions(config_data, ()),
    }
    _write_metadata(run_dir, metadata)
    _finalize_guided_result(run_dir, config=config_data, metadata=metadata)
    _write_agents(run_dir, config_data, evidence_ids=("plan", "local-assessment"), stage="assess")
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
    if str(runtime.get("mode", "deploy")) == "attach":
        return _assess_kubernetes_attach_config(
            config,
            config_path=config_path,
            agents_mode=agents_mode,
            agents_exclude=agents_exclude,
            context=context,
            prometheus_url=prometheus_url,
            runner=runner,
        )
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
        metadata = {
            "run_id": run_dir.name,
            "stage": "preflight_failed",
            "mode": "kubernetes",
            "config": str(config_copy),
            "runtime": runtime_plan,
            "provider": "kubernetes",
            "context": selected_context,
            "namespace": plan.namespace,
            "cleanup_performed": False,
            "cleanup_notes": ["No Kubernetes resources were applied after failed preflight."],
            "preflight": preflight_to_evidence(preflight),
            "success": False,
            "agent_mode": _agent_mode(config, agents_mode),
            "agent_exclude": _agent_exclusions(config, ()),
        }
        _write_metadata(run_dir, metadata)
        _finalize_guided_result(run_dir, config=config, metadata=metadata)
        render_report_from_run(run_dir)
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
    failure: BaseException | None = None
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
        if selected_prometheus:
            _collect_prometheus_memory_evidence(
                run_dir,
                prometheus_url=str(selected_prometheus),
                namespace=plan.namespace,
            )
        success = bool(traffic_result.get("success"))
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
    finally:
        if bool(runtime.get("cleanup", True)):
            _cleanup_kubernetes(plan, context=selected_context, runner=runner, commands=commands)
            cleanup_performed = True

    _write_json(run_dir / "evidence/kubernetes-commands.json", {"commands": commands})
    metadata = {
        "run_id": run_dir.name,
        "stage": (
            "assessed"
            if failure is None
            else "cancelled"
            if isinstance(failure, KeyboardInterrupt)
            else "failed"
        ),
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
    _finalize_guided_result(run_dir, config=config, metadata=metadata)
    _write_agents(
        run_dir,
        config,
        evidence_ids=_kubernetes_assess_evidence_ids(traffic_result, run_dir=run_dir),
        stage="assess",
    )
    render_report_from_run(run_dir)
    if failure is not None:
        raise failure
    return run_dir


def _assess_kubernetes_attach_config(
    config: dict[str, Any],
    *,
    config_path: Path,
    agents_mode: str | None,
    agents_exclude: tuple[str, ...],
    context: str | None,
    prometheus_url: str | None,
    runner: KubernetesCommandRunner,
) -> Path:
    """Run an in-place assessment against existing Kubernetes resources."""

    runtime = _mapping(config["runtime"], "runtime")
    deployment = _mapping(config["deployment"], "deployment")
    selected_context = context or str(runtime["kubernetesContext"])
    namespace = str(runtime["namespace"])
    selected_prometheus = prometheus_url or runtime.get("prometheusUrl")
    init_workspace()
    run_dir = _new_run_dir(str(config["service"]["name"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    config_copy = run_dir / "chamber.yaml"
    save_config(config, config_copy)
    _ensure_run_subdirs(run_dir)

    workloads = tuple(_attach_workload(item) for item in deployment["workloads"])
    services = tuple(_attach_service(item) for item in deployment["services"])
    runtime_plan = _attach_runtime_plan(config, namespace=namespace)
    runtime_plan["kubernetes_context"] = selected_context
    if selected_prometheus:
        runtime_plan["prometheus_url"] = str(selected_prometheus)
    _write_attach_plan(
        run_dir,
        config=config,
        namespace=namespace,
        workloads=workloads,
        services=services,
        runtime=runtime_plan,
    )
    _write_agents(run_dir, config, evidence_ids=("plan",), stage="plan")

    preflight = run_kubernetes_attach_preflight(
        context=selected_context,
        namespace=namespace,
        workloads=tuple((item["kind"], item["name"]) for item in workloads),
        services=tuple(item["name"] for item in services),
        fault_types=tuple(
            str(_mapping(item, "runtime.faults[]").get("type"))
            for item in runtime.get("faults", ())
        ),
        runner=runner,
    )
    _write_json(run_dir / "evidence/preflight.json", preflight_to_evidence(preflight))
    if not preflight.ready:
        metadata = {
            "run_id": run_dir.name,
            "stage": "preflight_failed",
            "mode": "kubernetes",
            "runtime_mode": "attach",
            "config": str(config_copy),
            "runtime": runtime_plan,
            "provider": "kubernetes",
            "context": selected_context,
            "namespace": namespace,
            "cleanup_performed": False,
            "cleanup_notes": [
                "Attach preflight failed before traffic, faults, or cleanup mutations."
            ],
            "preflight": preflight_to_evidence(preflight),
            "success": False,
            "agent_mode": _agent_mode(config, agents_mode),
            "agent_exclude": _agent_exclusions(config, agents_exclude),
        }
        _write_metadata(run_dir, metadata)
        _finalize_guided_result(run_dir, config=config, metadata=metadata)
        render_report_from_run(run_dir)
        raise WorkflowError("Kubernetes attach preflight failed: " + "; ".join(preflight.blockers))

    commands: list[dict[str, object]] = []
    traffic_result: dict[str, object] = {
        "success": False,
        "command": [],
        "exit_status": None,
        "stdout": "",
        "stderr": "",
        "summary_path": "",
    }
    rollback_evidence: dict[str, object] = {"faults_requested": False, "actions": []}
    success = False
    failure: BaseException | None = None
    discovery: dict[str, Any] = {}
    try:
        discovery = _discover_attach_target(
            context=selected_context,
            namespace=namespace,
            workloads=workloads,
            services=services,
            runner=runner,
            commands=commands,
        )
        _write_json(run_dir / "evidence/attach-discovery.json", discovery)
        _write_json(run_dir / "evidence/pre-test-state.json", discovery)
        faults = tuple(_mapping(item, "runtime.faults[]") for item in runtime.get("faults", ()))
        if faults:
            rollback_evidence = _run_attach_faults(
                faults,
                discovery=discovery,
                context=selected_context,
                namespace=namespace,
                runner=runner,
                commands=commands,
            )
        traffic_result = _execute_kubernetes_traffic(
            config=config,
            run_dir=run_dir,
            context=selected_context,
            namespace=namespace,
            runner=runner,
        )
        _collect_attach_kubernetes_evidence(
            discovery,
            context=selected_context,
            namespace=namespace,
            runner=runner,
            commands=commands,
        )
        if selected_prometheus:
            _collect_prometheus_memory_evidence(
                run_dir,
                prometheus_url=str(selected_prometheus),
                namespace=namespace,
                pod_names=tuple(str(item["name"]) for item in discovery.get("pods", ())),
            )
        success = bool(traffic_result.get("success"))
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
    finally:
        if rollback_evidence.get("pending_restore"):
            rollback_evidence = _restore_attach_faults(
                rollback_evidence,
                context=selected_context,
                namespace=namespace,
                runner=runner,
                commands=commands,
            )

    _write_json(run_dir / "evidence/rollback.json", rollback_evidence)
    _write_json(run_dir / "evidence/kubernetes-commands.json", {"commands": commands})
    rollback_verified = bool(rollback_evidence.get("verified", True))
    stage = (
        "assessed"
        if failure is None and rollback_verified
        else "cancelled"
        if isinstance(failure, KeyboardInterrupt)
        else "failed"
    )
    metadata = {
        "run_id": run_dir.name,
        "stage": stage,
        "mode": "kubernetes",
        "runtime_mode": "attach",
        "config": str(config_copy),
        "runtime": runtime_plan,
        "provider": "kubernetes",
        "context": selected_context,
        "namespace": namespace,
        "traffic_result": traffic_result,
        "cleanup_performed": False,
        "cleanup_notes": [
            "Attach mode did not delete the target namespace or externally deployed resources.",
            "Port-forward setup was stopped after traffic execution.",
        ],
        "rollback": rollback_evidence,
        "preflight": preflight_to_evidence(preflight),
        "success": success and rollback_verified,
        "agent_mode": _agent_mode(config, agents_mode),
        "agent_exclude": _agent_exclusions(config, agents_exclude),
    }
    if failure is not None:
        metadata["error"] = str(failure)
    if not rollback_verified:
        metadata["error"] = "attach fault rollback could not be verified"
    _write_metadata(run_dir, metadata)
    _finalize_guided_result(run_dir, config=config, metadata=metadata)
    _write_agents(
        run_dir,
        config,
        evidence_ids=_kubernetes_assess_evidence_ids(traffic_result, run_dir=run_dir),
        stage="assess",
    )
    render_report_from_run(run_dir)
    if failure is not None:
        raise failure
    if not rollback_verified:
        raise WorkflowError("attach fault rollback could not be verified")
    return run_dir


def render_report_from_run(run_dir: Path) -> Path:
    """Render `report.md` from a standard run directory."""

    config = load_config(run_dir / "chamber.yaml", require_repo=False)
    plan = _read_json(run_dir / "plan.json")
    metadata = _read_json(run_dir / "run-metadata.json")
    refresh_evidence_manifest(run_dir)
    if not (run_dir / "result.json").exists() or not (run_dir / "findings.json").exists():
        _finalize_guided_result(run_dir, config=config, metadata=metadata)
    report = _report_input(run_dir, config=config, plan=plan, metadata=metadata)
    report_path = run_dir / "report.md"
    report_path.write_text(render_markdown_report(report), encoding="utf-8")
    append_run_event(
        run_dir,
        state=str(initialize_run_record(run_dir).get("state", "reporting")),
        event_type="report_generated",
        payload={"path": str(report_path)},
    )
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
    ui_parser = subparsers.add_parser(
        "ui",
        description="Launch the local Ampule Chamber control plane.",
    )
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--workspace", default=WORKSPACE_DIR)
    ui_parser.add_argument("--no-open", action="store_true")
    ui_parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding beyond loopback; secure the network boundary yourself.",
    )
    run_parser = subparsers.add_parser("run", description="Run live local kind scenarios.")
    run_parser.add_argument("run_args", nargs=argparse.REMAINDER)
    return parser


def _write_attach_plan(
    run_dir: Path,
    *,
    config: dict[str, Any],
    namespace: str,
    workloads: tuple[dict[str, Any], ...],
    services: tuple[dict[str, Any], ...],
    runtime: dict[str, Any],
) -> None:
    service = _mapping(config["service"], "service")
    traffic = _mapping(config["traffic"], "traffic")
    _write_json(
        run_dir / "plan.json",
        {
            "service_name": str(service["name"]),
            "namespace": namespace,
            "runtime": runtime,
            "workloads": workloads,
            "services": services,
            "traffic_journey": _jsonable(_traffic_journeys(traffic)),
            "manifests": [],
            "redacted_config": [],
            "external_dependencies": [],
            "limitations": [
                "Attach mode assesses externally deployed resources in place.",
                "Attach mode does not adapt, apply, delete, or own target manifests.",
            ],
        },
    )
    adapted_dir = run_dir / "adapted-manifests"
    shutil.rmtree(adapted_dir, ignore_errors=True)
    adapted_dir.mkdir(parents=True, exist_ok=True)


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


def _attach_workload(item: Any) -> dict[str, Any]:
    workload = _mapping(item, "deployment.workloads[]")
    return {
        "name": _non_empty(workload.get("name"), "deployment.workloads[].name"),
        "kind": str(workload.get("kind", "Deployment")),
        "role": str(workload.get("role", "target")),
    }


def _attach_service(item: Any) -> dict[str, Any]:
    service = _mapping(item, "deployment.services[]")
    return {
        "name": _non_empty(service.get("name"), "deployment.services[].name"),
        "port": int(service["port"]),
    }


def _discover_attach_target(
    *,
    context: str,
    namespace: str,
    workloads: tuple[dict[str, Any], ...],
    services: tuple[dict[str, Any], ...],
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> dict[str, Any]:
    namespace_doc = _kubectl_json(
        runner,
        ("kubectl", "--context", context, "get", "namespace", namespace, "-o", "json"),
        commands,
        "read target namespace",
    )
    discovered_workloads = []
    selectors: list[dict[str, str]] = []
    for workload in workloads:
        kind = str(workload["kind"])
        name = str(workload["name"])
        resource = f"{kind.lower()}/{name}"
        workload_doc = _kubectl_json(
            runner,
            ("kubectl", "--context", context, "-n", namespace, "get", resource, "-o", "json"),
            commands,
            f"read {resource}",
        )
        selector = _match_labels_selector(workload_doc)
        if selector:
            selectors.append(selector)
        if kind == "Deployment":
            _run_kubernetes_recorded(
                runner,
                (
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    f"deployment/{name}",
                    "--timeout=180s",
                ),
                commands,
            )
        discovered_workloads.append(
            {
                "kind": kind,
                "name": name,
                "role": workload.get("role", "target"),
                "selector": selector,
                "replicas": workload_doc.get("spec", {}).get("replicas"),
                "labels": workload_doc.get("metadata", {}).get("labels", {}),
                "annotations": workload_doc.get("metadata", {}).get("annotations", {}),
                "owner_references": workload_doc.get("metadata", {}).get("ownerReferences", []),
            }
        )
    discovered_services = []
    for service in services:
        name = str(service["name"])
        service_doc = _kubectl_json(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "service",
                name,
                "-o",
                "json",
            ),
            commands,
            f"read service/{name}",
        )
        endpoints_doc = _kubectl_json(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "endpoints",
                name,
                "-o",
                "json",
            ),
            commands,
            f"read endpoints/{name}",
        )
        service_selector = service_doc.get("spec", {}).get("selector", {})
        if isinstance(service_selector, dict) and service_selector:
            selectors.append({str(key): str(value) for key, value in service_selector.items()})
        discovered_services.append(
            {
                "name": name,
                "port": service["port"],
                "selector": service_selector if isinstance(service_selector, dict) else {},
                "ports": service_doc.get("spec", {}).get("ports", []),
                "endpoints": endpoints_doc.get("subsets", []),
            }
        )
    pods = _discover_attach_pods(
        context=context,
        namespace=namespace,
        selectors=tuple(selectors),
        runner=runner,
        commands=commands,
    )
    return {
        "mode": "attach",
        "namespace": {
            "name": namespace,
            "labels": namespace_doc.get("metadata", {}).get("labels", {}),
            "annotations": namespace_doc.get("metadata", {}).get("annotations", {}),
        },
        "workloads": discovered_workloads,
        "services": discovered_services,
        "pods": pods,
    }


def _discover_attach_pods(
    *,
    context: str,
    namespace: str,
    selectors: tuple[dict[str, str], ...],
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> list[dict[str, Any]]:
    pods_by_name: dict[str, dict[str, Any]] = {}
    for selector in selectors:
        selector_text = _selector_text(selector)
        if not selector_text:
            continue
        payload = _kubectl_json(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "pods",
                "-l",
                selector_text,
                "-o",
                "json",
            ),
            commands,
            f"read pods matching {selector_text}",
        )
        for pod in payload.get("items", []):
            if not isinstance(pod, dict):
                continue
            name = pod.get("metadata", {}).get("name")
            if not name:
                continue
            pods_by_name[str(name)] = {
                "name": str(name),
                "phase": pod.get("status", {}).get("phase"),
                "labels": pod.get("metadata", {}).get("labels", {}),
                "owner_references": pod.get("metadata", {}).get("ownerReferences", []),
                "container_statuses": pod.get("status", {}).get("containerStatuses", []),
            }
    return list(pods_by_name.values())


def _kubectl_json(
    runner: KubernetesCommandRunner,
    command: tuple[str, ...],
    commands: list[dict[str, object]],
    description: str,
) -> dict[str, Any]:
    completed = _run_kubernetes_recorded(runner, command, commands)
    _require_command_success(completed, description)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"failed to parse {description} JSON") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{description} JSON must be an object")
    return payload


def _match_labels_selector(resource: dict[str, Any]) -> dict[str, str]:
    selector = resource.get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not isinstance(selector, dict):
        return {}
    return {str(key): str(value) for key, value in selector.items()}


def _selector_text(selector: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(selector.items()))


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
    journeys = _traffic_journeys(traffic)
    summary_path = run_dir / "evidence/k6-summary.json"
    script_path = run_dir / "evidence/k6.js"
    if access["mode"] == "endpoint":
        base_url = str(access["url"]).rstrip("/")
        port_forward = None
    else:
        service = str(access["service"])
        service_port = int(access["servicePort"])
        local_port = int(access.get("localPort", 18080))
        base_url = f"http://127.0.0.1:{local_port}"
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
        time.sleep(2)
    script_path.write_text(_k6_script_for_journeys(journeys, base_url=base_url), encoding="utf-8")
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
        "journeys": [_journey_name(journey) for journey in journeys],
    }


def _traffic_journeys(traffic: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        _mapping(item, f"traffic.journeys[{index}]")
        for index, item in enumerate(_list(traffic["journeys"], "traffic.journeys"))
    )


def _k6_script_for_journeys(journeys: tuple[dict[str, Any], ...], *, base_url: str) -> str:
    journey_payloads = []
    scenario_options: dict[str, Any] = {}
    start_after_seconds = 0
    for index, journey in enumerate(journeys, start=1):
        name = _journey_name(journey)
        function_name = _k6_function_name(name, index=index)
        stages = _journey_stages(journey)
        scenario: dict[str, Any]
        if stages:
            scenario = {
                "executor": "ramping-vus",
                "exec": function_name,
                "stages": stages,
            }
            duration_seconds = sum(_duration_seconds(str(stage["duration"])) for stage in stages)
        else:
            scenario = {
                "executor": "shared-iterations",
                "exec": function_name,
                "vus": int(journey.get("vus", 1)),
                "iterations": int(journey.get("iterations", 1)),
            }
            duration_seconds = int(journey.get("durationSeconds", 1))
        if start_after_seconds:
            scenario["startTime"] = f"{start_after_seconds}s"
        scenario_options[function_name] = scenario
        start_after_seconds += max(duration_seconds, 1)
        journey_payloads.append(
            {
                "key": function_name,
                "name": name,
                "functionName": function_name,
                "method": str(journey.get("method", "GET")).upper(),
                "url": base_url + str(journey.get("path", "/health")),
                "expectedStatus": int(journey.get("expectedStatus", 200)),
                "body": journey.get("body"),
                "textBytes": int(journey.get("textBytes", 0)),
            }
        )
    functions = []
    for payload in journey_payloads:
        function_name = str(payload["functionName"])
        key = str(payload["key"])
        functions.append(f"export function {function_name}() {{ runJourney({key!r}); }}")
    options_json = json.dumps({"scenarios": scenario_options}, sort_keys=True)
    journeys_json = json.dumps(
        {item["key"]: item for item in journey_payloads},
        sort_keys=True,
    )
    return "\n".join(
        (
            "import http from 'k6/http';",
            "import { check } from 'k6';",
            f"export const options = {options_json};",
            f"const JOURNEYS = {journeys_json};",
            "function requestBody(journey) {",
            "  if (!journey.body) { return null; }",
            "  const body = JSON.parse(JSON.stringify(journey.body));",
            "  if (body.task_id) {",
            "    body.task_id = `${body.task_id}-${__VU}-${__ITER}-${Date.now()}`;",
            "  }",
            "  if (journey.textBytes && body.text) {",
            "    const repeats = Math.ceil(journey.textBytes / body.text.length);",
            "    body.text = body.text.repeat(repeats).slice(0, journey.textBytes);",
            "  }",
            "  return JSON.stringify(body);",
            "}",
            "function runJourney(key) {",
            "  const journey = JOURNEYS[key];",
            "  const name = journey.name;",
            "  const body = requestBody(journey);",
            "  const params = {",
            "    headers: { 'Content-Type': 'application/json' },",
            "    tags: { journey: name },",
            "  };",
            "  const res = body === null",
            "    ? http.request(journey.method, journey.url, null, params)",
            "    : http.request(journey.method, journey.url, body, params);",
            "  check(res, {",
            "    [`${name} status is expected`]: r => r.status === journey.expectedStatus,",
            "  });",
            "}",
            *functions,
            "",
        )
    )


def _journey_name(journey: dict[str, Any]) -> str:
    return str(journey.get("name") or journey.get("path") or "traffic")


def _k6_function_name(value: str, *, index: int) -> str:
    candidate = re.sub(r"[^0-9A-Za-z_]", "_", value)
    candidate = re.sub(r"_+", "_", candidate).strip("_") or "journey"
    if candidate[0].isdigit():
        candidate = f"journey_{candidate}"
    return f"{candidate}_{index}"


def _journey_stages(journey: dict[str, Any]) -> list[dict[str, Any]]:
    raw_stages = journey.get("stages")
    if raw_stages is None:
        return []
    return [
        {
            "duration": str(_mapping(stage, "traffic.journeys[].stages[]")["duration"]),
            "target": int(_mapping(stage, "traffic.journeys[].stages[]")["targetVus"]),
        }
        for stage in _list(raw_stages, "traffic.journeys[].stages")
    ]


def _duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smh])", value.strip())
    if not match:
        return 1
    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600}[match.group(2)]
    return amount * multiplier


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
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "top",
            "pods",
            "--containers",
        ),
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


def _collect_attach_kubernetes_evidence(
    discovery: dict[str, Any],
    *,
    context: str,
    namespace: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> None:
    pod_names = [str(item["name"]) for item in discovery.get("pods", []) if item.get("name")]
    for pod_name in pod_names:
        _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "pod",
                pod_name,
                "-o",
                "json",
            ),
            commands,
        )
        _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "get",
                "events",
                "--field-selector",
                f"involvedObject.name={pod_name}",
                "-o",
                "json",
            ),
            commands,
        )
        _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "logs",
                f"pod/{pod_name}",
                "--all-containers=true",
                "--tail=200",
            ),
            commands,
        )
    if pod_names:
        _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "top",
                "pods",
                *pod_names,
                "--containers",
            ),
            commands,
        )


def _run_attach_faults(
    faults: tuple[dict[str, Any], ...],
    *,
    discovery: dict[str, Any],
    context: str,
    namespace: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> dict[str, object]:
    if not _attach_faults_allowed(discovery):
        raise WorkflowError(
            "attach faults require chamber.ampule.dev/allow-faults=true on the namespace "
            "or selected workload"
        )
    evidence: dict[str, object] = {
        "faults_requested": True,
        "verified": True,
        "pending_restore": [],
        "actions": [],
    }
    for fault in faults:
        fault_type = str(fault["type"])
        if fault_type == "pod_kill":
            action = _run_attach_pod_kill(
                fault,
                discovery=discovery,
                context=context,
                namespace=namespace,
                runner=runner,
                commands=commands,
            )
        elif fault_type == "deployment_scale":
            action = _run_attach_deployment_scale(
                fault,
                discovery=discovery,
                context=context,
                namespace=namespace,
                runner=runner,
                commands=commands,
            )
            pending = evidence["pending_restore"]
            if isinstance(pending, list):
                pending.append(action)
        else:  # pragma: no cover - validate_config rejects this first
            raise WorkflowError(f"unsupported attach fault type {fault_type!r}")
        actions = evidence["actions"]
        if isinstance(actions, list):
            actions.append(action)
    return evidence


def _restore_attach_faults(
    evidence: dict[str, object],
    *,
    context: str,
    namespace: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> dict[str, object]:
    verified = True
    pending = evidence.get("pending_restore")
    if not isinstance(pending, list):
        evidence["verified"] = False
        return evidence
    for action in pending:
        if not isinstance(action, dict) or action.get("type") != "deployment_scale":
            verified = False
            continue
        action_payload = cast(dict[str, Any], action)
        deployment = str(action_payload.get("deployment", ""))
        replicas_value = action_payload.get("original_replicas", 1)
        replicas = replicas_value if isinstance(replicas_value, int) else int(str(replicas_value))
        completed = _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "scale",
                f"deployment/{deployment}",
                f"--replicas={replicas}",
            ),
            commands,
        )
        if completed.returncode != 0:
            verified = False
            action_payload["manual_remediation"] = (
                f"kubectl --context {context} -n {namespace} scale "
                f"deployment/{deployment} --replicas={replicas}"
            )
            continue
        status = _run_kubernetes_recorded(
            runner,
            (
                "kubectl",
                "--context",
                context,
                "-n",
                namespace,
                "rollout",
                "status",
                f"deployment/{deployment}",
                "--timeout=180s",
            ),
            commands,
        )
        if status.returncode != 0:
            verified = False
        action_payload["restored"] = status.returncode == 0
    evidence["verified"] = verified
    evidence["pending_restore"] = []
    return evidence


def _run_attach_pod_kill(
    fault: dict[str, Any],
    *,
    discovery: dict[str, Any],
    context: str,
    namespace: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> dict[str, object]:
    pods = [item for item in discovery.get("pods", []) if isinstance(item, dict)]
    if not pods:
        raise WorkflowError("attach pod_kill fault requires at least one discovered pod")
    pod_by_name = {str(item["name"]): item for item in pods if item.get("name")}
    requested_pod = fault.get("pod")
    if requested_pod:
        pod_name = str(requested_pod)
        selected_pod = pod_by_name.get(pod_name)
        if selected_pod is None:
            raise WorkflowError(f"attach pod_kill fault target {pod_name!r} was not discovered")
    else:
        selected_pod = pods[0]
        pod_name = str(selected_pod["name"])
    action: dict[str, object] = {
        "type": "pod_kill",
        "pod": pod_name,
        "restore_snapshot": {"pod": selected_pod},
    }
    completed = _run_kubernetes_recorded(
        runner,
        ("kubectl", "--context", context, "-n", namespace, "delete", "pod", pod_name),
        commands,
    )
    _require_command_success(completed, f"delete pod/{pod_name} for attach fault")
    for workload in discovery.get("workloads", []):
        if isinstance(workload, dict) and workload.get("kind") == "Deployment":
            status = _run_kubernetes_recorded(
                runner,
                (
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    f"deployment/{workload['name']}",
                    "--timeout=180s",
                ),
                commands,
            )
            wait = _run_kubernetes_recorded(
                runner,
                (
                    "kubectl",
                    "--context",
                    context,
                    "-n",
                    namespace,
                    "wait",
                    "--for=condition=available",
                    f"deployment/{workload['name']}",
                    "--timeout=180s",
                ),
                commands,
            )
            action["restored"] = status.returncode == 0 and wait.returncode == 0
            break
    if not action.get("restored"):
        raise WorkflowError("pod_kill rollback could not verify deployment availability")
    return action


def _run_attach_deployment_scale(
    fault: dict[str, Any],
    *,
    discovery: dict[str, Any],
    context: str,
    namespace: str,
    runner: KubernetesCommandRunner,
    commands: list[dict[str, object]],
) -> dict[str, object]:
    deployments = [
        item
        for item in discovery.get("workloads", [])
        if isinstance(item, dict) and item.get("kind") == "Deployment"
    ]
    if not deployments:
        raise WorkflowError("attach deployment_scale fault requires a discovered Deployment")
    deployment_by_name = {str(item["name"]): item for item in deployments if item.get("name")}
    requested_workload = fault.get("workload")
    if requested_workload:
        deployment = str(requested_workload)
        selected = deployment_by_name.get(deployment)
        if selected is None:
            raise WorkflowError(
                f"attach deployment_scale fault target {deployment!r} was not discovered"
            )
    else:
        selected = deployments[0]
        deployment = str(selected["name"])
    replicas_value = selected.get("replicas")
    original = 1 if replicas_value is None else int(replicas_value)
    replicas = int(fault["replicas"])
    action: dict[str, object] = {
        "type": "deployment_scale",
        "deployment": deployment,
        "original_replicas": original,
        "fault_replicas": replicas,
        "restored": False,
    }
    completed = _run_kubernetes_recorded(
        runner,
        (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "scale",
            f"deployment/{deployment}",
            f"--replicas={replicas}",
        ),
        commands,
    )
    _require_command_success(completed, f"scale deployment/{deployment} for attach fault")
    return action


def _attach_faults_allowed(discovery: dict[str, Any]) -> bool:
    namespace = discovery.get("namespace", {})
    if isinstance(namespace, dict) and _metadata_allows_faults(namespace):
        return True
    for workload in discovery.get("workloads", []):
        if isinstance(workload, dict) and _metadata_allows_faults(workload):
            return True
    return False


def _metadata_allows_faults(metadata: dict[str, Any]) -> bool:
    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})
    return _allows_faults(labels) or _allows_faults(annotations)


def _allows_faults(values: Any) -> bool:
    return (
        isinstance(values, dict) and str(values.get(ATTACH_ALLOW_FAULTS_KEY, "")).lower() == "true"
    )


def _collect_prometheus_memory_evidence(
    run_dir: Path,
    *,
    prometheus_url: str,
    namespace: str,
    pod_names: tuple[str, ...] = (),
) -> None:
    pod_filter = ""
    if pod_names:
        escaped = "|".join(re.escape(name) for name in pod_names)
        pod_filter = f',pod=~"{escaped}"'
    evidence = {
        "prometheus_url": prometheus_url,
        "namespace": namespace,
        "pod_names": list(pod_names),
        "queries": {
            "container_memory_working_set_bytes": _prometheus_query(
                prometheus_url,
                (
                    "container_memory_working_set_bytes{"
                    f'namespace="{namespace}",container!="",pod!=""{pod_filter}'
                    "}"
                ),
            ),
            "container_cpu_usage_seconds_total": _prometheus_query(
                prometheus_url,
                (
                    "container_cpu_usage_seconds_total{"
                    f'namespace="{namespace}",container!="",pod!=""{pod_filter}'
                    "}"
                ),
            ),
            "kube_pod_container_status_restarts_total": _prometheus_query(
                prometheus_url,
                (
                    "kube_pod_container_status_restarts_total{"
                    f'namespace="{namespace}"{pod_filter}'
                    "}"
                ),
            ),
        },
    }
    _write_json(run_dir / "evidence/prometheus-memory.json", evidence)


def _prometheus_query(prometheus_url: str, query: str) -> dict[str, Any]:
    try:
        url = _prometheus_query_url(prometheus_url, query)
        payload = _read_prometheus_payload(url)
    except Exception as exc:
        return {"ok": False, "query": query, "error": str(exc), "series": []}
    data = payload.get("data") if isinstance(payload, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list):
        result = []
    return {
        "ok": payload.get("status") == "success" if isinstance(payload, dict) else False,
        "query": query,
        "series_count": len(result),
        "series": result[:20],
    }


def _prometheus_query_url(prometheus_url: str, query: str) -> str:
    parsed = urlparse(prometheus_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Prometheus URL must be an HTTP(S) URL")
    path = f"{parsed.path.rstrip('/')}/api/v1/query?{urlencode({'query': query})}"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _read_prometheus_payload(url: str) -> dict[str, Any]:
    # The URL is built by _prometheus_query_url, which rejects non-HTTP(S) schemes.
    # fmt: off
    with urlopen(url, timeout=10) as response:  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # noqa: E501
        # fmt: on
        return json.loads(response.read().decode("utf-8"))


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
        finding_ids=_finding_ids(run_dir),
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


def _finding_ids(run_dir: Path) -> tuple[str, ...]:
    path = run_dir / "findings.json"
    if not path.exists():
        return ()
    payload = read_json_value(path)
    if not isinstance(payload, list):
        return ()
    return tuple(
        str(item["finding_id"])
        for item in payload
        if isinstance(item, dict) and item.get("finding_id")
    )


def _kubernetes_assess_evidence_ids(
    traffic_result: dict[str, object],
    *,
    run_dir: Path | None = None,
) -> tuple[str, ...]:
    evidence_ids = ["plan", "preflight", "kubernetes-commands"]
    if run_dir is not None and (run_dir / "evidence/attach-discovery.json").exists():
        evidence_ids.append("attach-discovery")
    if run_dir is not None and (run_dir / "evidence/pre-test-state.json").exists():
        evidence_ids.append("pre-test-state")
    if run_dir is not None and (run_dir / "evidence/rollback.json").exists():
        evidence_ids.append("rollback")
    if traffic_result.get("summary_path"):
        evidence_ids.append("k6-summary")
    if run_dir is not None and (run_dir / "evidence/prometheus-memory.json").exists():
        evidence_ids.append("prometheus-memory")
    return tuple(evidence_ids)


def _agent_missing_signals(*, stage: str, evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    if stage == "plan":
        return ("live Kubernetes execution",)
    if stage == "assess" and not (
        "kubernetes-commands" in evidence_ids or "k6-summary" in evidence_ids
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
        if metadata.get("runtime_mode"):
            summaries.append(f"runtime mode: {metadata['runtime_mode']}")
        rollback = metadata.get("rollback")
        if isinstance(rollback, dict) and rollback.get("faults_requested"):
            summaries.append(f"rollback verified: {bool(rollback.get('verified'))}")
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
                    f"k6 http requests: count={k6_summary.get('http_request_count', 'unknown')}"
                )
                summaries.append(
                    "k6 http request failure rate: "
                    f"{k6_summary.get('http_failure_rate', 'unknown')}"
                )
                summaries.append(
                    "k6 derived failed http requests: "
                    f"{k6_summary.get('derived_failed_http_requests', 'unknown')}"
                )
    if "prometheus-memory" in evidence_ids:
        path = run_dir / "evidence/prometheus-memory.json"
        if path.exists():
            queries = _read_json(path).get("queries")
            if isinstance(queries, dict):
                for name, payload in queries.items():
                    if isinstance(payload, dict):
                        summaries.append(
                            f"prometheus {name} series: {payload.get('series_count', 0)}"
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
    if "attach-discovery" in evidence_ids:
        path = run_dir / "evidence/attach-discovery.json"
        if path.exists():
            details.extend(_attach_discovery_agent_details(_read_json(path)))
    if "rollback" in evidence_ids:
        path = run_dir / "evidence/rollback.json"
        if path.exists():
            details.append(_agent_detail("rollback", _read_json(path)))
    if "k6-summary" in evidence_ids:
        path = run_dir / "evidence/k6-summary.json"
        if path.exists():
            details.extend(_k6_agent_details(_read_json(path)))
    if "prometheus-memory" in evidence_ids:
        path = run_dir / "evidence/prometheus-memory.json"
        if path.exists():
            details.extend(_prometheus_memory_agent_details(_read_json(path)))
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
            token in command_text for token in ("rollout", "get endpoints", "get events", "logs")
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


def _attach_discovery_agent_details(payload: dict[str, Any]) -> list[str]:
    workloads = [
        {
            "kind": item.get("kind"),
            "name": item.get("name"),
            "role": item.get("role"),
            "selector": item.get("selector"),
            "replicas": item.get("replicas"),
        }
        for item in payload.get("workloads", [])
        if isinstance(item, dict)
    ]
    services = [
        {
            "name": item.get("name"),
            "port": item.get("port"),
            "selector": item.get("selector"),
        }
        for item in payload.get("services", [])
        if isinstance(item, dict)
    ]
    pods = [
        {"name": item.get("name"), "phase": item.get("phase")}
        for item in payload.get("pods", [])
        if isinstance(item, dict)
    ]
    return [
        _agent_detail(
            "attach-discovery",
            {
                "namespace": payload.get("namespace", {}).get("name")
                if isinstance(payload.get("namespace"), dict)
                else None,
                "workloads": workloads,
                "services": services,
                "pods": pods,
            },
        )
    ]


def _k6_agent_details(payload: dict[str, Any]) -> list[str]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return []
    return [_agent_detail("k6-summary", {"metrics": _k6_summary_metrics(metrics)})]


def _prometheus_memory_agent_details(payload: dict[str, Any]) -> list[str]:
    queries = payload.get("queries")
    if not isinstance(queries, dict):
        return []
    compact: dict[str, Any] = {}
    for name, query_payload in queries.items():
        if not isinstance(query_payload, dict):
            continue
        compact[name] = {
            "ok": query_payload.get("ok"),
            "series_count": query_payload.get("series_count", 0),
            "series": query_payload.get("series", [])[:5],
        }
    return [_agent_detail("prometheus-memory", compact)]


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
    journeys = tuple(
        _mapping(item, f"traffic.journeys[{index}]")
        for index, item in enumerate(_list(traffic["journeys"], "traffic.journeys"))
    )
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
    evidence.extend(_registered_evidence_references(run_dir, metadata=metadata, plan=plan))
    findings = _persisted_report_findings(run_dir)
    result = _read_json(run_dir / "result.json") if (run_dir / "result.json").exists() else {}
    missing_evidence = result.get("missing_evidence_ids")
    result_limitations = (
        tuple(f"Missing required evidence: {item}" for item in missing_evidence)
        if isinstance(missing_evidence, list)
        else ()
    )
    journey_names = ", ".join(
        str(item.get("name", f"journey-{index}")) for index, item in enumerate(journeys, start=1)
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
            namespace=str(metadata.get("namespace", plan.get("namespace", ""))),
            provider=str(
                metadata.get(
                    "provider", _mapping(plan.get("runtime", {}), "runtime").get("provider", "kind")
                )
            ),
            lifecycle_state=_lifecycle_state(metadata),
        ),
        scenario=TestedScenario(
            scenario_id=str(config.get("scenarioId", f"{service['name']}-assessment")),
            name=journey_names or "guided assessment",
            path=str(run_dir / "chamber.yaml"),
            traffic_tool=str(journeys[0].get("tool", "k6")),
            max_virtual_users=_max_virtual_users(journeys),
            fault_summary=_fault_summary(config, metadata),
        ),
        findings=findings,
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
            tuple(plan.get("limitations") or ())
            + result_limitations
            + (
                ("Local guided workflow may not include live Kubernetes telemetry.",)
                if not plan.get("limitations") and not result_limitations
                else ()
            )
        ),
        onboarding_summary=tuple(str(item) for item in config.get("assumptions", ())),
        adapted_workloads=tuple(_workload_line(item) for item in plan.get("workloads", [])),
        redacted_config=tuple(_config_line(item) for item in plan.get("redacted_config", [])),
        external_dependencies=tuple(
            _external_line(item) for item in plan.get("external_dependencies", [])
        ),
        agent_sections=agent_sections,
        assessment_status=str(result["status"]) if result.get("status") else None,
        readiness_score=(
            int(result["readiness_score"])
            if isinstance(result.get("readiness_score"), int)
            else None
        ),
        conclusive=result.get("conclusive") if isinstance(result.get("conclusive"), bool) else None,
        evidence_coverage_percent=(
            int(result["evidence_coverage_percent"])
            if isinstance(result.get("evidence_coverage_percent"), int)
            else None
        ),
        execution_coverage_percent=(
            int(result["execution_coverage_percent"])
            if isinstance(result.get("execution_coverage_percent"), int)
            else None
        ),
        rollback_verified=(
            result.get("rollback_verified")
            if isinstance(result.get("rollback_verified"), bool)
            else None
        ),
        cleanup_verified=(
            result.get("cleanup_verified")
            if isinstance(result.get("cleanup_verified"), bool)
            else None
        ),
    )


def _registered_evidence_references(
    run_dir: Path,
    *,
    metadata: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[EvidenceReference, ...]:
    resource = str(metadata.get("namespace", plan.get("namespace", run_dir.name)))
    references = []
    for item in registered_evidence(run_dir):
        relative = str(item.get("relative_path", ""))
        references.append(
            EvidenceReference(
                evidence_id=str(item.get("evidence_id", "unknown")),
                source=str(item.get("source", "ampule-chamber")),
                signal_type=str(item.get("signal_type", "unknown")),
                resource=resource,
                collected_at=str(item.get("collected_at", "from-file")),
                artifact_path=str(run_dir / relative),
            )
        )
    return tuple(references)


def _persisted_report_findings(run_dir: Path) -> tuple[ReportFinding, ...]:
    path = run_dir / "findings.json"
    if not path.exists():
        return ()
    payload = read_json_value(path)
    if not isinstance(payload, list):
        raise WorkflowError(f"{path} must contain a JSON list")
    findings = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        findings.append(
            ReportFinding(
                finding_id=str(item.get("finding_id", "unknown")),
                signal_type=str(item.get("signal_type", "unknown")),
                affected_resource=str(item.get("affected_resource", "unknown")),
                observed_facts=tuple(str(value) for value in item.get("observed_facts", ())),
                suspected_cause=str(item.get("suspected_cause", "Unknown cause.")),
                severity=str(item.get("severity", "low")),
                confidence=str(item.get("confidence", "low")),
                evidence_ids=tuple(str(value) for value in item.get("evidence_ids", ())),
                related_timeline_ids=tuple(
                    str(value) for value in item.get("related_timeline_ids", ())
                ),
                recommendations=tuple(str(value) for value in item.get("recommendations", ())),
            )
        )
    return tuple(findings)


def _max_virtual_users(journeys: tuple[dict[str, Any], ...]) -> int:
    values = [1]
    for journey in journeys:
        vus = journey.get("vus")
        if isinstance(vus, int):
            values.append(vus)
        stages = journey.get("stages")
        if isinstance(stages, list):
            values.extend(
                int(stage.get("targetVus", 0))
                for stage in stages
                if isinstance(stage, dict) and isinstance(stage.get("targetVus"), int)
            )
    return max(values)


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


def _lifecycle_state(metadata: dict[str, Any]) -> str:
    state = str(metadata.get("stage", "planned"))
    runtime_mode = metadata.get("runtime_mode")
    if runtime_mode:
        return f"{state} (mode: {runtime_mode})"
    return state


def _fault_summary(config: dict[str, Any], metadata: dict[str, Any]) -> str:
    runtime = _mapping(config.get("runtime", {}), "runtime")
    faults = _list(runtime.get("faults", []), "runtime.faults")
    if not faults:
        return "none"
    rollback = metadata.get("rollback")
    verified = rollback.get("verified") if isinstance(rollback, dict) else None
    fault_names = ", ".join(str(_mapping(item, "runtime.faults[]").get("type")) for item in faults)
    return f"{fault_names}; rollback verified: {bool(verified)}"


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
    return new_run_directory(Path(WORKSPACE_DIR) / RUNS_DIR, name)


def _ensure_run_subdirs(run_dir: Path) -> None:
    for child in ("adapted-manifests", "evidence", "agent"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    initialize_run_record(run_dir)


def _write_metadata(run_dir: Path, payload: dict[str, Any]) -> None:
    if "service_name" not in payload and (run_dir / "chamber.yaml").exists():
        try:
            config = yaml.safe_load((run_dir / "chamber.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            config = None
        if isinstance(config, dict) and isinstance(config.get("service"), dict):
            payload = {**payload, "service_name": str(config["service"].get("name", "unknown"))}
    _write_json(run_dir / "run-metadata.json", payload)
    sync_run_record(run_dir, payload)


def _write_json(path: Path, payload: Any) -> None:
    write_json_atomic(path, _jsonable(payload))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = read_json_value(path)
    except OSError as exc:
        raise WorkflowError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{path} must contain a JSON object")
    return payload


def _finalize_guided_result(
    run_dir: Path,
    *,
    config: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    refresh_evidence_manifest(run_dir)
    findings = analyze_guided_run(run_dir, config=config, metadata=metadata)
    _write_json(run_dir / "findings.json", findings)
    result = build_assessment_result(
        run_dir,
        config=config,
        metadata=metadata,
        findings=findings,
    )
    _write_json(run_dir / "result.json", result)
    run_record = initialize_run_record(run_dir)
    run_record["result_status"] = result["status"]
    run_record["updated_at"] = datetime.now(UTC).isoformat()
    write_json_atomic(run_dir / "run.json", run_record)
    append_run_event(
        run_dir,
        state=str(run_record.get("state", metadata.get("stage", "analyzing"))),
        event_type="analysis_completed",
        payload={
            "result_status": result["status"],
            "finding_count": result["finding_count"],
            "evidence_coverage_percent": result["evidence_coverage_percent"],
        },
    )
    return result


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
    runtime_mode = str(runtime.get("mode", "deploy"))
    if runtime_mode not in RUNTIME_MODES:
        raise WorkflowError(f"{source}.mode must be one of: {', '.join(sorted(RUNTIME_MODES))}")
    for key, value in runtime.items():
        if key in {
            "provider",
            "mode",
            "kubernetesContext",
            "namespace",
            "namespaceBase",
            "cleanup",
            "prometheusUrl",
            "trafficAccess",
            "faults",
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
    if runtime_mode == "attach":
        _non_empty(runtime.get("namespace"), f"{source}.namespace")
        if bool(runtime.get("cleanup", False)):
            raise WorkflowError(f"{source}.cleanup must be false in attach mode")
        _validate_attach_faults(_list(runtime.get("faults", []), f"{source}.faults"), source=source)
    if "trafficAccess" not in runtime:
        raise WorkflowError(f"{source}.trafficAccess is required for Kubernetes runtime")
    _validate_traffic_access(
        _mapping(runtime.get("trafficAccess"), f"{source}.trafficAccess"),
        source=f"{source}.trafficAccess",
    )
    prometheus_url = runtime.get("prometheusUrl")
    if prometheus_url is not None:
        _require_http_url(str(prometheus_url), f"{source}.prometheusUrl")


def _validate_attach_faults(faults: list[Any], *, source: str) -> None:
    for index, item in enumerate(faults):
        path = f"{source}.faults[{index}]"
        fault = _mapping(item, path)
        fault_type = str(fault.get("type", ""))
        if fault_type not in ATTACH_FAULT_TYPES:
            raise WorkflowError(
                f"{path}.type must be one of: {', '.join(sorted(ATTACH_FAULT_TYPES))}"
            )
        if fault_type == "deployment_scale":
            replicas = fault.get("replicas")
            if not isinstance(replicas, int) or replicas < 0:
                raise WorkflowError(f"{path}.replicas must be a non-negative integer")


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
    runtime_mode = str(runtime.get("mode", "deploy"))
    namespace_base = str(runtime.get("namespaceBase", config.get("namespaceBase", "")))
    result: dict[str, Any] = {
        "provider": provider,
        "mode": runtime_mode,
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


def _attach_runtime_plan(config: dict[str, Any], *, namespace: str) -> dict[str, Any]:
    runtime = _mapping(config.get("runtime", {}), "runtime")
    return {
        "provider": "kubernetes",
        "mode": "attach",
        "namespace": namespace,
        "namespace_base": None,
        "cleanup": False,
        "traffic_access": _jsonable(runtime.get("trafficAccess", {})),
        "faults": _jsonable(runtime.get("faults", [])),
        "external_resources": True,
        "ownership": "externally_deployed",
    }


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
            f"{source} contains unknown agent role(s): {', '.join(unknown)}; allowed: {allowed}"
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
        run_dir / "run.json",
        run_dir / "events.jsonl",
        run_dir / "chamber.yaml",
        run_dir / "plan.json",
        run_dir / "run-metadata.json",
        run_dir / "findings.json",
        run_dir / "result.json",
        run_dir / "evidence/manifest.json",
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
