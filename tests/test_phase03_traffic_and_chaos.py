from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from chamber.chaos import FaultPlanningError, plan_faults
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.load import K6TrafficAdapter, TrafficPlanningError, plan_traffic
from chamber.load import planning as load_planning
from chamber.load.planning import validate_traffic_contract
from chamber.orchestrator import build_experiment_timeline

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"
ARTIFACT_DIR = ROOT / "tests/fixtures"


class Phase03TrafficPlanningTests(unittest.TestCase):
    def test_k6_plan_generation_for_core_scenarios(self) -> None:
        cases = {
            "baseline-health.yaml": (180, 5, "/healthz"),
            "dependency-failure.yaml": (420, 25, "/dependency"),
            "retry-storm.yaml": (600, 75, "/dependency"),
            "soak-test.yaml": (2400, 20, "/work"),
        }

        for scenario_file, expected in cases.items():
            with self.subTest(scenario_file=scenario_file):
                scenario = load_scenario(SCENARIO_DIR / scenario_file)
                environment = plan_environment(scenario, run_id="phase03-test").metadata

                plan = plan_traffic(
                    scenario,
                    environment=environment,
                    artifact_dir=ARTIFACT_DIR,
                )

                expected_duration, expected_peak_vus, expected_path = expected
                self.assertEqual(plan.tool, "k6")
                self.assertEqual(plan.total_duration_seconds, expected_duration)
                self.assertEqual(max(stage.target_vus for stage in plan.stages), expected_peak_vus)
                self.assertIn(expected_path, plan.target_url)
                self.assertIn("127.0.0.1", plan.target_url)
                self.assertIn("127.0.0.1", plan.readiness_url)
                self.assertIn("http_req_duration.p99", plan.result_fields)
                self.assertIn("export const options", plan.script)
                self.assertIn(str(expected_peak_vus), plan.script)
                self.assertEqual(plan.command[0], "k6")
                self.assertEqual(plan.command[1], "run")
                self.assertEqual(
                    plan.port_forward_command[:4],
                    ("kubectl", "-n", environment.namespace, "port-forward"),
                )
                self.assertIn("port-forward", plan.port_forward_command)

    def test_invalid_traffic_definitions_report_specific_failures(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["traffic"]["tool"] = "locust"
        scenario.document["traffic"]["entrypoint"] = ""
        scenario.document["traffic"]["stages"][0]["duration"] = "1.5m"
        scenario.document["traffic"]["stages"][1]["targetVus"] = 6
        scenario.document["safety"]["maxVirtualUsers"] = "5"

        failures = validate_traffic_contract(scenario)

        self.assertTrue(any("traffic.tool" in failure for failure in failures))
        self.assertTrue(any("traffic.entrypoint" in failure for failure in failures))
        self.assertTrue(any("duration" in failure for failure in failures))
        self.assertTrue(any("exceeds safety.maxVirtualUsers" in failure for failure in failures))
        with self.assertRaisesRegex(TrafficPlanningError, "cannot build traffic plan"):
            plan_traffic(
                scenario,
                environment=plan_environment(
                    load_scenario(SCENARIO_DIR / "baseline-health.yaml"),
                    run_id="phase03-test",
                ).metadata,
                artifact_dir=ARTIFACT_DIR,
            )

    def test_missing_k6_binary_returns_clear_execution_error(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        plan = plan_traffic(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        with patch("chamber.load.planning.shutil.which", return_value=None):
            result = K6TrafficAdapter().execute(plan)

        self.assertFalse(result.success)
        self.assertIsNone(result.exit_status)
        self.assertIn("k6 executable was not found", result.error or "")

    def test_execute_writes_script_runs_k6_and_stops_port_forward(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        with TemporaryDirectory() as artifact_dir:
            plan = plan_traffic(scenario, environment=environment, artifact_dir=artifact_dir)
            port_forward = Mock()
            completed = Mock(returncode=0, stdout="ok", stderr="")

            with (
                patch("chamber.load.planning.shutil.which", return_value="/usr/bin/tool"),
                patch("chamber.load.planning._start_port_forward", return_value=port_forward),
                patch("chamber.load.planning._stop_port_forward") as stop_port_forward,
                patch("chamber.load.planning.subprocess.run", return_value=completed),
            ):
                result = K6TrafficAdapter().execute(plan)

            self.assertTrue(Path(plan.script_path).exists())
            self.assertTrue(result.success)
            self.assertEqual(result.stdout, "ok")
            stop_port_forward.assert_called_once_with(port_forward)

    def test_start_port_forward_requires_kubectl_and_waits_for_target(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        plan = plan_traffic(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        with patch("chamber.load.planning.shutil.which", return_value=None):
            with self.assertRaisesRegex(TrafficPlanningError, "kubectl executable"):
                load_planning._start_port_forward(plan)

        process = Mock()
        with (
            patch("chamber.load.planning.shutil.which", return_value="/usr/bin/kubectl"),
            patch("chamber.load.planning.subprocess.Popen", return_value=process) as popen,
            patch("chamber.load.planning._wait_for_target") as wait_for_target,
        ):
            result = load_planning._start_port_forward(plan)

        self.assertIs(result, process)
        popen.assert_called_once()
        wait_for_target.assert_called_once_with(plan.readiness_url, process)

    def test_post_traffic_uses_readiness_endpoint_for_port_forward_probe(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "external-text-translation.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata

        plan = plan_traffic(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        self.assertEqual(plan.method, "POST")
        self.assertIn("/translations", plan.target_url)
        self.assertIn("/health", plan.readiness_url)
        self.assertNotEqual(plan.target_url, plan.readiness_url)

    def test_wait_for_target_reports_failed_port_forward(self) -> None:
        process = Mock()
        process.poll.return_value = 1
        process.communicate.return_value = ("", "service not found")

        with self.assertRaisesRegex(TrafficPlanningError, "service not found"):
            load_planning._wait_for_target("http://127.0.0.1:1/healthz", process)

    def test_stop_port_forward_terminates_or_kills_process(self) -> None:
        stopped = Mock()
        stopped.poll.return_value = 0
        load_planning._stop_port_forward(stopped)
        stopped.terminate.assert_not_called()

        running = Mock()
        running.poll.return_value = None
        running.wait.return_value = 0
        load_planning._stop_port_forward(running)
        running.terminate.assert_called_once()
        running.kill.assert_not_called()

        stuck = Mock()
        stuck.poll.return_value = None
        stuck.wait.side_effect = [subprocess.TimeoutExpired("kubectl", 5), 0]
        load_planning._stop_port_forward(stuck)
        stuck.kill.assert_called_once()


class Phase03ChaosPlanningTests(unittest.TestCase):
    def test_dependency_unavailable_fault_is_scoped_and_reversible(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata

        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)
            self.assertEqual([action.action_type for action in plan.actions], ["inject", "remove"])
            self.assertEqual(
                [event.event_type for event in plan.events],
                ["fault_start", "fault_removed"],
            )
            self.assertEqual(plan.actions[0].offset_seconds, 120)
            self.assertEqual(plan.actions[1].offset_seconds, 180)
            self.assertEqual(plan.actions[0].command[:2], ("kubectl", "apply"))
            self.assertEqual(plan.actions[1].command[:2], ("kubectl", "delete"))
            self.assertIsNotNone(plan.actions[0].manifest_path)
            assert plan.actions[0].manifest_path is not None
            manifest_path = Path(plan.actions[0].manifest_path)
            self.assertTrue(manifest_path.exists())
            self.assertIn("kind: NetworkPolicy", manifest_path.read_text(encoding="utf-8"))
            manifest = plan.actions[0].manifest
            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest["kind"], "NetworkPolicy")
            self.assertEqual(
                manifest["spec"]["podSelector"]["matchLabels"],
                {"chamber.ampule.dev/run-id": "phase03-test"},
            )
            self.assertEqual(manifest["spec"]["egress"], [])

    def test_none_fault_produces_empty_fault_plan(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata

        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)

        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.events, ())

    def test_invalid_fault_definitions_are_reported(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        del scenario.document["faults"][0]["target"]

        with self.assertRaisesRegex(FaultPlanningError, "target"):
            with TemporaryDirectory() as artifact_dir:
                plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)

    def test_phase06_fault_mappings_cover_supported_current_mvp_scenarios(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "oom-stress.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)
        self.assertEqual([action.action_type for action in plan.actions], ["inject", "remove"])
        self.assertEqual(
            plan.actions[0].command[:4], ("kubectl", "-n", environment.namespace, "patch")
        )

        scenario = load_scenario(SCENARIO_DIR / "retry-storm.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)
        self.assertEqual(
            [action.action_type for action in plan.actions],
            ["inject", "remove", "inject", "remove"],
        )
        self.assertIn("FAULT_STATUS", plan.actions[0].command[-1])


class Phase03TimelineTests(unittest.TestCase):
    def test_experiment_timeline_orders_traffic_fault_and_recovery_events(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        traffic_plan = plan_traffic(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)
        with TemporaryDirectory() as artifact_dir:
            fault_plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)

        timeline = build_experiment_timeline(
            scenario,
            traffic_plan=traffic_plan,
            fault_plan=fault_plan,
        )

        self.assertEqual(
            [(event.event_type, event.offset_seconds) for event in timeline.events],
            [
                ("environment_setup", 0),
                ("traffic_start", 0),
                ("fault_start", 120),
                ("fault_removed", 180),
                ("traffic_stop", 420),
                ("observation_stop", 420),
                ("recovery_validate", 420),
                ("cleanup_planned", 420),
            ],
        )


if __name__ == "__main__":
    unittest.main()
