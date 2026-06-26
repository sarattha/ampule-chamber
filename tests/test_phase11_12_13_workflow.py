from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

import chamber.workflow as workflow
from chamber.agents import (
    AgentValidationError,
    ChamberAgentContext,
    EvidenceAnalystBrief,
    EvidenceCitation,
    OnboardingAgentDraft,
    ReportNarrative,
    RootCauseHypothesis,
    RunSupervisorBrief,
    ScenarioPlannerBrief,
    TrafficChaosRecommendation,
    deterministic_evidence_analyst_brief,
    deterministic_onboarding_draft,
    deterministic_run_supervisor_brief,
    deterministic_scenario_planner_brief,
    deterministic_traffic_chaos_recommendation,
    validate_evidence_bound_output,
)
from chamber.agents.sdk import OpenAIAgentsSdkRunner
from chamber.workflow import (
    WorkflowError,
    assess,
    config_to_onboarding_spec,
    infer_config,
    load_config,
    main,
    onboard_repository,
    plan_config,
    save_config,
    validate_config,
)


class Phase11GuidedWorkflowTests(unittest.TestCase):
    def test_onboard_rejects_missing_repository(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(WorkflowError, "repository path does not exist"):
                onboard_repository(Path(tmp) / "missing")

    def test_config_round_trip_converts_to_onboarding_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = infer_config(repo)

            save_config(config, config_path)
            loaded = load_config(config_path)
            spec = config_to_onboarding_spec(loaded)

        self.assertEqual(spec.service_name, "target-service")
        self.assertEqual(spec.manifest_paths, ("manifests/app.yaml",))
        self.assertEqual(spec.workload_roles[0].name, "target-service")
        self.assertEqual(spec.workload_roles[0].role, "target")
        self.assertEqual(spec.traffic.method, "GET")
        self.assertEqual(spec.traffic.entrypoint, "/health")
        self.assertEqual(spec.image_builds[0].image, "ampule/target-service:local")

    def test_config_conversion_supports_external_dependencies_and_followups(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["runtime"] = {
                "requiredEnv": ["API_TOKEN"],
                "secretEnv": ["API_TOKEN"],
                "config": {"LOG_LEVEL": "debug"},
            }
            config["dependencies"]["external"] = [
                {
                    "name": "payments",
                    "provider": "http",
                    "endpoint": "https://payments.example.invalid",
                    "requiredEnv": ["API_TOKEN"],
                    "timeoutSeconds": 15,
                }
            ]
            config["traffic"]["journeys"][0]["followUps"] = [
                {
                    "name": "status",
                    "type": "http_status",
                    "target": "/status/1",
                    "expected": "completed",
                    "serviceName": "target-service",
                }
            ]
            config_path = root / "chamber.yaml"
            save_config(config, config_path)

            spec = config_to_onboarding_spec(load_config(config_path))

        self.assertEqual(spec.required_env, ("API_TOKEN",))
        self.assertEqual(spec.secret_env, ("API_TOKEN",))
        self.assertEqual(spec.config_overrides, {"LOG_LEVEL": "debug"})
        self.assertEqual(spec.external_dependencies[0].name, "payments")
        self.assertEqual(spec.follow_up_checks[0].name, "status")

    def test_config_validation_reports_actionable_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)

            invalid = dict(config)
            invalid["kind"] = "Other"
            with self.assertRaisesRegex(WorkflowError, "kind must be ChamberConfig"):
                validate_config(invalid)

            invalid = dict(config)
            invalid["deployment"] = {**config["deployment"], "manifests": []}
            with self.assertRaisesRegex(WorkflowError, "manifests must not be empty"):
                validate_config(invalid)

            invalid = dict(config)
            invalid["agents"] = {"mode": "robot"}
            with self.assertRaisesRegex(WorkflowError, "agents.mode"):
                validate_config(invalid)

            invalid = dict(config)
            invalid["agents"] = {"mode": "offline", "exclude": ["unknown-agent"]}
            with self.assertRaisesRegex(WorkflowError, "unknown agent role"):
                validate_config(invalid)

            invalid = dict(config)
            invalid["service"] = {"name": "target-service", "repo": str(root / "missing")}
            with self.assertRaisesRegex(WorkflowError, "service.repo does not exist"):
                validate_config(invalid)

    def test_secret_like_runtime_config_is_rejected_before_planning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["runtime"]["config"] = {
                "LOG_LEVEL": "debug",
                "API_KEY": "plain-secret",
            }
            config_path = root / "chamber.yaml"
            save_config(config, config_path)

            with self.assertRaisesRegex(WorkflowError, "looks secret-like"):
                load_config(config_path)

            with self.assertRaisesRegex(WorkflowError, "runtime.secretEnv"):
                plan_config(config_path, run_dir=root / ".chamber/runs/secret-leak")

    def test_kubernetes_runtime_config_is_validated_and_recorded_in_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["runtime"].update(
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev-cluster",
                    "namespaceBase": "chamber-target-service",
                    "cleanup": True,
                    "prometheusUrl": "http://prometheus.example",
                    "trafficAccess": {
                        "mode": "port-forward",
                        "service": "target-service",
                        "servicePort": 8080,
                    },
                }
            )
            config["deployment"]["images"]["replacements"] = [
                {
                    "source": "registry.example/target-service:prod",
                    "target": "registry.example/target-service:20260618",
                }
            ]
            config_path = root / "chamber.yaml"
            run_dir = root / ".chamber/runs/kubernetes-config"
            save_config(config, config_path)

            plan_config(config_path, run_dir=run_dir)

            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["runtime"]["provider"], "kubernetes")
        self.assertEqual(plan["runtime"]["kubernetes_context"], "dev-cluster")
        self.assertEqual(plan["runtime"]["namespace"], plan["namespace"])
        self.assertTrue(plan["namespace"].startswith("chamber-target-service-"))
        self.assertEqual(plan["runtime"]["traffic_access"]["mode"], "port-forward")
        self.assertEqual(
            plan["runtime"]["image_replacements"],
            [
                {
                    "source": "registry.example/target-service:prod",
                    "target": "registry.example/target-service:20260618",
                }
            ],
        )
        rendered_plan = json.dumps(plan)
        self.assertIn("registry.example/target-service:20260618", rendered_plan)
        self.assertEqual(metadata["runtime"]["provider"], "kubernetes")

    def test_kubernetes_runtime_config_requires_context_and_traffic_access(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["runtime"]["provider"] = "kubernetes"
            config_path = root / "chamber.yaml"
            save_config(config, config_path)

            with self.assertRaisesRegex(WorkflowError, "runtime.kubernetesContext"):
                load_config(config_path)

            config["runtime"]["kubernetesContext"] = "dev-cluster"
            save_config(config, config_path)
            with self.assertRaisesRegex(WorkflowError, "runtime.trafficAccess"):
                load_config(config_path)

            config["runtime"]["trafficAccess"] = {"mode": "port-forward"}
            save_config(config, config_path)
            with self.assertRaisesRegex(WorkflowError, "service"):
                load_config(config_path)

            config["runtime"]["trafficAccess"] = {
                "mode": "ingress",
                "url": "https://service.example",
            }
            save_config(config, config_path)
            with self.assertRaisesRegex(WorkflowError, "mode must be one of"):
                load_config(config_path)

    def test_runtime_rejects_secret_like_top_level_values(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["runtime"]["provider"] = "local"
            config["runtime"]["apiToken"] = "plain-secret"
            config_path = root / "chamber.yaml"
            save_config(config, config_path)

            with self.assertRaisesRegex(WorkflowError, "looks secret-like"):
                load_config(config_path)

    def test_load_config_rejects_non_mapping_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "chamber.yaml"
            path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

            with self.assertRaisesRegex(WorkflowError, "config must be a YAML mapping"):
                load_config(path)

    def test_infer_config_handles_minimal_repository(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty_service"
            repo.mkdir()
            (repo / "service.yaml").write_text(
                "apiVersion: v1\nkind: ConfigMap\n",
                encoding="utf-8",
            )

            config = infer_config(repo)

        self.assertEqual(config["deployment"]["manifests"], ["service.yaml"])
        self.assertEqual(config["deployment"]["images"], {})
        self.assertEqual(config["deployment"]["workloads"][0]["name"], "empty-service")

    def test_infer_config_ignores_non_kubernetes_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "mixed-service"
            repo.mkdir()
            (repo / "contract.yaml").write_text(
                """\
apiVersion: chamber.ampule.dev/v1alpha1
kind: SampleServiceContract
metadata:
  name: mixed-service
""",
                encoding="utf-8",
            )
            (repo / "docker-compose.yml").write_text(
                """\
services:
  app:
    image: mixed-service:local
""",
                encoding="utf-8",
            )
            (repo / "service.yaml").write_text(
                """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: mixed-service-config
""",
                encoding="utf-8",
            )

            config = infer_config(repo)

        self.assertEqual(config["deployment"]["manifests"], ["service.yaml"])

    def test_plan_command_writes_standard_run_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            save_config(infer_config(repo), config_path)
            run_dir = root / ".chamber/runs/chamber-fixture"

            planned = plan_config(config_path, run_dir=run_dir)

            self.assertEqual(planned, run_dir)
            self.assertTrue((run_dir / "chamber.yaml").exists())
            self.assertTrue((run_dir / "plan.json").exists())
            self.assertTrue((run_dir / "run-metadata.json").exists())
            self.assertTrue(list((run_dir / "adapted-manifests").glob("01-namespace-*.yaml")))
            self.assertTrue((run_dir / "agent/onboarding-agent.json").exists())
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))

        rendered = json.dumps(plan)
        self.assertNotIn("registry.example/target-service:prod", rendered)
        self.assertIn("ampule/target-service:local", rendered)

    def test_cli_init_onboard_plan_report_sequence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            run_dir = root / ".chamber/runs/chamber-cli"

            self.assertEqual(main(["init", "--workspace", str(root / ".chamber")]), 0)
            self.assertEqual(
                main(["onboard", "--repo", str(repo), "--output", str(config_path)]),
                0,
            )
            self.assertEqual(
                main(["plan", "--config", str(config_path), "--run-dir", str(run_dir)]),
                0,
            )
            self.assertEqual(main(["report", "--run", str(run_dir)]), 0)

            report = (run_dir / "report.md").read_text(encoding="utf-8")

        self.assertIn("Ampule Chamber Reliability Report", report)
        self.assertIn("Onboarding Summary", report)

    def test_cli_run_delegates_to_existing_live_runner(self) -> None:
        with patch("chamber.orchestrator.live.run_cli", return_value=0) as run_cli:
            self.assertEqual(main(["run", "--scenario", "scenarios/baseline-health.yaml"]), 0)

        run_cli.assert_called_once_with(["run", "--scenario", "scenarios/baseline-health.yaml"])


class Phase12AgentPipelineTests(unittest.TestCase):
    def test_all_six_offline_agent_outputs_are_evidence_bound(self) -> None:
        context = ChamberAgentContext(
            scenario_id="target-service-assessment",
            scenario_path="chamber.yaml",
            service_name="target-service",
            run_id="run-1",
            evidence_ids=("plan", "local-assessment"),
            finding_ids=(),
            artifact_paths=("manifests/app.yaml",),
            missing_signals=("traces",),
        )
        outputs = (
            deterministic_onboarding_draft(context),
            deterministic_scenario_planner_brief(context),
            deterministic_run_supervisor_brief(context),
            deterministic_traffic_chaos_recommendation(context),
            deterministic_evidence_analyst_brief(context),
        )

        for output in outputs:
            validate_evidence_bound_output(
                output,
                available_evidence_ids={"plan", "local-assessment"},
            )

    def test_live_agent_mode_requires_api_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["agents"]["mode"] = "live"
            config_path = root / "chamber.yaml"
            save_config(config, config_path)

            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(AgentValidationError, "OPENAI_API_KEY"):
                    plan_config(config_path, run_dir=root / ".chamber/runs/live-agents")

    def test_live_agent_mode_calls_sdk_for_all_six_roles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["agents"]["mode"] = "live"
            config_path = root / "chamber.yaml"
            save_config(config, config_path)
            fake_runner = _FakeAgentsRunner()

            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
                with patch("chamber.workflow.OpenAIAgentsSdkRunner", return_value=fake_runner):
                    run_dir = plan_config(config_path, run_dir=root / ".chamber/runs/live-agents")

            names = [call["name"] for call in fake_runner.calls]
            for filename in (
                "onboarding-agent.json",
                "scenario-planner-agent.json",
                "run-supervisor-agent.json",
                "traffic-chaos-agent.json",
                "evidence-analyst-agent.json",
                "report-writer-agent.json",
            ):
                self.assertTrue((run_dir / "agent" / filename).exists(), filename)

        self.assertEqual(
            names,
            [
                "onboarding-agent",
                "scenario-planner-agent",
                "run-supervisor-agent",
                "traffic-chaos-agent",
                "evidence-analyst-agent",
                "report-writer-agent",
            ],
        )

    def test_agent_exclude_skips_selected_live_role(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["agents"] = {"mode": "live", "exclude": ["onboarding-agent"]}
            config_path = root / "chamber.yaml"
            save_config(config, config_path)
            fake_runner = _FakeAgentsRunner()

            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
                with patch("chamber.workflow.OpenAIAgentsSdkRunner", return_value=fake_runner):
                    run_dir = plan_config(config_path, run_dir=root / ".chamber/runs/live-agents")

            names = [call["name"] for call in fake_runner.calls]
            self.assertNotIn("onboarding-agent", names)
            self.assertFalse((run_dir / "agent/onboarding-agent.json").exists())
            self.assertTrue((run_dir / "agent/run-supervisor-agent.json").exists())
            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["agent_exclude"], ["onboarding-agent"])

    def test_sdk_default_model_matches_phase12_live_target(self) -> None:
        self.assertEqual(OpenAIAgentsSdkRunner().model, "gpt-5.4-mini")

    def test_agent_mode_off_skips_agent_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config["agents"]["mode"] = "off"
            config_path = root / "chamber.yaml"
            save_config(config, config_path)
            run_dir = root / ".chamber/runs/off-agents"

            plan_config(config_path, run_dir=run_dir)

            self.assertFalse(list((run_dir / "agent").glob("*.json")))

    def test_agent_mode_off_clears_stale_agent_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config = infer_config(repo)
            config_path = root / "chamber.yaml"
            run_dir = root / ".chamber/runs/stale-agents"
            save_config(config, config_path)
            plan_config(config_path, run_dir=run_dir)
            self.assertTrue(list((run_dir / "agent").glob("*.json")))

            config["agents"]["mode"] = "off"
            save_config(config, config_path)
            plan_config(config_path, run_dir=run_dir)
            report = run_dir / "report.md"
            if report.exists():
                report.unlink()
            workflow.render_report_from_run(run_dir)
            rendered = report.read_text(encoding="utf-8")

        self.assertFalse(list((run_dir / "agent").glob("*.json")))
        self.assertNotIn("## Onboarding Agent", rendered)


class Phase13OneCommandAssessmentTests(unittest.TestCase):
    def test_assess_repo_writes_core_artifacts_and_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            with patch(
                "chamber.workflow._current_kube_context",
                return_value="kind-ampule-chamber",
            ):
                with patch("chamber.workflow.Path.cwd", return_value=root):
                    run_dir = assess(repo=repo, config=None, resume=None)

            expected = (
                "chamber.yaml",
                "plan.json",
                "run-metadata.json",
                "findings.json",
                "report.md",
            )
            for filename in expected:
                self.assertTrue((run_dir / filename).exists(), filename)
            self.assertTrue((run_dir / "evidence/local-assessment.json").exists())
            self.assertTrue((run_dir / "agent/report-writer-agent.json").exists())
            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
            report = (run_dir / "report.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["stage"], "assessed")
        self.assertTrue(metadata["cleanup_performed"])
        self.assertIn("uv run ampule-chamber assess --resume", report)

    def test_resume_regenerates_report_without_rerunning_assessment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            with patch("chamber.workflow._current_kube_context", return_value=None):
                with patch("chamber.workflow.Path.cwd", return_value=root):
                    run_dir = assess(repo=repo, config=None, resume=None)
            (run_dir / "report.md").unlink()

            resumed = assess(repo=None, config=None, resume=run_dir)

            self.assertEqual(resumed, run_dir)
            self.assertTrue((run_dir / "report.md").exists())
            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))

        self.assertIn("resumed_at", metadata)

    def test_report_and_resume_do_not_require_original_source_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            with patch("chamber.workflow._current_kube_context", return_value=None):
                with patch("chamber.workflow.Path.cwd", return_value=root):
                    run_dir = assess(repo=repo, config=None, resume=None)
            shutil.rmtree(repo)
            (run_dir / "report.md").unlink()

            workflow.render_report_from_run(run_dir)
            assess(repo=None, config=None, resume=run_dir)

            report = (run_dir / "report.md").read_text(encoding="utf-8")

        self.assertIn("- Commit: unknown", report)
        self.assertIn("Ampule Chamber Reliability Report", report)

    def test_assess_refuses_unsafe_kubernetes_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)

            with patch("chamber.workflow._current_kube_context", return_value="aks-prod-east"):
                with patch("chamber.workflow.Path.cwd", return_value=root):
                    with self.assertRaisesRegex(WorkflowError, "unsafe Kubernetes context"):
                        assess(repo=repo, config=None, resume=None)

    def test_assess_config_and_argument_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            save_config(infer_config(repo), config_path)

            with self.assertRaisesRegex(WorkflowError, "only --mode local"):
                assess(repo=None, config=config_path, resume=None, mode="remote")
            with self.assertRaisesRegex(WorkflowError, "requires --repo"):
                assess(repo=None, config=None, resume=None)
            with patch("chamber.workflow._current_kube_context", return_value=None):
                with patch("chamber.workflow.Path.cwd", return_value=root):
                    run_dir = assess(
                        repo=None,
                        config=config_path,
                        resume=None,
                        agents_mode="off",
                    )

            self.assertFalse(list((run_dir / "agent").glob("*.json")))

    def test_kubernetes_assess_fails_preflight_before_apply(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _kubernetes_config(repo)
            save_config(config, config_path)
            runner = _FakeKubernetesRunner(deny_events=True)

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with self.assertRaisesRegex(WorkflowError, "preflight failed"):
                    workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url="http://prometheus.example",
                        runner=runner,
                    )

        self.assertFalse(any(command[:2] == ("kubectl", "apply") for command in runner.commands))

    def test_kubernetes_assess_writes_live_artifacts_with_fake_runner(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            save_config(_kubernetes_config(repo), config_path)
            runner = _FakeKubernetesRunner()

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with patch(
                    "chamber.workflow._execute_kubernetes_traffic",
                    return_value={
                        "success": True,
                        "command": ["k6", "run", "traffic.js"],
                        "exit_status": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "summary_path": "evidence/k6-summary.json",
                    },
                ):
                    run_dir = workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url="http://prometheus.example",
                        runner=runner,
                    )

            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
            preflight = json.loads(
                (run_dir / "evidence/preflight.json").read_text(encoding="utf-8")
            )
            report = (run_dir / "report.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["stage"], "assessed")
        self.assertEqual(metadata["mode"], "kubernetes")
        self.assertTrue(metadata["cleanup_performed"])
        self.assertTrue(preflight["ready"])
        self.assertTrue((run_dir / "evidence/kubernetes-commands.json").exists())
        self.assertTrue((run_dir / "findings.json").exists())
        self.assertIn("Provider: kubernetes", report)
        self.assertTrue(
            any(command[0] == "kubectl" and "apply" in command for command in runner.commands)
        )
        self.assertTrue(any("delete" in command for command in runner.commands))
        self.assertTrue(any("top" in command for command in runner.commands))

    def test_kubernetes_attach_assess_discovers_existing_resources_without_apply_or_delete(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _attach_kubernetes_config(repo)
            save_config(config, config_path)
            runner = _attach_runner()

            def fake_traffic(**kwargs: object) -> dict[str, object]:
                run_dir_arg = cast(Path, kwargs["run_dir"])
                (run_dir_arg / "evidence/k6-summary.json").write_text(
                    json.dumps({"metrics": {"checks": {"passes": 1, "fails": 0}}}) + "\n",
                    encoding="utf-8",
                )
                return {
                    "success": True,
                    "command": ["k6", "run", "traffic.js"],
                    "exit_status": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "summary_path": str(run_dir_arg / "evidence/k6-summary.json"),
                }

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with patch(
                    "chamber.workflow._execute_kubernetes_traffic",
                    side_effect=fake_traffic,
                ):
                    run_dir = workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url=None,
                        runner=runner,
                    )

            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
            report = (run_dir / "report.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["runtime_mode"], "attach")
        self.assertEqual(metadata["namespace"], "translation-test")
        self.assertFalse(metadata["cleanup_performed"])
        self.assertEqual(plan["runtime"]["mode"], "attach")
        self.assertTrue((run_dir / "evidence/attach-discovery.json").exists())
        self.assertTrue((run_dir / "evidence/pre-test-state.json").exists())
        self.assertIn("mode: attach", report)
        self.assertIn("attach-discovery", report)
        self.assertFalse(any("apply" in command for command in runner.commands))
        self.assertFalse(
            any(
                command[:6]
                == ("kubectl", "--context", "dev-cluster", "-n", "translation-test", "delete")
                for command in runner.commands
            )
        )
        self.assertTrue(
            any(
                command[:7]
                == (
                    "kubectl",
                    "--context",
                    "dev-cluster",
                    "-n",
                    "translation-test",
                    "get",
                    "events",
                )
                and "involvedObject.name=translation-service-abc" in command
                for command in runner.commands
            )
        )

    def test_kubernetes_attach_faults_require_allow_list_label(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _attach_kubernetes_config(repo)
            cast(dict[str, Any], config["runtime"])["faults"] = [{"type": "pod_kill"}]
            save_config(config, config_path)
            runner = _attach_runner(allow_faults=False)

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with self.assertRaisesRegex(WorkflowError, "allow-faults"):
                    workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url=None,
                        runner=runner,
                    )

        self.assertFalse(
            any(
                command[:6]
                == ("kubectl", "--context", "dev-cluster", "-n", "translation-test", "delete")
                for command in runner.commands
            )
        )

    def test_kubernetes_attach_pod_kill_fault_waits_for_deployment_availability(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _attach_kubernetes_config(repo)
            cast(dict[str, Any], config["runtime"])["faults"] = [{"type": "pod_kill"}]
            save_config(config, config_path)
            runner = _attach_runner(allow_faults=True)

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with patch(
                    "chamber.workflow._execute_kubernetes_traffic",
                    return_value={
                        "success": True,
                        "command": ["k6", "run", "traffic.js"],
                        "exit_status": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "summary_path": "",
                    },
                ):
                    run_dir = workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url=None,
                        runner=runner,
                    )

            rollback = json.loads((run_dir / "evidence/rollback.json").read_text(encoding="utf-8"))

        self.assertTrue(rollback["verified"])
        self.assertEqual(rollback["actions"][0]["type"], "pod_kill")
        self.assertTrue(rollback["actions"][0]["restored"])
        self.assertIn(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "delete",
                "pod",
                "translation-service-abc",
            ),
            runner.commands,
        )
        self.assertIn(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "wait",
                "--for=condition=available",
                "deployment/translation-service",
                "--timeout=180s",
            ),
            runner.commands,
        )

    def test_kubernetes_attach_deployment_scale_fault_restores_original_replicas(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _attach_kubernetes_config(repo)
            cast(dict[str, Any], config["runtime"])["faults"] = [
                {"type": "deployment_scale", "replicas": 0}
            ]
            save_config(config, config_path)
            runner = _attach_runner(allow_faults=True)

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with patch(
                    "chamber.workflow._execute_kubernetes_traffic",
                    return_value={
                        "success": True,
                        "command": ["k6", "run", "traffic.js"],
                        "exit_status": 0,
                        "stdout": "ok",
                        "stderr": "",
                        "summary_path": "",
                    },
                ):
                    run_dir = workflow._assess_kubernetes_config(
                        config_path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url=None,
                        runner=runner,
                    )

            rollback = json.loads((run_dir / "evidence/rollback.json").read_text(encoding="utf-8"))

        self.assertTrue(rollback["verified"])
        self.assertIn(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "scale",
                "deployment/translation-service",
                "--replicas=0",
            ),
            runner.commands,
        )
        self.assertIn(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "scale",
                "deployment/translation-service",
                "--replicas=2",
            ),
            runner.commands,
        )

    def test_kubernetes_k6_script_supports_multiple_memory_journeys(self) -> None:
        script = workflow._k6_script_for_journeys(
            (
                {
                    "name": "memory-health-ramp",
                    "method": "GET",
                    "path": "/health",
                    "expectedStatus": 200,
                    "stages": [{"duration": "5s", "targetVus": 1}],
                },
                {
                    "name": "memory-large-text-admission",
                    "method": "POST",
                    "path": "/translations",
                    "expectedStatus": 202,
                    "iterations": 2,
                    "body": {
                        "task_id": "memory-large-text",
                        "text": "Ampule memory payload. ",
                        "language_target": "Thai",
                    },
                    "textBytes": 4096,
                },
                {
                    "name": "memory-backpressure-read",
                    "method": "GET",
                    "path": "/relayna/runtime/backpressure",
                    "expectedStatus": 200,
                    "iterations": 1,
                },
            ),
            base_url="http://127.0.0.1:18891",
        )

        self.assertIn("memory_health_ramp", script)
        self.assertIn("memory_large_text_admission", script)
        self.assertIn("memory_backpressure_read", script)
        self.assertIn('"textBytes": 4096', script)
        self.assertIn("body.task_id = `${body.task_id}-${__VU}-${__ITER}-${Date.now()}`", script)
        self.assertIn('"startTime": "5s"', script)

    def test_kubernetes_k6_script_uses_unique_function_names_for_collisions(self) -> None:
        script = workflow._k6_script_for_journeys(
            (
                {
                    "name": "health-check",
                    "method": "GET",
                    "path": "/health",
                    "expectedStatus": 200,
                    "iterations": 1,
                },
                {
                    "name": "health_check",
                    "method": "GET",
                    "path": "/health",
                    "expectedStatus": 200,
                    "iterations": 1,
                },
            ),
            base_url="http://127.0.0.1:18891",
        )

        self.assertIn("export function health_check_1()", script)
        self.assertIn("export function health_check_2()", script)
        self.assertIn('"health_check_1": {"body": null', script)
        self.assertIn('"health_check_2": {"body": null', script)
        self.assertIn("runJourney('health_check_1')", script)
        self.assertIn("runJourney('health_check_2')", script)

    def test_prometheus_query_url_rejects_non_http_urls(self) -> None:
        result = workflow._prometheus_query(
            "file:///etc/passwd",
            'container_memory_working_set_bytes{namespace="chamber-test"}',
        )

        self.assertFalse(result["ok"])
        self.assertIn("HTTP(S) URL", result["error"])
        self.assertEqual(result["series"], [])

    def test_prometheus_query_url_builds_http_path(self) -> None:
        url = workflow._prometheus_query_url(
            "https://prometheus.example/base/",
            'container_memory_working_set_bytes{namespace="chamber-test"}',
        )

        self.assertTrue(url.startswith("https://prometheus.example/base/api/v1/query?"))
        self.assertIn("container_memory_working_set_bytes", url)
        self.assertIn("namespace%3D%22chamber-test%22", url)

    def test_local_assessment_keeps_live_execution_missing_signal(self) -> None:
        self.assertEqual(
            workflow._agent_missing_signals(
                stage="assess",
                evidence_ids=("plan", "local-assessment"),
            ),
            ("live Kubernetes execution",),
        )

    def test_runtime_assessment_clears_live_execution_missing_signal(self) -> None:
        self.assertEqual(
            workflow._agent_missing_signals(
                stage="assess",
                evidence_ids=("plan", "kubernetes-commands"),
            ),
            (),
        )

    def test_kubernetes_live_agents_receive_runtime_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            config = _kubernetes_config(repo)
            agents = cast(dict[str, Any], config["agents"])
            agents["mode"] = "live"
            save_config(config, config_path)
            runner = _FakeKubernetesRunner()
            fake_agents = _FakeAgentsRunner()

            def fake_traffic(**kwargs: object) -> dict[str, object]:
                run_dir_arg = cast(Path, kwargs["run_dir"])
                (run_dir_arg / "evidence/k6-summary.json").write_text(
                    json.dumps(
                        {
                            "metrics": {
                                "checks": {"passes": 1, "fails": 0, "value": 1},
                                "http_req_failed": {"fails": 1, "passes": 0, "value": 0},
                                "http_reqs": {"count": 1, "rate": 42.0},
                            }
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {
                    "success": True,
                    "command": ["k6", "run", "traffic.js"],
                    "exit_status": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "summary_path": "evidence/k6-summary.json",
                }

            with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
                with patch("chamber.workflow.OpenAIAgentsSdkRunner", return_value=fake_agents):
                    with patch("chamber.workflow.Path.cwd", return_value=root):
                        with patch(
                            "chamber.workflow._execute_kubernetes_traffic",
                            side_effect=fake_traffic,
                        ):
                            run_dir = workflow._assess_kubernetes_config(
                                config_path,
                                agents_mode=None,
                                context="dev-cluster",
                                prometheus_url="http://prometheus.example",
                                runner=runner,
                            )

        self.assertEqual(len(fake_agents.calls), 12)
        assess_calls = fake_agents.calls[6:]
        for call in assess_calls:
            payload = json.loads(str(call["input_text"]))
            context = payload["context"]
            self.assertEqual(context["missing_signals"], [])
            self.assertIn("k6-summary", context["evidence_ids"])
            self.assertIn("kubernetes-commands", context["evidence_ids"])
            self.assertIn("traffic success: True", context["evidence_summaries"])
            self.assertIn("cleanup performed: True", context["evidence_summaries"])
            self.assertTrue(any("plan:" in item for item in context["evidence_details"]))
            self.assertTrue(
                any("kubernetes-commands:" in item for item in context["evidence_details"])
            )
            self.assertTrue(any("k6-summary:" in item for item in context["evidence_details"]))
            details = "\n".join(context["evidence_details"])
            summaries = "\n".join(context["evidence_summaries"])
            self.assertIn('"derived_failed_http_requests": 0', details)
            self.assertIn("k6 derived failed http requests: 0", summaries)
            self.assertNotIn('"http_req_failed"', details)
        self.assertTrue((run_dir / "agent/run-supervisor-agent.json").exists())

    def test_kubernetes_assess_persists_failure_evidence_before_reraising(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            config_path = root / "chamber.yaml"
            run_dir = root / ".chamber/runs/kubernetes-failure"
            save_config(_kubernetes_config(repo), config_path)
            runner = _FakeKubernetesRunner(fail_apply=True)

            with patch("chamber.workflow.Path.cwd", return_value=root):
                with patch("chamber.workflow._new_run_dir", return_value=run_dir):
                    with self.assertRaisesRegex(WorkflowError, "apply adapted manifest"):
                        workflow._assess_kubernetes_config(
                            config_path,
                            agents_mode="off",
                            context="dev-cluster",
                            prometheus_url="http://prometheus.example",
                            runner=runner,
                        )

            metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
            commands = json.loads(
                (run_dir / "evidence/kubernetes-commands.json").read_text(encoding="utf-8")
            )
            findings_exists = (run_dir / "findings.json").exists()
            report_exists = (run_dir / "report.md").exists()

        self.assertEqual(metadata["stage"], "failed")
        self.assertIn("apply adapted manifest", metadata["error"])
        self.assertTrue(metadata["cleanup_performed"])
        self.assertTrue(findings_exists)
        self.assertTrue(report_exists)
        self.assertTrue(
            any("apply failed" in command["stderr"] for command in commands["commands"])
        )
        self.assertTrue(any("delete" in command["command"] for command in commands["commands"]))

    def test_kubernetes_command_recording_redacts_secret_like_output(self) -> None:
        commands: list[dict[str, object]] = []
        command = ("kubectl", "--context", "dev-cluster", "logs", "deployment/app")
        runner = _FakeKubernetesRunner(
            responses={
                command: _completed(
                    command,
                    stdout="TOKEN=plain-secret\nnormal line\n",
                    stderr="PASSWORD=plain-secret\n",
                )
            }
        )

        workflow._run_kubernetes_recorded(runner, command, commands)

        rendered = json.dumps(commands)
        self.assertIn("<redacted>", rendered)
        self.assertIn("normal line", rendered)
        self.assertNotIn("plain-secret", rendered)

    def test_resume_rejects_non_run_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(WorkflowError, "not a resumable"):
                assess(repo=None, config=None, resume=Path(tmp))

    def test_current_kube_context_probe_handles_missing_and_failed_kubectl(self) -> None:
        with patch("chamber.workflow.shutil.which", return_value=None):
            self.assertIsNone(workflow._current_kube_context())

        failed = workflow.subprocess.CompletedProcess(
            args=("kubectl",),
            returncode=1,
            stdout="",
            stderr="no context",
        )
        with patch("chamber.workflow.shutil.which", return_value="/usr/bin/kubectl"):
            with patch("chamber.workflow.subprocess.run", return_value=failed):
                self.assertIsNone(workflow._current_kube_context())

        succeeded = workflow.subprocess.CompletedProcess(
            args=("kubectl",),
            returncode=0,
            stdout="kind-ampule-chamber\n",
            stderr="",
        )
        with patch("chamber.workflow.shutil.which", return_value="/usr/bin/kubectl"):
            with patch("chamber.workflow.subprocess.run", return_value=succeeded):
                self.assertEqual(workflow._current_kube_context(), "kind-ampule-chamber")

    def test_json_reader_rejects_non_object_payloads(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(WorkflowError, "must contain a JSON object"):
                workflow._read_json(path)


def _fixture_repo(root: Path) -> Path:
    repo = root / "target-service"
    (repo / "manifests").mkdir(parents=True)
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "manifests/app.yaml").write_text(
        """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: target-service
spec:
  selector:
    matchLabels:
      app: target-service
  template:
    metadata:
      labels:
        app: target-service
    spec:
      containers:
        - name: app
          image: registry.example/target-service:prod
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: target-service
spec:
  selector:
    app: target-service
  ports:
    - name: http
      port: 8080
""",
        encoding="utf-8",
    )
    return repo


def _kubernetes_config(repo: Path) -> dict[str, object]:
    config = infer_config(repo)
    runtime = config["runtime"]
    runtime.update(
        {
            "provider": "kubernetes",
            "kubernetesContext": "dev-cluster",
            "namespaceBase": "chamber-target-service",
            "cleanup": True,
            "prometheusUrl": "http://prometheus.example",
            "trafficAccess": {
                "mode": "port-forward",
                "service": "target-service",
                "servicePort": 8080,
            },
        }
    )
    config["agents"]["mode"] = "off"
    config["deployment"]["images"]["replacements"] = [
        {
            "source": "registry.example/target-service:prod",
            "target": "registry.example/target-service:20260618",
        }
    ]
    return config


def _attach_kubernetes_config(repo: Path) -> dict[str, object]:
    config = infer_config(repo)
    config["deployment"]["manifests"] = []
    config["deployment"]["services"] = [{"name": "translation-service", "port": 8080}]
    config["deployment"]["workloads"] = [
        {"name": "translation-service", "role": "target", "kind": "Deployment"}
    ]
    runtime = config["runtime"]
    runtime.update(
        {
            "provider": "kubernetes",
            "mode": "attach",
            "kubernetesContext": "dev-cluster",
            "namespace": "translation-test",
            "cleanup": False,
            "trafficAccess": {
                "mode": "port-forward",
                "service": "translation-service",
                "servicePort": 8080,
            },
        }
    )
    config["agents"]["mode"] = "off"
    return config


def _attach_runner(*, allow_faults: bool = False) -> _FakeKubernetesRunner:
    label_value = "true" if allow_faults else "false"
    responses = {
        (
            "kubectl",
            "--context",
            "dev-cluster",
            "get",
            "namespace",
            "translation-test",
            "-o",
            "json",
        ): _completed(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "get",
                "namespace",
                "translation-test",
                "-o",
                "json",
            ),
            stdout=json.dumps(
                {
                    "metadata": {
                        "name": "translation-test",
                        "labels": {"chamber.ampule.dev/allow-faults": label_value},
                    }
                }
            ),
        ),
        (
            "kubectl",
            "--context",
            "dev-cluster",
            "-n",
            "translation-test",
            "get",
            "deployment/translation-service",
            "-o",
            "json",
        ): _completed(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "get",
                "deployment/translation-service",
                "-o",
                "json",
            ),
            stdout=json.dumps(
                {
                    "metadata": {
                        "name": "translation-service",
                        "labels": {"app": "translation-service"},
                    },
                    "spec": {
                        "replicas": 2,
                        "selector": {"matchLabels": {"app": "translation-service"}},
                    },
                }
            ),
        ),
        (
            "kubectl",
            "--context",
            "dev-cluster",
            "-n",
            "translation-test",
            "get",
            "service",
            "translation-service",
            "-o",
            "json",
        ): _completed(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "get",
                "service",
                "translation-service",
                "-o",
                "json",
            ),
            stdout=json.dumps(
                {
                    "metadata": {"name": "translation-service"},
                    "spec": {
                        "selector": {"app": "translation-service"},
                        "ports": [{"port": 8080, "targetPort": 8080}],
                    },
                }
            ),
        ),
        (
            "kubectl",
            "--context",
            "dev-cluster",
            "-n",
            "translation-test",
            "get",
            "endpoints",
            "translation-service",
            "-o",
            "json",
        ): _completed(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "get",
                "endpoints",
                "translation-service",
                "-o",
                "json",
            ),
            stdout=json.dumps({"subsets": [{"addresses": [{"ip": "10.0.0.10"}]}]}),
        ),
        (
            "kubectl",
            "--context",
            "dev-cluster",
            "-n",
            "translation-test",
            "get",
            "pods",
            "-l",
            "app=translation-service",
            "-o",
            "json",
        ): _completed(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "-n",
                "translation-test",
                "get",
                "pods",
                "-l",
                "app=translation-service",
                "-o",
                "json",
            ),
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "translation-service-abc",
                                "labels": {"app": "translation-service"},
                            },
                            "status": {"phase": "Running", "containerStatuses": []},
                        }
                    ]
                }
            ),
        ),
    }
    return _FakeKubernetesRunner(responses=responses)


