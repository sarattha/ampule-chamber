from __future__ import annotations

import unittest
from pathlib import Path

from chamber.contracts.scenario import load_scenario
from chamber.environment import (
    EnvironmentPlanningError,
    plan_environment,
)
from chamber.environment.planning import validate_environment_contract

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"


class EnvironmentPlanningTests(unittest.TestCase):
    def test_kind_dry_run_plan_from_baseline_scenario(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        plan = plan_environment(scenario, run_id="run-20260612-001")

        self.assertEqual(plan.provider, "kind")
        self.assertEqual(plan.scenario_id, "baseline-health-001")
        self.assertEqual(plan.metadata.source_provider, "docker")
        self.assertIn("baseline-health-001", plan.namespace)
        self.assertEqual(
            [action.name for action in plan.actions],
            [
                "create-namespace",
                "apply-deployment",
                "apply-service",
                "verify-readiness",
                "collect-environment-metadata",
                "delete-managed-resources",
                "delete-namespace",
            ],
        )
        self.assertEqual(
            [manifest["kind"] for manifest in plan.manifests],
            ["Namespace", "Deployment", "Service"],
        )

    def test_namespace_names_labels_and_cleanup_are_deterministic(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        first_plan = plan_environment(scenario, run_id="run-abc")
        second_plan = plan_environment(scenario, run_id="run-abc")

        self.assertEqual(first_plan.namespace, second_plan.namespace)
        self.assertEqual(first_plan.metadata.labels, second_plan.metadata.labels)
        self.assertLessEqual(len(first_plan.namespace), 63)
        self.assertEqual(
            first_plan.metadata.labels["app.kubernetes.io/managed-by"],
            "ampule-chamber",
        )
        self.assertEqual(
            first_plan.cleanup.selectors,
            {
                "app.kubernetes.io/managed-by": "ampule-chamber",
                "chamber.ampule.dev/run-id": "run-abc",
            },
        )

    def test_generated_deployment_and_service_use_scenario_contract(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        plan = plan_environment(scenario, run_id="contract-run")
        deployment = next(
            manifest for manifest in plan.manifests if manifest["kind"] == "Deployment"
        )
        service = next(manifest for manifest in plan.manifests if manifest["kind"] == "Service")
        container = deployment["spec"]["template"]["spec"]["containers"][0]

        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(container["name"], "sample-service")
        self.assertEqual(container["image"], "ampule/sample-service:local")
        self.assertEqual(container["ports"][0]["containerPort"], 8080)
        self.assertEqual(container["resources"]["requests"]["cpu"], "100m")
        self.assertEqual(container["resources"]["limits"]["memory"], "256Mi")
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/readyz")
        self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/healthz")
        self.assertEqual(
            service["metadata"]["annotations"]["chamber.ampule.dev/deployment-name"],
            deployment["metadata"]["name"],
        )
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], "http")
        self.assertEqual(service["spec"]["type"], "ClusterIP")

    def test_service_names_are_sanitized_for_kubernetes_manifests(self) -> None:
        cases = {
            "sample_service": "sample-service",
            "Sample Service": "sample-service",
            "api/v1": "api-v1",
        }

        for source_name, expected_name in cases.items():
            with self.subTest(source_name=source_name):
                scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
                scenario.document["target"]["service"]["name"] = source_name

                plan = plan_environment(scenario, run_id="name-sanitize")
                deployment = next(
                    manifest for manifest in plan.manifests if manifest["kind"] == "Deployment"
                )
                service = next(
                    manifest for manifest in plan.manifests if manifest["kind"] == "Service"
                )
                container = deployment["spec"]["template"]["spec"]["containers"][0]

                self.assertEqual(container["name"], expected_name)
                self.assertEqual(
                    deployment["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"],
                    expected_name,
                )
                self.assertEqual(
                    service["spec"]["selector"]["app.kubernetes.io/name"],
                    expected_name,
                )
                self.assertEqual(
                    deployment["metadata"]["annotations"]["chamber.ampule.dev/source-service-name"],
                    source_name,
                )

    def test_service_names_without_dns_label_content_are_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["target"]["service"]["name"] = "///"

        failures = validate_environment_contract(scenario)

        self.assertEqual(failures[0].reason, "invalid_service_name")
        self.assertEqual(failures[0].path, "target.service.name")

    def test_planning_failure_reports_specific_missing_image_reason(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        del scenario.document["target"]["service"]["image"]

        failures = validate_environment_contract(scenario)

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "missing_image")
        self.assertEqual(failures[0].path, "target.service.image")
        with self.assertRaisesRegex(EnvironmentPlanningError, "target.service.image"):
            plan_environment(scenario, run_id="bad-image")

    def test_unsupported_provider_is_reported_before_future_provider_work(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        with self.assertRaisesRegex(EnvironmentPlanningError, "reserved for a later phase"):
            plan_environment(scenario, run_id="aks-run", provider_name="aks")

        failures = validate_environment_contract(scenario, provider_name="custom")
        self.assertEqual(failures[0].reason, "unsupported_provider")

        with self.assertRaisesRegex(EnvironmentPlanningError, "unsupported environment provider"):
            plan_environment(scenario, run_id="custom-run", provider_name="custom")

    def test_empty_run_id_is_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        with self.assertRaisesRegex(EnvironmentPlanningError, "run_id"):
            plan_environment(scenario, run_id="")

    def test_planning_failure_reports_missing_service_port(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["target"]["service"]["ports"] = []

        failures = validate_environment_contract(scenario)

        self.assertEqual(failures[0].reason, "missing_service_port")
        self.assertEqual(failures[0].path, "target.service.ports")

    def test_planning_failure_reports_invalid_replicas_and_resources(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["environment"]["replicas"] = 0
        scenario.document["environment"]["resources"] = {"cpuRequest": "100m"}

        failures = validate_environment_contract(scenario)

        self.assertEqual(
            [(failure.reason, failure.path) for failure in failures],
            [
                ("invalid_replicas", "environment.replicas"),
                ("invalid_resources", "environment.resources"),
            ],
        )

    def test_planning_failure_reports_unavailable_manifest_data(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["target"]["service"] = "not-a-service-map"

        failures = validate_environment_contract(scenario)

        self.assertEqual(failures[0].reason, "unavailable_manifest_data")
        self.assertEqual(failures[0].path, "target.service")


if __name__ == "__main__":
    unittest.main()
