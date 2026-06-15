from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from chamber.chaos import FaultPlanningError, plan_faults
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.report import (
    ReportInput,
    ReportSection,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"


class Phase08FaultPlanningTests(unittest.TestCase):
    def test_pod_kill_is_scoped_to_chamber_labels(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["faults"] = [
            {
                "type": "pod_kill",
                "description": "Delete a target pod and let Kubernetes replace it.",
                "startAfter": "30s",
            }
        ]
        environment = plan_environment(scenario, run_id="phase08-pod-kill").metadata

        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)

        self.assertEqual(
            plan.actions[0].command[:5], ("kubectl", "-n", environment.namespace, "delete", "pod")
        )
        selector = plan.actions[0].command[6]
        self.assertIn("app.kubernetes.io/name=sample-service", selector)
        self.assertIn("chamber.ampule.dev/run-id=phase08-pod-kill", selector)

    def test_cpu_pressure_and_dependency_response_faults_are_reversible(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "multi-service-dependency.yaml")
        scenario.document["faults"].append(
            {
                "type": "cpu_pressure",
                "description": "Constrain target CPU.",
                "startAfter": "10s",
                "duration": "20s",
            }
        )
        environment = plan_environment(scenario, run_id="phase08-faults").metadata

        with TemporaryDirectory() as artifact_dir:
            plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)

        self.assertEqual(
            [action.action_type for action in plan.actions],
            ["inject", "remove", "inject", "remove"],
        )
        self.assertIn("--type=strategic", plan.actions[0].command)
        self.assertIn("--type=strategic", plan.actions[2].command)
        dependency_patch = json.loads(plan.actions[0].command[-1])
        dependency_env = dependency_patch["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertIn({"name": "FAULT_STATUS", "value": "500"}, dependency_env)
        cpu_patch = json.loads(plan.actions[2].command[-1])
        resources = cpu_patch["spec"]["template"]["spec"]["containers"][0]["resources"]
        self.assertEqual(resources["limits"]["cpu"], "100m")

    def test_unknown_fault_target_is_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "multi-service-dependency.yaml")
        scenario.document["faults"][0]["target"] = "not-managed"
        environment = plan_environment(scenario, run_id="phase08-bad-target").metadata

        with TemporaryDirectory() as artifact_dir:
            with self.assertRaisesRegex(FaultPlanningError, "not a chamber-managed service"):
                plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)


class Phase09MultiServiceTests(unittest.TestCase):
    def test_multi_service_environment_deploys_dependency_workload(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "multi-service-dependency.yaml")

        plan = plan_environment(scenario, run_id="phase09-topology")

        self.assertEqual(
            [resource.role for resource in plan.metadata.service_resources],
            ["target", "dependency"],
        )
        self.assertIn("dependency.downstream-api.deployment", plan.metadata.resource_names)
        self.assertEqual(
            [manifest["kind"] for manifest in plan.manifests],
            ["Namespace", "Deployment", "Service", "Deployment", "Service"],
        )
        target_deployment = plan.manifests[1]
        dependency_deployment = plan.manifests[3]
        target_env = target_deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(target_env[0]["name"], "DOWNSTREAM_URL")
        self.assertIn("downstream-api-svc", target_env[0]["value"])
        dependency_container = dependency_deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(dependency_container["command"], ["python", "/app/server.py"])
        self.assertEqual(dependency_container["args"], ["--mode", "downstream", "--port", "8081"])

    def test_report_renders_dependency_agent_and_recovery_sections(self) -> None:
        report = ReportInput(
            title="Ampule Chamber Reliability Report",
            service=ServiceMetadata(
                name="sample-service",
                owner="platform-reliability",
                repository="local",
                commit="test",
            ),
            run=RunMetadata(
                run_id="phase09-report",
                test_date="2026-06-15",
                duration_seconds=60,
                namespace="chamber",
                provider="kind",
                lifecycle_state="completed",
            ),
            scenario=TestedScenario(
                scenario_id="multi-service-dependency-001",
                name="Target contains downstream dependency failure",
                path="scenarios/multi-service-dependency.yaml",
                traffic_tool="k6",
                max_virtual_users=15,
                fault_summary="dependency_errors",
            ),
            findings=(),
            evidence=(),
            reproduction=ReproductionDetails(commands=(), artifacts=()),
            retest_plan=(),
            cleanup_notes=(),
            limitations=(),
            agent_sections=(ReportSection(heading="Agent Analysis", lines=("Evidence-bound.",)),),
            dependency_graph=("sample-service -> downstream-api",),
            recovery_status=("Status: recovered.",),
        )

        markdown = render_markdown_report(report)

        self.assertIn("## Dependency Graph", markdown)
        self.assertIn("sample-service -> downstream-api", markdown)
        self.assertIn("## Agent Analysis", markdown)
        self.assertIn("## Recovery Status", markdown)


if __name__ == "__main__":
    unittest.main()
