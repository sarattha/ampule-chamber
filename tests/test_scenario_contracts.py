from __future__ import annotations

import unittest
from pathlib import Path

from chamber.contracts.lifecycle import RunState, is_allowed_transition
from chamber.contracts.scenario import (
    ENVIRONMENT_PROVIDERS,
    FAILURE_CONDITION_TYPES,
    OBSERVABILITY_SIGNALS,
    SUCCESS_CONDITION_TYPES,
    TRAFFIC_TOOLS,
    ScenarioValidationError,
    load_scenario,
    validate_scenario_document,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"


class ScenarioContractTests(unittest.TestCase):
    def test_all_example_scenarios_validate(self) -> None:
        paths = sorted(SCENARIO_DIR.glob("*.yaml"))

        self.assertGreaterEqual(len(paths), 5)
        scenarios = [load_scenario(path) for path in paths]

        self.assertEqual(len({scenario.scenario_id for scenario in scenarios}), len(scenarios))

    def test_schema_covers_required_reliability_sections(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        document = scenario.document

        self.assertIn("baseline", document)
        self.assertIn("traffic", document)
        self.assertIn("observability", document)
        self.assertIn("failureConditions", document)
        self.assertIn("successConditions", document)

    def test_condition_and_signal_vocabularies_cover_mvp_failure_modes(self) -> None:
        self.assertIn("oom_killed", FAILURE_CONDITION_TYPES)
        self.assertIn("restart_loop", FAILURE_CONDITION_TYPES)
        self.assertIn("dependency_unavailable", FAILURE_CONDITION_TYPES)
        self.assertIn("retry_amplification", FAILURE_CONDITION_TYPES)
        self.assertIn("queue_backlog_not_draining", FAILURE_CONDITION_TYPES)
        self.assertIn("kubernetes_events", OBSERVABILITY_SIGNALS)
        self.assertIn("logs", OBSERVABILITY_SIGNALS)
        self.assertIn("traces", OBSERVABILITY_SIGNALS)
        self.assertIn("baseline_checks_pass", SUCCESS_CONDITION_TYPES)

    def test_invalid_scenario_reports_missing_contract_fields(self) -> None:
        with self.assertRaisesRegex(ScenarioValidationError, "missing required keys"):
            validate_scenario_document({"apiVersion": "chamber.ampule.dev/v1alpha1"})

    def test_invalid_yaml_reports_parse_error(self) -> None:
        path = ROOT / "docs/internal/phases/phase-01-foundation/artifacts/invalid-scenario.tmp.yaml"
        path.write_text("apiVersion: [", encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)

        with self.assertRaisesRegex(ScenarioValidationError, "invalid YAML"):
            load_scenario(path)

    def test_scalar_yaml_is_rejected(self) -> None:
        path = ROOT / "docs/internal/phases/phase-01-foundation/artifacts/scalar-scenario.tmp.yaml"
        path.write_text("not-a-mapping", encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)

        with self.assertRaisesRegex(ScenarioValidationError, "must be a YAML mapping"):
            load_scenario(path)

    def test_optional_metadata_tags_must_be_strings(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml").document
        scenario["metadata"]["tags"] = ["baseline", ""]

        with self.assertRaisesRegex(ScenarioValidationError, "metadata.tags"):
            validate_scenario_document(scenario)

    def test_dependency_entries_require_name_and_type(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml").document
        scenario["environment"]["dependencies"] = [{"name": "downstream-api"}]

        with self.assertRaisesRegex(ScenarioValidationError, "dependencies"):
            validate_scenario_document(scenario)

    def test_fault_entries_require_allowed_type(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml").document
        scenario["faults"] = [{"type": "solar_flare", "description": "Unsupported fault."}]

        with self.assertRaisesRegex(ScenarioValidationError, "faults"):
            validate_scenario_document(scenario)

    def test_empty_required_lists_are_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml").document
        scenario["observability"]["signals"] = []

        with self.assertRaisesRegex(ScenarioValidationError, "expected at least one item"):
            validate_scenario_document(scenario)

    def test_unknown_provider_and_traffic_tool_are_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml").document
        scenario["environment"]["provider"] = "bare_metal"

        with self.assertRaisesRegex(ScenarioValidationError, "environment.provider"):
            validate_scenario_document(scenario)

        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml").document
        scenario["traffic"]["tool"] = "siege"

        with self.assertRaisesRegex(ScenarioValidationError, "traffic.tool"):
            validate_scenario_document(scenario)

    def test_vocabularies_include_docker_and_load_tools(self) -> None:
        self.assertIn("docker", ENVIRONMENT_PROVIDERS)
        self.assertIn("kind", ENVIRONMENT_PROVIDERS)
        self.assertIn("k6", TRAFFIC_TOOLS)
        self.assertIn("locust", TRAFFIC_TOOLS)


class RunLifecycleTests(unittest.TestCase):
    def test_lifecycle_allows_happy_path(self) -> None:
        happy_path = [
            RunState.CREATED,
            RunState.INTAKE_VALIDATED,
            RunState.ENVIRONMENT_READY,
            RunState.BASELINE_RUNNING,
            RunState.BASELINE_PASSED,
            RunState.EXPERIMENT_RUNNING,
            RunState.RECOVERY_VALIDATING,
            RunState.ANALYZING,
            RunState.REPORTING,
            RunState.COMPLETED,
        ]

        for current, next_state in zip(happy_path, happy_path[1:], strict=False):
            self.assertTrue(is_allowed_transition(current, next_state))

    def test_terminal_states_do_not_transition(self) -> None:
        self.assertFalse(is_allowed_transition(RunState.COMPLETED, RunState.REPORTING))
        self.assertFalse(is_allowed_transition(RunState.FAILED, RunState.REPORTING))
        self.assertFalse(is_allowed_transition(RunState.CANCELLED, RunState.REPORTING))


if __name__ == "__main__":
    unittest.main()
