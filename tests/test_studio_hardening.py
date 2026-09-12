"""Regression coverage for the Studio integration assessment findings."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from chamber import workflow
from chamber.agents.contracts import (
    AgentValidationError,
    ReportNarrative,
    validate_evidence_bound_output,
)
from chamber.application.results import _has_evidence_content, build_assessment_result
from chamber.control_plane.jobs import OUTPUT_LIMIT, AssessmentJob, AssessmentJobManager
from chamber.control_plane.server import _run_event_stream, create_app
from chamber.runs import initialize_run_record, refresh_evidence_manifest, write_json_atomic
from tests.evidence_fixtures import evidence_payload
from tests.test_decision_results import _config, _metadata, _write_required_evidence


class StudioHardeningTests(unittest.TestCase):
    def test_required_signals_and_empty_artifacts_fail_closed(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_required_evidence(run)
            config = _config()
            for signal in (
                "queue_depth",
                "traces",
                "cpu_throttling",
                "dependency_health",
                "future_signal",
            ):
                config["scenario"]["requiredSignals"] = [signal]
                result = build_assessment_result(
                    run, config=config, metadata=_metadata(run.name), findings=()
                )
                self.assertFalse(result["conclusive"])
                self.assertIsNone(result["readiness_score"])
                self.assertIn(f"signal:{signal}", result["missing_evidence_ids"])
            config["scenario"]["requiredSignals"] = []
            for content in ("{}", "[]", "null", "invalid", '{"metrics": {}}'):
                (run / "evidence/k6-summary.json").write_text(content)
                refresh_evidence_manifest(run)
                result = build_assessment_result(
                    run, config=config, metadata=_metadata(run.name), findings=()
                )
                self.assertIn("k6-summary", result["missing_evidence_ids"])
            for signal in ("logs", "pod_status", "kubernetes_events"):
                config["scenario"]["requiredSignals"] = [signal]
                (run / "evidence/kubernetes-commands.json").write_text("{}")
                result = build_assessment_result(
                    run, config=config, metadata=_metadata(run.name), findings=()
                )
                self.assertIn(f"signal:{signal}", result["missing_evidence_ids"])

    def test_non_finite_traffic_and_failed_preflight_are_not_evidence(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            for count in (0, -1, float("nan"), float("inf"), True):
                payload = evidence_payload("k6-summary")
                payload["metrics"]["http_reqs"]["values"]["count"] = count
                path.write_text(json.dumps(payload))
                self.assertFalse(_has_evidence_content(path, "k6-summary"))
            path.write_text('{"ready": false}')
            self.assertFalse(_has_evidence_content(path, "preflight"))
            path.write_text('{"task_count": 0, "tasks": []}')
            self.assertFalse(_has_evidence_content(path, "relayna-summary"))
            self.assertFalse(_has_evidence_content(Path(tmp) / "absent.json", "preflight"))

    def test_uncited_agent_narrative_is_rejected(self):
        narrative = ReportNarrative("All pods recovered", (), (), ())
        with self.assertRaises(AgentValidationError):
            validate_evidence_bound_output(narrative, available_evidence_ids={"pods"})

    def test_job_isolation_idempotency_and_large_output(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = workspace / "chamber.yaml"
            config.write_text("test configuration")
            manager = AssessmentJobManager(workspace)
            command = [
                sys.executable,
                "-c",
                "import time; print('x'*200000, flush=True); time.sleep(.3); print('done')",
            ]
            with patch("chamber.control_plane.jobs._command", return_value=command):
                first = manager.start(config, mode="local", idempotency_key="first")
                second = manager.start(config, mode="local")
                self.assertNotEqual(first["run_id"], second["run_id"])
                self.assertEqual(
                    manager.start(config, mode="local", idempotency_key="first")["job_id"],
                    first["job_id"],
                )
                observer = AssessmentJobManager(workspace)
                self.assertNotEqual(observer.get(first["job_id"])["state"], "failed")
                with self.assertRaises(ValueError):
                    manager.start(config, mode="kubernetes", idempotency_key="first")
                for job in (first, second):
                    result = wait_job(manager, job["job_id"])
                    self.assertEqual(result["state"], "completed")
                    self.assertEqual(len(result["output"]), OUTPUT_LIMIT)
                    self.assertTrue(result["output"].endswith("done\n"))
            restored = AssessmentJobManager(workspace)
            self.assertEqual(len(restored.list()), 2)
            self.assertEqual(restored.get(first["job_id"])["run_id"], first["run_id"])
            self.assertEqual(restored.cancel(first["job_id"])["state"], "completed")
            with self.assertRaises(KeyError):
                restored.get("../jobs/secret")

    def test_cross_manager_cancel_escalates_and_records_cleanup(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config = workspace / "chamber.yaml"
            config.write_text("test")
            manager = AssessmentJobManager(workspace)
            command = [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "print('ready',flush=True); time.sleep(20)",
            ]
            with (
                patch("chamber.control_plane.jobs._command", return_value=command),
                patch("chamber.control_plane.jobs.CANCEL_GRACE_SECONDS", 0.2),
                patch("chamber.control_plane.jobs.KILL_GRACE_SECONDS", 0.2),
            ):
                job = manager.start(config, mode="kubernetes")
                time.sleep(0.3)
                other = AssessmentJobManager(workspace)
                other.cancel(job["job_id"])
                result = wait_job(manager, job["job_id"])
            self.assertEqual(result["state"], "cancelled")
            self.assertTrue(result["cleanup_required"])
            self.assertIn("forced", result["error"])
            self.assertEqual(
                json.loads((workspace / "runs" / job["run_id"] / "run.json").read_text())["state"],
                "cancelled",
            )

    def test_orphaned_job_is_interrupted_and_result_loses_readiness(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AssessmentJobManager(root)
            run = root / "runs" / "interrupted"
            initialize_run_record(run, mode="kubernetes")
            write_json_atomic(run / "result.json", {"status": "ready", "readiness_score": 100})
            job = AssessmentJob(
                "a" * 32,
                root / "config.yaml",
                "kubernetes",
                None,
                None,
                "now",
                state="running",
                run_id=run.name,
            )
            manager._persist(job)
            recovered = AssessmentJobManager(root).get(job.job_id)
            self.assertEqual(recovered["state"], "failed")
            self.assertTrue(recovered["cleanup_required"])
            result = json.loads((run / "result.json").read_text())
            self.assertIsNone(result["readiness_score"])
            self.assertFalse(result["cleanup_verified"])
            workflow._write_metadata(run, {"stage": "assessed", "mode": "kubernetes"})
            self.assertEqual(json.loads((run / "run.json").read_text())["state"], "failed")
            late_result = build_assessment_result(
                run, config=_config(), metadata=_metadata(run.name), findings=()
            )
            self.assertEqual(late_result["status"], "failed")
            self.assertIsNone(late_result["readiness_score"])

    def test_run_events_resume_from_last_delivered_id(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            write_json_atomic(run / "run.json", {"state": "completed"})
            (run / "events.jsonl").write_text('{"state":"running"}\n{"state":"completed"}\n')

            async def collect():
                return [event async for event in _run_event_stream(run, after=1)]

            events = asyncio.run(collect())
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0].startswith("id: 2\n"))

    def test_api_bearer_plan_start_and_cookie_csrf(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = "op_live_" + "a" * 40
            app = create_app(root, admin_token=token)
            plan = root / "runs" / "plan"
            initialize_run_record(plan)
            (plan / "chamber.yaml").write_text("test")
            with (
                TestClient(app) as client,
                patch.object(app.state.jobs, "start", return_value={"job_id": "job"}) as start,
            ):
                headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "example"}
                response = client.post(
                    "/api/v1/runs", headers=headers, json={"plan_id": "plan", "mode": "local"}
                )
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(start.call_args.kwargs["idempotency_key"], "example")
                self.assertEqual(start.call_args.args[0], (plan / "chamber.yaml").resolve())
                response = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={"plan_id": "plan", "config_path": "x", "mode": "local"},
                )
                self.assertEqual(response.status_code, 400)
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer invalid"},
                    json={"plan_id": "plan", "mode": "local"},
                )
                self.assertEqual(response.status_code, 401)
            with TestClient(create_app(root)) as client:
                self.assertEqual(
                    client.post(
                        "/api/v1/runs", json={"plan_id": "plan", "mode": "local"}
                    ).status_code,
                    403,
                )

    def test_agent_history_preserves_previous_stage_and_mode(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            config = workflow.infer_config(Path("examples/sample-service"))
            config["agents"] = {"mode": "offline"}
            workflow._write_agents(run, config, evidence_ids=("plan",), stage="plan")
            first = (run / "agent/scenario-planner-agent.json").read_bytes()
            workflow._write_agents(run, config, evidence_ids=("plan",), stage="assess")
            archives = list((run / "agent-history").iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual((archives[0] / "scenario-planner-agent.json").read_bytes(), first)
            self.assertEqual(
                json.loads((archives[0] / "provenance.json").read_text())["stage"], "plan"
            )
            self.assertEqual(json.loads((run / "agent-stage.json").read_text())["stage"], "assess")

    def test_missing_git_and_readiness_inference(self):
        with patch("chamber.workflow.subprocess.run", side_effect=FileNotFoundError("git")):
            self.assertEqual(workflow._git_commit(Path.cwd()), "unknown")
        config = workflow.infer_config(Path("examples/sample-service"))
        self.assertEqual(config["traffic"]["journeys"][0]["path"], "/readyz")


def wait_job(manager, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result["state"] in {"completed", "failed", "cancelled"}:
            return result
        time.sleep(0.02)
    raise AssertionError("job did not finish within deadline")
