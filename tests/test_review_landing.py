"""Regression coverage for execution boundaries and load report comparisons."""

from __future__ import annotations

import io
import json
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from chamber import workflow
from chamber.application.service import ChamberApplication
from chamber.control_plane.jobs import AssessmentJob, AssessmentJobManager, _command
from chamber.environment.chambers import ChamberProfile, ChamberStore
from chamber.runs import write_json_atomic
from tests.test_control_plane_enhancements import _run
from tests.test_phase11_12_13_workflow import _attach_kubernetes_config, _fixture_repo


class ReviewLandingTests(unittest.TestCase):
    def test_job_snapshot_validates_managed_upload_at_actual_workspace(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            upload = workspace / "uploads/input.txt"
            upload.parent.mkdir(parents=True)
            upload.write_text("test payload")
            config: dict[str, Any] = _attach_kubernetes_config(_fixture_repo(root))
            config["traffic"]["journeys"] = [
                {
                    "name": "upload",
                    "method": "POST",
                    "path": "/upload",
                    "expectedStatus": 200,
                    "vus": 1,
                    "iterations": 1,
                    "requestEncoding": "multipart",
                    "multipart": {
                        "files": [
                            {
                                "field": "file",
                                "path": str(upload),
                                "filename": "input.txt",
                                "contentType": "text/plain",
                            }
                        ]
                    },
                }
            ]
            path = workspace / "drafts/input.yaml"
            path.parent.mkdir()
            workflow.save_config(config, path)
            manager = AssessmentJobManager(workspace)
            done = threading.Event()
            received = []

            def execute(job_id):
                received.append(workflow.load_config(manager._jobs[job_id].config_path))
                done.set()

            with patch.object(manager, "_execute", side_effect=execute):
                job = manager.start(path, mode="kubernetes")
                self.assertTrue(done.wait(5))
            self.assertEqual(
                received[0]["traffic"]["journeys"][0]["multipart"]["files"][0]["path"], str(upload)
            )
            self.assertEqual(
                Path(job["config_path"]).parent, workspace.resolve() / "runs" / job["run_id"]
            )
            command = _command(manager._jobs[job["job_id"]])
            self.assertEqual(
                command[command.index("--run-dir") + 1], str(Path(job["config_path"]).parent)
            )

    def test_cli_rebinds_budget_and_fault_capability_from_store(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config: dict[str, Any] = _attach_kubernetes_config(_fixture_repo(root))
            profile = ChamberStore(root).create(
                ChamberProfile(
                    name="Stored",
                    context="dev-cluster",
                    namespace="translation-test",
                    service=config["service"]["name"],
                    workload="translation-service",
                    max_vus=1,
                    max_duration_seconds=7200,
                )
            )
            config["chamber"] = {
                **profile,
                "max_vus": 100,
                "allow_faults": True,
                "chaos_mesh": True,
            }
            config["traffic"]["journeys"][0]["vus"] = 2
            path = root / "config.yaml"
            workflow.save_config(config, path)
            runner = MagicMock()
            with patch("chamber.workflow.WORKSPACE_DIR", str(root)):
                with self.assertRaisesRegex(ValueError, "VU budget"):
                    workflow._assess_kubernetes_config(
                        path, agents_mode="off", context=None, prometheus_url=None, runner=runner
                    )
                config["traffic"]["journeys"][0]["vus"] = 1
                config["runtime"]["faults"] = [
                    {"type": "pod_kill", "workload": "translation-service"}
                ]
                workflow.save_config(config, path)
                with self.assertRaisesRegex(ValueError, "does not allow faults"):
                    workflow._assess_kubernetes_config(
                        path, agents_mode="off", context=None, prometheus_url=None, runner=runner
                    )
            runner.run.assert_not_called()

    def test_supervisor_preserves_terminal_child_evidence_and_cleanup(self):
        cases = (
            ({"stage": "preflight_failed", "cleanup_performed": False}, False),
            (
                {
                    "stage": "cancelled",
                    "runtime_mode": "attach",
                    "rollback": {"faults_requested": False, "actions": []},
                },
                False,
            ),
            (
                {
                    "stage": "failed",
                    "runtime_mode": "attach",
                    "rollback": {"faults_requested": False, "verified": False},
                },
                True,
            ),
            (
                {"stage": "cancelled", "runtime_mode": "attach", "rollback": {"verified": True}},
                False,
            ),
            ({"stage": "failed", "cleanup_performed": True}, False),
            ({"stage": "failed", "runtime_mode": "attach", "rollback": {"verified": False}}, True),
        )
        for terminal, cleanup in cases:
            with self.subTest(terminal=terminal), TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                manager = AssessmentJobManager(workspace)
                run_dir = workspace / "runs/run"
                metadata = {"run_id": "run", "success": False, **terminal}
                result = {
                    "run_id": "run",
                    "status": terminal["stage"],
                    "verdict": {"headline": "Original reason"},
                }
                write_json_atomic(run_dir / "run-metadata.json", metadata)
                write_json_atomic(run_dir / "result.json", result)
                job = AssessmentJob(
                    "job",
                    run_dir / "execution-config.yaml",
                    "kubernetes",
                    None,
                    None,
                    "now",
                    run_id="run",
                )
                manager._jobs[job.job_id] = job
                process = MagicMock(spec=subprocess.Popen)
                process.poll.return_value = 1
                process.returncode = 1
                process.stdout = io.StringIO("handled failure\n")
                with patch("chamber.control_plane.jobs.subprocess.Popen", return_value=process):
                    manager._execute(job.job_id)
                self.assertEqual(job.cleanup_required, cleanup)
                self.assertEqual(json.loads((run_dir / "run-metadata.json").read_text()), metadata)
                self.assertEqual(json.loads((run_dir / "result.json").read_text()), result)
                self.assertFalse((run_dir / "execution-failure.json").exists())

    def test_comparison_uses_load_metrics_and_rejects_different_metrics_sources(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            for name, p95, errors in (("baseline", 200, 0.1), ("candidate", 100, 0.05)):
                run = _run(
                    workspace, name, created_at="2026-09-14T00:00:00Z", score=90, coverage=100
                )
                config = yaml.safe_load((run / "chamber.yaml").read_text())
                config["traffic"]["load"] = {"model": "arrival"}
                config["runtime"]["prometheusUrl"] = "http://metrics:9090"
                config["chamber"] = {
                    "context": "test",
                    "namespace": "payments-test",
                    "workload": "payments",
                }
                (run / "chamber.yaml").write_text(yaml.safe_dump(config))
                write_json_atomic(
                    run / "evidence/load-summary.json",
                    {"metrics": {"p95Ms": p95, "errorRate": errors}},
                )
            app = ChamberApplication(workspace)
            comparison = app.compare("baseline", "candidate")
            self.assertTrue(comparison.compatible)
            deltas = {item["key"]: item for item in comparison.signal_deltas}
            self.assertEqual(deltas["latency_p95_ms"]["delta"], -100)
            self.assertEqual(deltas["error_rate_percent"]["delta"], -5)
            path = workspace / "runs/candidate/chamber.yaml"
            config["runtime"]["prometheusUrl"] = "http://other:9090"
            path.write_text(yaml.safe_dump(config))
            comparison = app.compare("baseline", "candidate")
            self.assertFalse(comparison.compatible)
            self.assertTrue(
                any(
                    c["dimension"] == "metrics source" and not c["compatible"]
                    for c in comparison.compatibility_reasons
                )
            )
            self.assertTrue(all(not d["comparable"] for d in comparison.signal_deltas))
