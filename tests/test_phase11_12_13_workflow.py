from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import chamber.workflow as workflow
from chamber.agents import (
    AgentValidationError,
    ChamberAgentContext,
    deterministic_evidence_analyst_brief,
    deterministic_onboarding_draft,
    deterministic_run_supervisor_brief,
    deterministic_scenario_planner_brief,
    deterministic_traffic_chaos_recommendation,
    validate_evidence_bound_output,
)
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
            invalid["service"] = {"name": "target-service", "repo": str(root / "missing")}
            with self.assertRaisesRegex(WorkflowError, "service.repo does not exist"):
                validate_config(invalid)

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


if __name__ == "__main__":
    unittest.main()