class _FakeKubernetesRunner:
    def __init__(
        self,
        responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]] | None = None,
        *,
        deny_events: bool = False,
        fail_apply: bool = False,
    ) -> None:
        self.responses = responses or {}
        self.deny_events = deny_events
        self.fail_apply = fail_apply
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command in self.responses:
            return self.responses[command]
        if self.fail_apply and command[0] == "kubectl" and "apply" in command:
            return _completed(command, returncode=1, stderr="apply failed\n")
        if command == ("kubectl", "config", "current-context"):
            return _completed(command, stdout="dev-cluster\n")
        if self.deny_events and command[5:8] == ("get", "events", "-n"):
            return _completed(command, stdout="no\n")
        if "can-i" in command:
            return _completed(command, stdout="yes\n")
        if command[:4] == ("kubectl", "--context", "dev-cluster", "version"):
            return _completed(command, stdout='{"serverVersion":{"gitVersion":"v1.30.0"}}\n')
        return _completed(command, stdout="ok\n")


def _completed(
    command: tuple[str, ...],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class _FakeAgentsRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_structured(
        self,
        *,
        name: str,
        instructions: str,
        input_text: str,
        output_type: type[object],
    ) -> object:
        self.calls.append(
            {
                "name": name,
                "instructions": instructions,
                "input_text": input_text,
                "output_type": output_type,
            }
        )
        citation = EvidenceCitation(evidence_id="plan", usage="live test")
        if output_type is OnboardingAgentDraft:
            return OnboardingAgentDraft(
                service_name="target-service",
                assumptions=("read-only repository inspection",),
                manifest_paths=("manifests/app.yaml",),
                workload_roles=("target",),
                citations=(citation,),
                limitations=(),
            )
        if output_type is ScenarioPlannerBrief:
            return ScenarioPlannerBrief(
                scenario_id="target-service-assessment",
                planned_scenarios=("baseline",),
                required_evidence=("pod_status",),
                safety_constraints=("chamber-owned resources only",),
                citations=(citation,),
                limitations=(),
            )
        if output_type is RunSupervisorBrief:
            return RunSupervisorBrief(
                run_id="live-agents",
                status="ready",
                blockers=(),
                readiness_notes=("readiness checks are bounded",),
                citations=(citation,),
                limitations=(),
            )
        if output_type is TrafficChaosRecommendation:
            return TrafficChaosRecommendation(
                scenario_id="target-service-assessment",
                traffic_profiles=("baseline-health",),
                fault_profiles=("none",),
                safety_constraints=("approved plan only",),
                citations=(citation,),
                limitations=(),
            )
        if output_type is EvidenceAnalystBrief:
            return EvidenceAnalystBrief(
                scenario_id="target-service-assessment",
                observed_facts=("plan evidence was supplied",),
                hypotheses=(
                    RootCauseHypothesis(
                        summary="no unsupported hypothesis",
                        confidence="low",
                        evidence_ids=("plan",),
                        follow_up_checks=("collect live evidence",),
                    ),
                ),
                citations=(citation,),
                limitations=(),
            )
        if output_type is ReportNarrative:
            return ReportNarrative(
                summary="Report narrative is bounded by supplied evidence.",
                recommendations=("Rerun after remediation.",),
                evidence_ids=("plan",),
                limitations=(),
            )
        raise AssertionError(f"unexpected output_type {output_type}")


if __name__ == "__main__":
    unittest.main()
