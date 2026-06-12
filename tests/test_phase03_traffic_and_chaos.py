from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from chamber.chaos import FaultPlanningError, plan_faults
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.load import K6TrafficAdapter, TrafficPlanningError, plan_traffic
from chamber.load.planning import validate_traffic_contract
from chamber.orchestrator import build_experiment_timeline

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"
ARTIFACT_DIR = ROOT / "docs/internal/phases/phase-03-traffic-and-chaos/artifacts"


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
                self.assertIn("http_req_duration.p99", plan.result_fields)
                self.assertIn("export const options", plan.script)
                self.assertIn(str(expected_peak_vus), plan.script)
                self.assertEqual(plan.command[0], "k6")
                self.assertEqual(plan.command[1], "run")

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


class Phase03ChaosPlanningTests(unittest.TestCase):
    def test_dependency_unavailable_fault_is_scoped_and_reversible(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata

        plan = plan_faults(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        self.assertEqual([action.action_type for action in plan.actions], ["inject", "remove"])
        self.assertEqual(
            [event.event_type for event in plan.events],
            ["fault_start", "fault_removed"],
        )
        self.assertEqual(plan.actions[0].offset_seconds, 120)
        self.assertEqual(plan.actions[1].offset_seconds, 180)
        self.assertEqual(plan.actions[0].command[:2], ("kubectl", "apply"))
        self.assertEqual(plan.actions[1].command[:2], ("kubectl", "delete"))
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

        plan = plan_faults(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.events, ())

    def test_invalid_and_reserved_fault_definitions_are_reported(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        del scenario.document["faults"][0]["target"]

        with self.assertRaisesRegex(FaultPlanningError, "target"):
            plan_faults(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        scenario = load_scenario(SCENARIO_DIR / "oom-stress.yaml")
        with self.assertRaisesRegex(FaultPlanningError, "reserved for a later phase"):
            plan_faults(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)


class Phase03TimelineTests(unittest.TestCase):
    def test_experiment_timeline_orders_traffic_fault_and_recovery_events(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase03-test").metadata
        traffic_plan = plan_traffic(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)
        fault_plan = plan_faults(scenario, environment=environment, artifact_dir=ARTIFACT_DIR)

        timeline = build_experiment_timeline(
            scenario,
            traffic_plan=traffic_plan,
            fault_plan=fault_plan,
        )

        self.assertEqual(
            [(event.event_type, event.offset_seconds) for event in timeline.events],
            [
                ("traffic_start", 0),
                ("fault_start", 120),
                ("fault_removed", 180),
                ("traffic_stop", 420),
                ("recovery_validate", 420),
            ],
        )


if __name__ == "__main__":
    unittest.main()
