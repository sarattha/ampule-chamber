"""Live local Kubernetes orchestration for phase 06 chamber runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import yaml

from chamber.analysis import analyze_evidence
from chamber.analysis.findings import Finding
from chamber.chaos import FaultPlan, plan_faults
from chamber.contracts.scenario import Scenario, load_scenario
from chamber.environment import EnvironmentMetadata, EnvironmentPlan, plan_environment
from chamber.load import TrafficExecutionResult, TrafficPlan, plan_traffic
from chamber.observability import (
    EvidenceArtifact,
    collect_kubernetes_evidence,
    collect_prometheus_evidence,
)
from chamber.orchestrator.timeline import ExperimentTimeline, build_experiment_timeline
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

DEFAULT_CONTEXT = "kind-ampule-chamber"
DEFAULT_IMAGE = "ampule/sample-service:local"
PHASE06_ARTIFACT_DIR = Path("docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts")
PROMETHEUS_URL_ENV = "PROMETHEUS_URL"
PRODUCTION_CONTEXT_FRAGMENTS = (
    "prod",
    "production",
    "aks-prod",
    "prd",
    "live",
)


class LiveRunError(RuntimeError):
    """Raised when a live phase 06 chamber run cannot continue safely."""


class CommandRunner(Protocol):
    """Subprocess boundary used by the live runner."""

    def run(
        self, command: tuple[str, ...], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and return the completed process."""

    def popen(
        self,
        command: tuple[str, ...],
        *,
        stdout: Any | None = subprocess.PIPE,
        stderr: Any | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        """Start a long-running process."""


@dataclass(frozen=True)
class SubprocessCommandRunner:
    """Default command runner backed by subprocess."""

    def run(  # pragma: no cover
        self, command: tuple[str, ...], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            input=input_text,
            capture_output=True,
            text=True,
        )

    def popen(  # pragma: no cover
        self,
        command: tuple[str, ...],
        *,
        stdout: Any | None = subprocess.PIPE,
        stderr: Any | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )


@dataclass(frozen=True)
class LiveRunOptions:
    """Options for one or more phase 06 live chamber runs."""

    scenarios: tuple[Path, ...]
    output: Path | None
    output_dir: Path | None
    artifact_dir: Path
    context: str = DEFAULT_CONTEXT
    image: str = DEFAULT_IMAGE
    retain: bool = False
    retain_on_failure: bool = False
    prometheus_url: str | None = None


@dataclass(frozen=True)
class CommandRecord:
    """Command result recorded for run evidence."""

    command: tuple[str, ...]
    exit_status: int | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class LiveScenarioResult:
    """Result of one scenario live run."""

    run_id: str
    scenario_id: str
    report_path: str
    metadata_path: str
    cleanup_performed: bool
    success: bool


def run_cli(argv: list[str] | None = None) -> int:  # pragma: no cover
    """CLI entrypoint for `ampule-chamber run`."""

    parser = argparse.ArgumentParser(description="Run live Ampule Chamber scenarios.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", description="Run live local kind scenarios.")
    scenario_group = run_parser.add_mutually_exclusive_group(required=True)
    scenario_group.add_argument("--scenario", help="Path to one scenario YAML file.")
    scenario_group.add_argument(
        "--all-scenarios",
        action="store_true",
        help="Run all scenario YAML files under scenarios/.",
    )
    run_parser.add_argument("--output", help="Path for a single scenario report.")
    run_parser.add_argument("--output-dir", help="Directory for scenario reports and artifacts.")
    run_parser.add_argument("--context", default=DEFAULT_CONTEXT)
    run_parser.add_argument("--image", default=DEFAULT_IMAGE)
    run_parser.add_argument("--retain", action="store_true")
    run_parser.add_argument("--retain-on-failure", action="store_true")
    run_parser.add_argument(
        "--prometheus-url", help="Prometheus base URL. Defaults to PROMETHEUS_URL."
    )

    args = parser.parse_args(argv)
    if args.command != "run":
        raise LiveRunError(f"unsupported command {args.command!r}")

    if args.all_scenarios:
        scenarios = tuple(sorted(Path("scenarios").glob("*.yaml")))
    else:
        scenarios = (Path(args.scenario),)
    options = LiveRunOptions(
        scenarios=scenarios,
        output=Path(args.output) if args.output else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        artifact_dir=(
            Path(args.output_dir)
            if args.output_dir
            else Path(args.output).parent
            if args.output
            else PHASE06_ARTIFACT_DIR
        ),
        context=args.context,
        image=args.image,
        retain=args.retain,
        retain_on_failure=args.retain_on_failure,
        prometheus_url=args.prometheus_url,
    )
    try:
        results = run_live_scenarios(options)
    except LiveRunError as exc:
        parser.exit(1, f"error: {exc}\n")
    for result in results:
        print(f"{result.scenario_id}: report {result.report_path}")
    return 0


def run_live_scenarios(
    options: LiveRunOptions,
    *,
    runner: CommandRunner | None = None,
) -> tuple[LiveScenarioResult, ...]:  # pragma: no cover
    """Run all requested scenarios after global safety checks pass."""

    command_runner = runner or SubprocessCommandRunner()
    if not options.scenarios:
        raise LiveRunError("at least one scenario path is required")
    if options.output is not None and len(options.scenarios) != 1:
        raise LiveRunError("--output can only be used with a single --scenario run")

    _require_tools(("kubectl", "kind", "k6", "docker"))
    _validate_context(options.context)
    _require_current_context(options.context, command_runner)
    prometheus_url = _require_prometheus(options.prometheus_url)
    _verify_kind_image(options.context, options.image, command_runner)
    scenarios = tuple((path, load_scenario(path)) for path in options.scenarios)
    for _, scenario in scenarios:
        _validate_live_fault_support(scenario)

    results = []
    for scenario_path, scenario in scenarios:
        result = _run_one_scenario(
            scenario,
            scenario_path=scenario_path,
            options=options,
            prometheus_url=prometheus_url,
            runner=command_runner,
        )
        results.append(result)
    return tuple(results)


def should_cleanup(*, retain: bool, retain_on_failure: bool, success: bool) -> bool:
    """Return whether chamber resources should be cleaned after a scenario."""

    if retain:
        return False
    if retain_on_failure and not success:
        return False
    return True


def _run_one_scenario(
    scenario: Scenario,
    *,
    scenario_path: Path,
    options: LiveRunOptions,
    prometheus_url: str,
    runner: CommandRunner,
) -> LiveScenarioResult:  # pragma: no cover
    started = time.monotonic()
    run_id = _run_id(scenario.scenario_id)
    scenario_artifact_dir = options.artifact_dir / run_id
    scenario_artifact_dir.mkdir(parents=True, exist_ok=True)
    report_path = _report_path(options, scenario, run_id)
    metadata_path = scenario_artifact_dir / "run-metadata.json"
    commands: list[CommandRecord] = []
    limitations = _scenario_limitations(scenario)
    success = False
    cleanup_performed = False

    environment_plan = plan_environment(scenario, run_id=run_id)
    traffic_plan = plan_traffic(
        scenario,
        environment=environment_plan.metadata,
        artifact_dir=scenario_artifact_dir,
    )
    fault_plan = plan_faults(
        scenario,
        environment=environment_plan.metadata,
        artifact_dir=scenario_artifact_dir,
    )
    timeline = build_experiment_timeline(
        scenario,
        traffic_plan=traffic_plan,
        fault_plan=fault_plan,
    )

    evidence: tuple[EvidenceArtifact, ...] = ()
    findings: tuple[Finding, ...] = ()
    traffic_result = TrafficExecutionResult(
        success=False,
        command=traffic_plan.command,
        exit_status=None,
        stdout="",
        stderr="",
        error="traffic did not run",
    )
    try:
        _apply_environment(environment_plan, runner=runner, commands=commands)
        _wait_for_readiness(environment_plan, runner=runner, commands=commands)
        traffic_result = _execute_traffic_with_faults(
            traffic_plan,
            fault_plan=fault_plan,
            runner=runner,
            commands=commands,
        )
        evidence = (
            *collect_kubernetes_evidence(environment_plan.metadata),
            *collect_prometheus_evidence(
                environment_plan.metadata,
                prometheus_url=prometheus_url,
            ),
        )
        _write_evidence(scenario_artifact_dir / "evidence.json", evidence)
        analysis = analyze_evidence(
            evidence,
            scenario=scenario,
            timeline=timeline,
            k6_summary_path=traffic_plan.summary_path,
        )
        findings = analysis.findings
        success = traffic_result.success
    finally:
        cleanup = should_cleanup(
            retain=options.retain,
            retain_on_failure=options.retain_on_failure,
            success=success,
        )
        if cleanup:
            cleanup_performed = True
            _cleanup_environment(environment_plan, runner=runner, commands=commands)

    report = _report_input(
        scenario=scenario,
        scenario_path=scenario_path,
        environment=environment_plan.metadata,
        traffic_plan=traffic_plan,
        traffic_result=traffic_result,
        timeline=timeline,
        evidence=evidence,
        findings=findings,
        limitations=limitations,
        artifact_dir=scenario_artifact_dir,
        report_path=report_path,
        duration_seconds=max(1, int(time.monotonic() - started)),
        cleanup_performed=cleanup_performed,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(report), encoding="utf-8")
    _write_metadata(
        metadata_path,
        scenario=scenario,
        run_id=run_id,
        context=options.context,
        image=options.image,
        environment=environment_plan.metadata,
        traffic_plan=traffic_plan,
        traffic_result=traffic_result,
        timeline=timeline,
        commands=tuple(commands),
        cleanup_performed=cleanup_performed,
        limitations=limitations,
    )
    return LiveScenarioResult(
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        report_path=str(report_path),
        metadata_path=str(metadata_path),
        cleanup_performed=cleanup_performed,
        success=success,
    )


def _validate_context(context: str) -> None:
    lowered = context.lower()
    if any(fragment in lowered for fragment in PRODUCTION_CONTEXT_FRAGMENTS):
        raise LiveRunError(f"refusing unsafe Kubernetes context {context!r}")
    if context != DEFAULT_CONTEXT:
        raise LiveRunError(
            f"phase 06 live runs require Kubernetes context {DEFAULT_CONTEXT!r}; got {context!r}"
        )


def _require_current_context(context: str, runner: CommandRunner) -> None:
    completed = runner.run(("kubectl", "config", "current-context"))
    _recordless_success(completed, "read current Kubernetes context")
    current = completed.stdout.strip()
    if current != context:
        raise LiveRunError(f"current Kubernetes context is {current!r}, expected {context!r}")


def _require_prometheus(prometheus_url: str | None) -> str:
    base_url = (prometheus_url or os.environ.get(PROMETHEUS_URL_ENV) or "").rstrip("/")
    if not base_url:
        raise LiveRunError(f"{PROMETHEUS_URL_ENV} is required for phase 06 live runs")
    url = f"{base_url}/api/v1/query?{urlencode({'query': 'up'})}"
    try:
        with urlopen(url, timeout=5) as response:
            raw = response.read().decode("utf-8")
    except (OSError, URLError) as exc:
        raise LiveRunError(f"Prometheus endpoint {base_url!r} is not reachable: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiveRunError(f"Prometheus endpoint {base_url!r} returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise LiveRunError(f"Prometheus endpoint {base_url!r} did not return success")
    return base_url


def _verify_kind_image(context: str, image: str, runner: CommandRunner) -> None:
    cluster_name = context.removeprefix("kind-")
    nodes = runner.run(("kind", "get", "nodes", "--name", cluster_name))
    _recordless_success(nodes, f"list kind nodes for {cluster_name}")
    node_names = tuple(line.strip() for line in nodes.stdout.splitlines() if line.strip())
    if not node_names:
        raise LiveRunError(f"kind cluster {cluster_name!r} has no nodes")
    for node_name in node_names:
        inspected = runner.run(("docker", "exec", node_name, "crictl", "images", "-q", image))
        if inspected.returncode == 0 and inspected.stdout.strip():
            return
    raise LiveRunError(
        f"image {image!r} is not preloaded into kind cluster {cluster_name!r}; "
        "build it and run `kind load docker-image` before phase 06 live runs"
    )


def _apply_environment(
    environment_plan: EnvironmentPlan,
    *,
    runner: CommandRunner,
    commands: list[CommandRecord],
) -> None:  # pragma: no cover
    for action in environment_plan.actions:
        if action.action_type not in {"provision", "deploy"} or action.manifest is None:
            continue
        manifest = yaml.safe_dump(action.manifest, sort_keys=False)
        completed = _run_recorded(
            runner, ("kubectl", "apply", "-f", "-"), commands, input_text=manifest
        )
        _success(completed, action.description)


def _wait_for_readiness(
    environment_plan: EnvironmentPlan,
    *,
    runner: CommandRunner,
    commands: list[CommandRecord],
) -> None:  # pragma: no cover
    metadata = environment_plan.metadata
    namespace = metadata.namespace
    selector = ",".join(
        f"{key}={value}" for key, value in sorted(metadata.cleanup_selectors.items())
    )
    checks: list[tuple[str, ...]] = []
    for resource in metadata.service_resources:
        checks.extend(
            [
                (
                    "kubectl",
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    f"deployment/{resource.deployment}",
                    "--timeout=180s",
                ),
                ("kubectl", "-n", namespace, "get", "endpoints", resource.service, "-o", "json"),
            ]
        )
    checks.append(
        (
            "kubectl",
            "-n",
            namespace,
            "wait",
            "pod",
            "-l",
            selector,
            "--for=condition=Ready",
            "--timeout=180s",
        )
    )
    for command in checks:
        completed = _run_recorded(runner, command, commands)
        _success(completed, "verify target readiness")


def _execute_traffic_with_faults(
    traffic_plan: TrafficPlan,
    *,
    fault_plan: FaultPlan,
    runner: CommandRunner,
    commands: list[CommandRecord],
) -> TrafficExecutionResult:  # pragma: no cover
    if shutil.which("k6") is None:
        return TrafficExecutionResult(
            success=False,
            command=traffic_plan.command,
            exit_status=None,
            stdout="",
            stderr="",
            error="k6 executable was not found on PATH; install k6 to run live traffic.",
        )
    Path(traffic_plan.script_path).write_text(traffic_plan.script, encoding="utf-8")
    port_forward = runner.popen(traffic_plan.port_forward_command)
    try:
        _wait_for_target(traffic_plan.readiness_url, port_forward)
        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file,
        ):
            process = runner.popen(
                traffic_plan.command,
                stdout=stdout_file,
                stderr=stderr_file,
            )
            try:
                started = time.monotonic()
                deadline = started + traffic_plan.total_duration_seconds + 60
                pending = list(sorted(fault_plan.actions, key=lambda action: action.offset_seconds))
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    while pending and elapsed >= pending[0].offset_seconds:
                        action = pending.pop(0)
                        completed = _run_recorded(runner, action.command, commands)
                        _success(completed, action.description)
                    if time.monotonic() > deadline:
                        _stop_process(process)
                        return TrafficExecutionResult(
                            success=False,
                            command=traffic_plan.command,
                            exit_status=process.returncode,
                            stdout="",
                            stderr="",
                            error=(
                                "k6 exceeded planned traffic duration plus 60 second grace window."
                            ),
                        )
                    time.sleep(0.2)
            except Exception:
                _stop_process(process)
                raise
            process.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
            return TrafficExecutionResult(
                success=process.returncode == 0,
                command=traffic_plan.command,
                exit_status=process.returncode,
                stdout=stdout,
                stderr=stderr,
                error=None if process.returncode == 0 else "k6 exited with a non-zero status.",
            )
    finally:
        _stop_process(port_forward)


def _cleanup_environment(
    environment_plan: EnvironmentPlan,
    *,
    runner: CommandRunner,
    commands: list[CommandRecord],
) -> None:  # pragma: no cover
    namespace = environment_plan.namespace
    selector = ",".join(
        f"{key}={value}" for key, value in sorted(environment_plan.cleanup.selectors.items())
    )
    cleanup_commands = (
        (
            "kubectl",
            "-n",
            namespace,
            "delete",
            "networkpolicy,deployment,service",
            "-l",
            selector,
            "--ignore-not-found=true",
        ),
        ("kubectl", "delete", "namespace", namespace, "--ignore-not-found=true"),
    )
    for command in cleanup_commands:
        completed = _run_recorded(runner, command, commands)
        _success(completed, "cleanup chamber-owned resources")


def _wait_for_target(target_url: str, process: subprocess.Popen[str]) -> None:  # pragma: no cover
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise LiveRunError("kubectl port-forward exited before traffic started: " + stderr)
        try:
            with urlopen(target_url, timeout=0.5):
                return
        except (OSError, URLError):
            time.sleep(0.2)
    raise LiveRunError(f"timed out waiting for port-forward target {target_url}")


def _stop_process(process: subprocess.Popen[str]) -> None:  # pragma: no cover
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_recorded(
    runner: CommandRunner,
    command: tuple[str, ...],
    commands: list[CommandRecord],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:  # pragma: no cover
    completed = runner.run(command, input_text=input_text)
    commands.append(
        CommandRecord(
            command=command,
            exit_status=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    )
    return completed


def _success(completed: subprocess.CompletedProcess[str], description: str) -> None:
    if completed.returncode != 0:
        raise LiveRunError(
            f"failed to {description}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def _recordless_success(completed: subprocess.CompletedProcess[str], description: str) -> None:
    if completed.returncode != 0:
        raise LiveRunError(
            f"failed to {description}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def _require_tools(tools: tuple[str, ...]) -> None:  # pragma: no cover
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise LiveRunError("missing required executable(s): " + ", ".join(missing))


def _validate_live_fault_support(scenario: Scenario) -> None:
    if _dependency_response_faults(scenario) and not scenario.document["environment"].get(
        "dependencies"
    ):
        raise LiveRunError(
            f"{scenario.scenario_id} uses dependency response faults but declares no "
            "controlled dependency workload"
        )


def _report_input(
    *,
    scenario: Scenario,
    scenario_path: Path,
    environment: EnvironmentMetadata,
    traffic_plan: TrafficPlan,
    traffic_result: TrafficExecutionResult,
    timeline: ExperimentTimeline,
    evidence: tuple[EvidenceArtifact, ...],
    findings: tuple[Finding, ...],
    limitations: tuple[str, ...],
    artifact_dir: Path,
    report_path: Path,
    duration_seconds: int,
    cleanup_performed: bool,
) -> ReportInput:
    return ReportInput(
        title="Ampule Chamber Reliability Report",
        service=ServiceMetadata(
            name=str(scenario.document["target"]["service"]["name"]),
            owner=str(scenario.document["metadata"]["owner"]),
            repository=str(Path.cwd()),
            commit=_git_commit(),
        ),
        run=RunMetadata(
            run_id=environment.run_id,
            test_date=datetime.now(UTC).date().isoformat(),
            duration_seconds=duration_seconds,
            namespace=environment.namespace,
            provider=environment.provider,
            lifecycle_state="completed" if traffic_result.success else "failed",
        ),
        scenario=TestedScenario(
            scenario_id=scenario.scenario_id,
            name=str(scenario.document["metadata"]["name"]),
            path=str(scenario_path),
            traffic_tool=str(scenario.document["traffic"]["tool"]),
            max_virtual_users=max(stage.target_vus for stage in traffic_plan.stages),
            fault_summary=_fault_summary(scenario),
        ),
        findings=tuple(_report_finding(finding) for finding in findings),
        evidence=(
            *tuple(_evidence_reference(item, artifact_dir=artifact_dir) for item in evidence),
            EvidenceReference(
                evidence_id=f"{environment.run_id}:k6-summary:{Path(traffic_plan.summary_path).name}",
                source="k6",
                signal_type="traffic_summary",
                resource=traffic_plan.target_url,
                collected_at="from-file",
                artifact_path=traffic_plan.summary_path,
            ),
        ),
        reproduction=ReproductionDetails(
            commands=(
                f"uv run ampule-chamber run --scenario {scenario_path} --output {report_path}",
            ),
            artifacts=(
                str(report_path),
                str(artifact_dir / "run-metadata.json"),
                str(artifact_dir / "evidence.json"),
                traffic_plan.summary_path,
            ),
        ),
        retest_plan=(
            f"Rerun {scenario.scenario_id} after remediation using the same kind context.",
            "Require no critical findings and verify cleanup status before promotion.",
        ),
        cleanup_notes=(
            "Cleanup completed for chamber-owned resources."
            if cleanup_performed
            else "Resources were retained for debugging.",
        ),
        limitations=limitations or ("No phase-specific limitations recorded.",),
        agent_sections=(
            ReportSection(
                heading="Agent Analysis",
                lines=(
                    "No live phase 07 agent analysis was attached to this runner output.",
                    "Agent outputs must cite supplied evidence before report inclusion.",
                ),
            ),
        ),
        dependency_graph=_dependency_graph_lines(environment),
        recovery_status=_recovery_status_lines(
            timeline=timeline,
            traffic_success=traffic_result.success,
            findings=findings,
            cleanup_performed=cleanup_performed,
        ),
    )


def _report_finding(finding: Finding) -> ReportFinding:
    return ReportFinding(
        finding_id=finding.finding_id,
        signal_type=finding.signal_type,
        affected_resource=finding.affected_resource,
        observed_facts=finding.observed_facts,
        suspected_cause=finding.suspected_cause,
        severity=finding.severity,
        confidence=finding.confidence,
        evidence_ids=finding.evidence_ids,
        related_timeline_ids=finding.related_timeline_ids,
        recommendations=_recommendations(finding),
    )


def _dependency_graph_lines(environment: EnvironmentMetadata) -> tuple[str, ...]:
    dependencies = [
        resource for resource in environment.service_resources if resource.role == "dependency"
    ]
    if not dependencies:
        return ()
    target = next(
        resource for resource in environment.service_resources if resource.role == "target"
    )
    return tuple(
        f"{target.source_name} -> {dependency.source_name} "
        f"(service/{dependency.service}:{dependency.port})"
        for dependency in dependencies
    )


def _recovery_status_lines(
    *,
    timeline: ExperimentTimeline,
    traffic_success: bool,
    findings: tuple[Finding, ...],
    cleanup_performed: bool,
) -> tuple[str, ...]:
    recovery_events = [
        event for event in timeline.events if event.event_type == "recovery_validate"
    ]
    status = "recovered" if traffic_success and not findings else "degraded_or_inconclusive"
    cleanup = "cleanup completed" if cleanup_performed else "resources retained"
    return (
        f"Status: {status}.",
        f"Recovery checkpoints: {len(recovery_events)}.",
        f"Cleanup: {cleanup}.",
    )


def _recommendations(finding: Finding) -> tuple[str, ...]:
    if finding.signal_type == "error_rate":
        return (
            "Verify dependency degradation behavior and retry budgets.",
            "Retest after downstream failure handling changes.",
        )
    if finding.signal_type in {"memory_usage", "oom_killed", "restart_loop"}:
        return (
            "Review memory limits and container restart evidence.",
            "Retest with Prometheus memory and Kubernetes pod status evidence.",
        )
    if finding.signal_type in {"request_latency", "cpu_usage", "cpu_throttling"}:
        return (
            "Review resource limits and request latency under load.",
            "Retest after capacity or concurrency changes.",
        )
    return ("Review the recorded evidence and rerun the chamber scenario.",)


def _evidence_reference(item: EvidenceArtifact, *, artifact_dir: Path) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=item.evidence_id,
        source=item.source,
        signal_type=item.signal_type,
        resource=item.resource,
        collected_at=item.collected_at,
        artifact_path=str(artifact_dir / "evidence.json"),
    )


def _write_evidence(path: Path, evidence: tuple[EvidenceArtifact, ...]) -> None:  # pragma: no cover
    path.write_text(json.dumps([asdict(item) for item in evidence], indent=2), encoding="utf-8")


def _write_metadata(
    path: Path,
    *,
    scenario: Scenario,
    run_id: str,
    context: str,
    image: str,
    environment: EnvironmentMetadata,
    traffic_plan: TrafficPlan,
    traffic_result: TrafficExecutionResult,
    timeline: ExperimentTimeline,
    commands: tuple[CommandRecord, ...],
    cleanup_performed: bool,
    limitations: tuple[str, ...],
) -> None:  # pragma: no cover
    payload: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": scenario.scenario_id,
        "context": context,
        "image": image,
        "environment": asdict(environment),
        "traffic_plan": asdict(traffic_plan),
        "traffic_result": asdict(traffic_result),
        "timeline": asdict(timeline),
        "commands": [asdict(command) for command in commands],
        "cleanup_performed": cleanup_performed,
        "limitations": list(limitations),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _report_path(options: LiveRunOptions, scenario: Scenario, run_id: str) -> Path:
    if options.output is not None:
        return options.output
    output_dir = options.output_dir or options.artifact_dir
    return output_dir / f"{run_id}-report.md"


def _scenario_limitations(scenario: Scenario) -> tuple[str, ...]:
    limitations = []
    fault_types = _fault_types(scenario)
    if _dependency_response_faults(scenario) and not scenario.document["environment"].get(
        "dependencies"
    ):
        limitations.append(
            "Dependency error and rate-limit faults require a future downstream dependency "
            "workload or fault proxy before live execution."
        )
    if "memory_pressure" in fault_types:
        limitations.append(
            "Memory pressure is implemented with Kubernetes resource patching for the "
            "sample service, not a general memory stress sidecar."
        )
    return tuple(limitations)


def _dependency_response_faults(scenario: Scenario) -> set[str]:
    return _fault_types(scenario) & {
        "dependency_errors",
        "dependency_latency",
        "dependency_rate_limit",
    }


def _fault_types(scenario: Scenario) -> set[str]:
    return {
        str(fault.get("type"))
        for fault in scenario.document.get("faults", [])
        if isinstance(fault, dict)
    }


def _fault_summary(scenario: Scenario) -> str:
    values = []
    for fault in scenario.document.get("faults", []):
        if isinstance(fault, dict):
            values.append(str(fault.get("type", "unknown")))
    return ", ".join(values) if values else "none"


def _run_id(scenario_id: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"chamber-{scenario_id}-{timestamp}"


def _git_commit() -> str:  # pragma: no cover
    completed = subprocess.run(
        ("git", "rev-parse", "--short", "HEAD"),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"
