from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from chamber.contracts.scenario import load_scenario
from chamber.environment import EnvironmentMetadata
from chamber.load import plan_traffic
from chamber.onboarding import build_tara2_onboarding_plan, validate_tara2_environment
from chamber.report import (
    ReportInput,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)

ROOT = Path(__file__).resolve().parents[1]
TARA2_REPO = Path("/Users/jobz/Works/tara2_translation_service")


class Phase10OnboardingTests(unittest.TestCase):
    def test_tara2_onboarding_plan_adapts_real_repo_without_secret_values(self) -> None:
        if not TARA2_REPO.exists():
            self.skipTest("Tara2 repository is not available")

        plan = build_tara2_onboarding_plan(
            repo_path=TARA2_REPO,
            run_id="phase10-test",
            env={
                "LLM_API_KEY": "sk-secret",
                "LLM_BACKEND": "openai",
                "LLM_ENDPOINT": "https://api.openai.com/v1",
                "MODEL_NAME": "gpt-4.1-mini",
            },
        )

        self.assertEqual(plan.service_name, "tara2-translation-service")
        self.assertEqual(plan.repository_ref, "current-working-tree")
        self.assertFalse(plan.blockers)
        self.assertEqual(
            [item.name for item in plan.image_builds],
            ["translation-api", "translation-worker"],
        )
        self.assertIn("docker/Dockerfile.api", plan.image_builds[0].dockerfile)
        self.assertIn("docker/Dockerfile.worker", plan.image_builds[1].dockerfile)
        self.assertIn("Namespace", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("ConfigMap", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("Secret", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("Deployment", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("Service", [manifest["kind"] for manifest in plan.manifests])
        rendered = str(plan.manifests) + str(plan.redacted_config)
        self.assertNotIn("sk-secret", rendered)
        self.assertIn("<redacted", rendered)
        self.assertTrue(
            any(item.name == "LLM_API_KEY" and item.secret for item in plan.redacted_config)
        )
        self.assertTrue(any(check.name == "redis-ping" for check in plan.readiness_checks))
        self.assertTrue(any(check.name == "rabbitmq-endpoints" for check in plan.readiness_checks))
        self.assertEqual(plan.traffic_journey["method"], "POST")
        self.assertEqual(plan.traffic_journey["expectedStatus"], 202)

    def test_missing_openai_key_is_reported_as_live_blocker(self) -> None:
        self.assertEqual(validate_tara2_environment({}), ("LLM_API_KEY",))
        if not TARA2_REPO.exists():
            self.skipTest("Tara2 repository is not available")

        plan = build_tara2_onboarding_plan(
            repo_path=TARA2_REPO,
            run_id="phase10-missing-key",
            env={},
        )

        self.assertEqual(
            plan.blockers,
            ("LLM_API_KEY is required for live OpenAI translation evidence",),
        )
        key_entry = next(item for item in plan.redacted_config if item.name == "LLM_API_KEY")
        self.assertEqual(key_entry.value, "<missing>")

    def test_tara2_scenario_generates_post_k6_script(self) -> None:
        scenario = load_scenario(ROOT / "scenarios/tara2-text-translation.yaml")
        environment = EnvironmentMetadata(
            run_id="phase10-traffic",
            scenario_id=scenario.scenario_id,
            provider="kind",
            source_provider="docker",
            namespace="chamber-tara2",
            labels={"chamber.ampule.dev/run-id": "phase10-traffic"},
            resource_names={"service": "translation-service"},
            readiness_checks=(),
            cleanup_selectors={"chamber.ampule.dev/run-id": "phase10-traffic"},
            service_resources=(),
        )

        with TemporaryDirectory() as artifact_dir:
            plan = plan_traffic(scenario, environment=environment, artifact_dir=artifact_dir)

        self.assertEqual(plan.method, "POST")
        self.assertEqual(plan.expected_status, 202)
        self.assertIsNotNone(plan.request_body)
        assert plan.request_body is not None
        self.assertEqual(plan.request_body["task_id"], "ampule-phase10-text")
        self.assertIn("http.post", plan.script)
        self.assertIn("JSON.stringify(payload)", plan.script)
        self.assertIn("status matches expected", plan.script)
        self.assertIn("/translations", plan.target_url)

    def test_report_renders_onboarding_sections(self) -> None:
        report = ReportInput(
            title="Ampule Chamber Reliability Report",
            service=ServiceMetadata(
                name="tara2-translation-service",
                owner="platform-reliability",
                repository=str(TARA2_REPO),
                commit="current-working-tree",
            ),
            run=RunMetadata(
                run_id="phase10-report",
                test_date="2026-06-15",
                duration_seconds=120,
                namespace="chamber-tara2",
                provider="kind",
                lifecycle_state="completed",
            ),
            scenario=TestedScenario(
                scenario_id="tara2-text-translation-001",
                name="Tara2 text translation through OpenAI",
                path="scenarios/tara2-text-translation.yaml",
                traffic_tool="k6",
                max_virtual_users=1,
                fault_summary="none",
            ),
            findings=(),
            evidence=(),
            reproduction=ReproductionDetails(commands=(), artifacts=()),
            retest_plan=("Rerun with the same text payload and OpenAI env vars.",),
            cleanup_notes=("Delete chamber-owned Tara2 namespace after the run.",),
            limitations=("Document-service path is intentionally ignored.",),
            onboarding_summary=("Tara2 current working tree used read-only.",),
            adapted_workloads=(
                "translation-service API, translation-worker, rabbitmq, redis-master",
            ),
            redacted_config=("LLM_API_KEY=<redacted-present>",),
            external_dependencies=("openai -> https://api.openai.com/v1",),
        )

        markdown = render_markdown_report(report)

        self.assertIn("## Onboarding Summary", markdown)
        self.assertIn("## Adapted Workloads", markdown)
        self.assertIn("## Redacted Configuration", markdown)
        self.assertIn("## External Dependencies", markdown)
        self.assertNotIn("sk-", markdown)


if __name__ == "__main__":
    unittest.main()
