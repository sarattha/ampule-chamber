from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from chamber.analysis.findings import Finding
from chamber.chaos import plan_faults
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.load import TrafficExecutionResult, plan_traffic
from chamber.observability import EvidenceArtifact
from chamber.orchestrator import build_experiment_timeline, live
from chamber.report import render_markdown_report

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return self.responses.get(command, _completed(command, returncode=0))

    def popen(
        self,
        command: tuple[str, ...],
        *,
        stdout: object | None = subprocess.PIPE,
        stderr: object | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        raise AssertionError(f"unexpected popen call: {command}")


class FakeProcess:
    def __init__(self, *, returncode: int = 0, polls: list[int | None] | None = None) -> None:
        self.returncode = returncode
        self.polls = polls or [0]
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        if len(self.polls) > 1:
            return self.polls.pop(0)
        return self.polls[0]

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.polls = [-15]

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.polls = [-9]

    def communicate(self) -> tuple[str, str]:
        return ("", "")


class TrafficRunner:
    def __init__(self, *, fault_returncode: int = 0) -> None:
        self.fault_returncode = fault_returncode
        self.port_forward = FakeProcess(returncode=0, polls=[None])
        traffic_polls: list[int | None] = [None] if fault_returncode else [None, 0]
        self.traffic = FakeProcess(returncode=0, polls=traffic_polls)
        self.popen_stdout: list[object | None] = []
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return _completed(command, returncode=self.fault_returncode, stderr="fault failed")

    def popen(
        self,
        command: tuple[str, ...],
        *,
        stdout: object | None = subprocess.PIPE,
        stderr: object | None = subprocess.PIPE,
    ) -> subprocess.Popen[str]:
        self.popen_stdout.append(stdout)
        if command and command[0] == "k6":
            if hasattr(stdout, "write"):
                stdout.write("k6 output\n")
            return self.traffic  # type: ignore[return-value]
        return self.port_forward  # type: ignore[return-value]


class Phase06SafetyTests(unittest.TestCase):
    def test_context_safety_requires_project_kind_context(self) -> None:
        live._validate_context("kind-ampule-chamber")

        with self.assertRaisesRegex(live.LiveRunError, "require Kubernetes context"):
            live._validate_context("kind-other")

        with self.assertRaisesRegex(live.LiveRunError, "unsafe Kubernetes context"):
            live._validate_context("kind-ampule-production")

    def test_current_context_must_match_required_context(self) -> None:
        runner = FakeRunner(
            {
                ("kubectl", "config", "current-context"): _completed(
                    ("kubectl", "config", "current-context"),
                    stdout="kind-ampule-chamber\n",
                )
            }
        )

        live._require_current_context("kind-ampule-chamber", runner)

        runner = FakeRunner(
            {
                ("kubectl", "config", "current-context"): _completed(
                    ("kubectl", "config", "current-context"),
                    stdout="kind-other\n",
                )
            }
        )
        with self.assertRaisesRegex(live.LiveRunError, "expected 'kind-ampule-chamber'"):
            live._require_current_context("kind-ampule-chamber", runner)

    def test_prometheus_is_required_and_must_be_reachable(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(live.LiveRunError, "PROMETHEUS_URL"):
                live._require_prometheus(None)

        response = Mock()
        response.read.return_value = json.dumps({"status": "success"}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("chamber.orchestrator.live.urlopen", return_value=response):
            self.assertEqual(
                live._require_prometheus("http://prometheus:9090"), "http://prometheus:9090"
            )

    def test_preloaded_image_check_uses_kind_nodes(self) -> None:
        runner = FakeRunner(
            {
                ("kind", "get", "nodes", "--name", "ampule-chamber"): _completed(
                    ("kind", "get", "nodes", "--name", "ampule-chamber"),
                    stdout="ampule-chamber-control-plane\n",
                ),
                (
                    "docker",
                    "exec",
                    "ampule-chamber-control-plane",
                    "crictl",
                    "images",
                    "-q",
                    "ampule/sample-service:local",
                ): _completed(("docker", "exec"), stdout="sha256:abc\n"),
            }
        )

        live._verify_kind_image("kind-ampule-chamber", "ampule/sample-service:local", runner)

        missing = FakeRunner(
            {
                ("kind", "get", "nodes", "--name", "ampule-chamber"): _completed(
                    ("kind", "get", "nodes", "--name", "ampule-chamber"),
                    stdout="ampule-chamber-control-plane\n",
                )
            }
        )
        with self.assertRaisesRegex(live.LiveRunError, "not preloaded"):
            live._verify_kind_image("kind-ampule-chamber", "ampule/sample-service:local", missing)


class Phase06RunnerWiringTests(unittest.TestCase):
    def test_cleanup_decisions_match_retain_flags(self) -> None:
        self.assertTrue(live.should_cleanup(retain=False, retain_on_failure=False, success=True))
        self.assertTrue(live.should_cleanup(retain=False, retain_on_failure=False, success=False))
        self.assertFalse(live.should_cleanup(retain=True, retain_on_failure=False, success=True))
        self.assertFalse(live.should_cleanup(retain=False, retain_on_failure=True, success=False))
        self.assertTrue(live.should_cleanup(retain=False, retain_on_failure=True, success=True))

    def test_live_runner_gates_dependency_response_faults(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "retry-storm.yaml")

        with self.assertRaisesRegex(live.LiveRunError, "does not provision"):
            live._validate_live_fault_support(scenario)

    def test_report_wiring_includes_live_evidence_cleanup_and_limitations(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase06-test").metadata
        with TemporaryDirectory() as artifact_dir:
            traffic_plan = plan_traffic(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            Path(traffic_plan.summary_path).write_text(
                json.dumps({"metrics": {"http_reqs": {"count": 1}}}),
                encoding="utf-8",
            )
            fault_plan = plan_faults(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            timeline = build_experiment_timeline(
                scenario,
                traffic_plan=traffic_plan,
                fault_plan=fault_plan,
            )
            report = live._report_input(
                scenario=scenario,
                scenario_path=SCENARIO_DIR / "dependency-failure.yaml",
                environment=environment,
                traffic_plan=traffic_plan,
                traffic_result=TrafficExecutionResult(
                    success=True,
                    command=traffic_plan.command,
                    exit_status=0,
                    stdout="ok",
                    stderr="",
                ),
                timeline=timeline,
                evidence=(
                    EvidenceArtifact(
                        evidence_id="phase06-test:kubernetes:pod_status:ns",
                        run_id="phase06-test",
                        scenario_id=scenario.scenario_id,
                        source="kubernetes",
                        signal_type="pod_status",
                        resource=environment.namespace,
                        collected_at="2026-06-12T00:00:00+00:00",
                        start_time=None,
                        end_time=None,
                        payload={"items": []},
                    ),
                ),
                findings=(
                    Finding(
                        finding_id="phase06-test:k6:error-rate",
                        signal_type="error_rate",
                        affected_resource="sample-service",
                        observed_facts=("k6 HTTP failure rate was high.",),
                        suspected_cause="Dependency degraded during load.",
                        severity="high",
                        confidence="high",
                        evidence_ids=("phase06-test:k6-summary:summary.json",),
                        related_timeline_ids=("traffic-start",),
                    ),
                ),
                limitations=live._scenario_limitations(scenario),
                artifact_dir=Path(artifact_dir),
                report_path=Path(artifact_dir) / "report.md",
                duration_seconds=1,
                cleanup_performed=True,
            )

        markdown = render_markdown_report(report)

        self.assertIn("dependency-failure-001", markdown)
        self.assertIn("phase06-test:kubernetes:pod_status:ns", markdown)
        self.assertIn("No phase-specific limitations recorded", markdown)
        self.assertIn("Cleanup completed", markdown)

    def test_traffic_execution_redirects_k6_output_away_from_pipes(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase06-test").metadata
        with TemporaryDirectory() as artifact_dir:
            traffic_plan = plan_traffic(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            fault_plan = plan_faults(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            runner = TrafficRunner()
            with (
                patch("chamber.orchestrator.live.shutil.which", return_value="/usr/bin/k6"),
                patch("chamber.orchestrator.live._wait_for_target"),
            ):
                result = live._execute_traffic_with_faults(
                    traffic_plan,
                    fault_plan=fault_plan,
                    runner=runner,
                    commands=[],
                )

        self.assertTrue(result.success)
        self.assertIn("k6 output", result.stdout)
        self.assertIs(subprocess.PIPE, runner.popen_stdout[0])
        self.assertIsNot(subprocess.PIPE, runner.popen_stdout[1])

    def test_traffic_process_stops_when_fault_action_fails(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase06-test").metadata
        with TemporaryDirectory() as artifact_dir:
            traffic_plan = plan_traffic(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            fault_plan = plan_faults(
                scenario,
                environment=environment,
                artifact_dir=artifact_dir,
            )
            runner = TrafficRunner(fault_returncode=1)
            with (
                patch("chamber.orchestrator.live.shutil.which", return_value="/usr/bin/k6"),
                patch("chamber.orchestrator.live._wait_for_target"),
                patch("chamber.orchestrator.live.time.monotonic", side_effect=[0.0, 999.0]),
            ):
                with self.assertRaisesRegex(live.LiveRunError, "fault failed"):
                    live._execute_traffic_with_faults(
                        traffic_plan,
                        fault_plan=fault_plan,
                        runner=runner,
                        commands=[],
                    )

        self.assertTrue(runner.traffic.terminated)
        self.assertTrue(runner.port_forward.terminated)


def _completed(
    command: tuple[str, ...],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


if __name__ == "__main__":
    unittest.main()
