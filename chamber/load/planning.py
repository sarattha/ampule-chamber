"""k6 traffic planning for chamber scenarios."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.request import urlopen

from chamber.contracts.scenario import Scenario
from chamber.environment import EnvironmentMetadata

K6_TOOL = "k6"
RESULT_FIELDS = (
    "http_reqs",
    "http_req_failed",
    "http_req_duration.p95",
    "http_req_duration.p99",
)


class TrafficPlanningError(ValueError):
    """Raised when a traffic plan cannot be generated or executed."""


@dataclass(frozen=True)
class TrafficStage:
    """One scenario traffic stage normalized for runner configuration."""

    duration: str
    duration_seconds: int
    target_vus: int


@dataclass(frozen=True)
class TrafficPlan:
    """Deterministic runner plan for a scenario traffic profile."""

    run_id: str
    scenario_id: str
    tool: str
    target_url: str
    stages: tuple[TrafficStage, ...]
    total_duration_seconds: int
    script_path: str
    summary_path: str
    command: tuple[str, ...]
    port_forward_command: tuple[str, ...]
    port_forward_cleanup_command: tuple[str, ...]
    script: str
    result_fields: tuple[str, ...]


@dataclass(frozen=True)
class TrafficExecutionResult:
    """Observed result from an attempted traffic execution."""

    success: bool
    command: tuple[str, ...]
    exit_status: int | None
    stdout: str
    stderr: str
    error: str | None = None


class TrafficAdapter(Protocol):
    """Builds and optionally executes traffic for one runner."""

    tool_name: str

    def build_plan(
        self,
        scenario: Scenario,
        *,
        environment: EnvironmentMetadata,
        artifact_dir: str | Path,
    ) -> TrafficPlan:
        """Build a deterministic traffic plan."""

    def execute(self, plan: TrafficPlan) -> TrafficExecutionResult:
        """Execute a generated traffic plan."""


class K6TrafficAdapter:
    """Traffic adapter for k6 staged VU plans."""

    tool_name = K6_TOOL

    def build_plan(
        self,
        scenario: Scenario,
        *,
        environment: EnvironmentMetadata,
        artifact_dir: str | Path,
    ) -> TrafficPlan:
        failures = validate_traffic_contract(scenario)
        if failures:
            raise TrafficPlanningError("cannot build traffic plan: " + "; ".join(failures))

        artifact_path = Path(artifact_dir)
        script_path = artifact_path / f"{scenario.scenario_id}-k6.js"
        summary_path = artifact_path / f"{scenario.scenario_id}-k6-summary.json"
        stages = _traffic_stages(scenario)
        service_name = environment.resource_names.get(
            "service",
            str(scenario.document["target"]["service"]["name"]),
        )
        service_port = _service_port(scenario)
        local_port = _local_forward_port(scenario.scenario_id, environment.run_id)
        target_url = _target_url(scenario, local_port=local_port)
        script = _k6_script(stages=stages, target_url=target_url)
        command = (
            "k6",
            "run",
            "--summary-export",
            str(summary_path),
            str(script_path),
        )
        port_forward_command = (
            "kubectl",
            "-n",
            environment.namespace,
            "port-forward",
            f"service/{service_name}",
            f"{local_port}:{service_port}",
        )
        port_forward_cleanup_command = (
            "pkill",
            "-f",
            " ".join(port_forward_command),
        )
        return TrafficPlan(
            run_id=environment.run_id,
            scenario_id=scenario.scenario_id,
            tool=self.tool_name,
            target_url=target_url,
            stages=stages,
            total_duration_seconds=sum(stage.duration_seconds for stage in stages),
            script_path=str(script_path),
            summary_path=str(summary_path),
            command=command,
            port_forward_command=port_forward_command,
            port_forward_cleanup_command=port_forward_cleanup_command,
            script=script,
            result_fields=RESULT_FIELDS,
        )

    def execute(self, plan: TrafficPlan) -> TrafficExecutionResult:
        if shutil.which("k6") is None:
            return TrafficExecutionResult(
                success=False,
                command=plan.command,
                exit_status=None,
                stdout="",
                stderr="",
                error="k6 executable was not found on PATH; install k6 to run live traffic.",
            )

        script_path = Path(plan.script_path)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(plan.script, encoding="utf-8")
        port_forward = _start_port_forward(plan)
        try:
            completed = subprocess.run(
                plan.command,
                check=False,
                capture_output=True,
                text=True,
            )
            return TrafficExecutionResult(
                success=completed.returncode == 0,
                command=plan.command,
                exit_status=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                error=None if completed.returncode == 0 else "k6 exited with a non-zero status.",
            )
        finally:
            _stop_port_forward(port_forward)


def plan_traffic(
    scenario: Scenario,
    *,
    environment: EnvironmentMetadata,
    artifact_dir: str | Path,
) -> TrafficPlan:
    """Build a deterministic k6 traffic plan from a scenario."""

    return K6TrafficAdapter().build_plan(
        scenario,
        environment=environment,
        artifact_dir=artifact_dir,
    )


def validate_traffic_contract(scenario: Scenario) -> tuple[str, ...]:
    """Return specific traffic contract failures for planning."""

    failures: list[str] = []
    traffic = _mapping(scenario.document.get("traffic"), "traffic", failures)
    safety = _mapping(scenario.document.get("safety"), "safety", failures)
    target = _mapping(scenario.document.get("target"), "target", failures)
    service = _mapping(target.get("service") if target else None, "target.service", failures)

    if traffic is not None:
        if traffic.get("tool") != K6_TOOL:
            failures.append(f"traffic.tool: only {K6_TOOL!r} is implemented for phase 03")
        if not _is_non_empty_string(traffic.get("entrypoint")):
            failures.append("traffic.entrypoint: non-empty HTTP path is required")
        stages = traffic.get("stages")
        if not isinstance(stages, list) or not stages:
            failures.append("traffic.stages: at least one stage is required")
        else:
            max_vus = _max_virtual_users(safety, failures)
            for index, stage in enumerate(stages):
                _validate_stage(stage, index=index, max_vus=max_vus, failures=failures)

    if service is not None:
        ports = service.get("ports")
        if not isinstance(ports, list) or not ports:
            failures.append("target.service.ports: at least one service port is required")
        elif not isinstance(ports[0], dict) or not isinstance(ports[0].get("port"), int):
            failures.append("target.service.ports[0].port: integer port is required")

    return tuple(failures)


def _validate_stage(
    value: Any,
    *,
    index: int,
    max_vus: int | None,
    failures: list[str],
) -> None:
    path = f"traffic.stages[{index}]"
    if not isinstance(value, dict):
        failures.append(f"{path}: expected mapping")
        return
    duration = value.get("duration")
    try:
        parse_duration_seconds(duration)
    except TrafficPlanningError as exc:
        failures.append(f"{path}.duration: {exc}")
    target_vus = value.get("targetVus")
    if not isinstance(target_vus, int) or target_vus < 0:
        failures.append(f"{path}.targetVus: non-negative integer is required")
    elif max_vus is not None and target_vus > max_vus:
        failures.append(f"{path}.targetVus: {target_vus} exceeds safety.maxVirtualUsers {max_vus}")


def parse_duration_seconds(value: Any) -> int:
    """Parse an MVP duration string such as 60s, 2m, or 1h."""

    if not isinstance(value, str) or not value.strip():
        raise TrafficPlanningError("non-empty duration string is required")
    text = value.strip()
    unit = text[-1]
    amount_text = text[:-1]
    multipliers = {"s": 1, "m": 60, "h": 3600}
    if unit not in multipliers or not amount_text.isdigit():
        raise TrafficPlanningError("duration must use an integer s, m, or h suffix")
    amount = int(amount_text)
    if amount <= 0:
        raise TrafficPlanningError("duration must be positive")
    return amount * multipliers[unit]


def _traffic_stages(scenario: Scenario) -> tuple[TrafficStage, ...]:
    stages = []
    for stage in scenario.document["traffic"]["stages"]:
        duration = str(stage["duration"])
        stages.append(
            TrafficStage(
                duration=duration,
                duration_seconds=parse_duration_seconds(duration),
                target_vus=int(stage["targetVus"]),
            )
        )
    return tuple(stages)


def _target_url(scenario: Scenario, *, local_port: int) -> str:
    entrypoint = str(scenario.document["traffic"]["entrypoint"])
    path = entrypoint if entrypoint.startswith("/") else f"/{entrypoint}"
    return f"http://127.0.0.1:{local_port}{path}"


def _service_port(scenario: Scenario) -> int:
    return int(scenario.document["target"]["service"]["ports"][0]["port"])


def _local_forward_port(scenario_id: str, run_id: str) -> int:
    digest = sha1(f"{scenario_id}:{run_id}:k6".encode()).hexdigest()
    return 20000 + (int(digest[:6], 16) % 10000)


def _start_port_forward(plan: TrafficPlan) -> subprocess.Popen[str] | None:
    if not plan.port_forward_command:
        return None
    if shutil.which("kubectl") is None:
        raise TrafficPlanningError(
            "kubectl executable was not found on PATH; install kubectl to port-forward "
            "cluster services for local k6 execution."
        )
    process = subprocess.Popen(
        plan.port_forward_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_target(plan.target_url, process)
    return process


def _wait_for_target(target_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise TrafficPlanningError(
                "kubectl port-forward exited before k6 could run: " + stderr.strip()
            )
        try:
            with urlopen(target_url, timeout=0.5):
                return
        except (OSError, URLError):
            time.sleep(0.2)
    _stop_port_forward(process)
    raise TrafficPlanningError(f"timed out waiting for port-forward target {target_url}")


def _stop_port_forward(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _k6_script(*, stages: tuple[TrafficStage, ...], target_url: str) -> str:
    stage_docs = [{"duration": stage.duration, "target": stage.target_vus} for stage in stages]
    return (
        "import http from 'k6/http';\n"
        "import { check } from 'k6';\n\n"
        f"export const options = {json.dumps({'stages': stage_docs}, indent=2)};\n\n"
        f"const targetUrl = __ENV.TARGET_URL || {json.dumps(target_url)};\n\n"
        "export default function () {\n"
        "  const response = http.get(targetUrl);\n"
        "  check(response, {\n"
        "    'status is below 500': (r) => r.status < 500,\n"
        "  });\n"
        "}\n"
    )


def _max_virtual_users(safety: dict[str, Any] | None, failures: list[str]) -> int | None:
    if safety is None:
        return None
    value = safety.get("maxVirtualUsers")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    failures.append("safety.maxVirtualUsers: integer string is required")
    return None


def _mapping(value: Any, path: str, failures: list[str]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    failures.append(f"{path}: expected mapping")
    return None


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
