from __future__ import annotations

import json
import subprocess
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import yaml
from fastapi.testclient import TestClient

from chamber.application.results import analyze_guided_run, build_assessment_result
from chamber.application.service import ChamberApplication
from chamber.control_plane.jobs import AssessmentJob, AssessmentJobManager, _command, _last_line
from chamber.control_plane.server import _json_file, create_app, run_server
from chamber.runs import (
    RunIndex,
    append_run_event,
    initialize_run_record,
    new_run_directory,
    refresh_evidence_manifest,
    registered_evidence,
    sync_run_record,
    write_json_atomic,
)
from chamber.workflow import infer_config, save_config


class RunStoreTests(unittest.TestCase):
    def test_unique_run_event_manifest_and_rebuildable_index(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            now = datetime(2026, 7, 11, tzinfo=UTC)
            first = new_run_directory(workspace / "runs", "Payments API", now=now)
            second = new_run_directory(workspace / "runs", "Payments API", now=now)
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith("chamber-payments-api-20260711000000000-"))

            record = initialize_run_record(first)
            self.assertEqual(record["state"], "created")
            self.assertEqual(record["service_name"], "unknown")
            initialize_run_record(first)
            event = append_run_event(first, state="running", event_type="preflight_passed")
            self.assertEqual(event["sequence"], 2)
            synced = sync_run_record(
                first,
                {
                    "run_id": first.name,
                    "service_name": "payments",
                    "mode": "kubernetes",
                    "stage": "assessed",
                    "success": True,
                    "cleanup_performed": True,
                },
            )
            self.assertEqual(synced["state"], "completed")
            self.assertEqual(synced["service_name"], "payments")

            evidence = first / "evidence/k6-summary.json"
            evidence.parent.mkdir()
            evidence.write_text('{"metrics": {}}', encoding="utf-8")
            manifest = refresh_evidence_manifest(first)
            self.assertEqual(len(manifest["entries"]), 1)
            self.assertEqual(len(registered_evidence(first)), 1)
            evidence.write_text("tampered", encoding="utf-8")
            self.assertEqual(registered_evidence(first), ())

            index = RunIndex(workspace)
            self.assertEqual(index.rebuild(), 1)
            rows = index.list_runs(limit=0)
            self.assertEqual(rows[0]["run_id"], first.name)

    def test_invalid_manifest_and_legacy_metadata_are_handled(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            run_dir = workspace / "runs/legacy"
            run_dir.mkdir(parents=True)
            write_json_atomic(
                run_dir / "run-metadata.json",
                {"run_id": "legacy", "stage": "failed", "mode": "local"},
            )
            (run_dir / "evidence").mkdir()
            write_json_atomic(
                run_dir / "evidence/manifest.json",
                {"run_id": "another-run", "entries": []},
            )
            self.assertEqual(registered_evidence(run_dir), ())
            self.assertEqual(RunIndex(workspace).rebuild(), 1)
            self.assertEqual(RunIndex(workspace).list_runs()[0]["state"], "failed")


class AssessmentResultTests(unittest.TestCase):
    def test_result_stays_inconclusive_without_live_registered_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            initialize_run_record(run_dir)
            result = build_assessment_result(
                run_dir,
                config={"runtime": {"provider": "local"}},
                metadata={"run_id": run_dir.name, "stage": "assessed", "mode": "local"},
                findings=(),
            )
            self.assertEqual(result["status"], "inconclusive")
            self.assertIsNone(result["readiness_score"])
            self.assertEqual(result["missing_evidence_ids"], ["live-kubernetes-execution"])

    def test_registered_kubernetes_evidence_produces_scored_result(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "live-run"
            evidence = run_dir / "evidence"
            evidence.mkdir(parents=True)
            for name in ("preflight", "kubernetes-commands", "k6-summary"):
                (evidence / f"{name}.json").write_text("{}", encoding="utf-8")
            refresh_evidence_manifest(run_dir)
            metadata = {
                "run_id": run_dir.name,
                "stage": "assessed",
                "mode": "kubernetes",
                "success": True,
                "traffic_result": {"success": True},
                "cleanup_performed": True,
                "rollback": {"verified": True},
            }
            result = build_assessment_result(
                run_dir,
                config={"runtime": {"provider": "kubernetes", "mode": "deploy"}},
                metadata=metadata,
                findings=({"severity": "medium"},),
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["readiness_score"], 90)
            self.assertTrue(result["conclusive"])

            cancelled = build_assessment_result(
                run_dir,
                config={"runtime": {"provider": "kubernetes", "mode": "deploy"}},
                metadata={**metadata, "stage": "cancelled"},
                findings=(),
            )
            self.assertEqual(cancelled["status"], "cancelled")

            incomplete = build_assessment_result(
                run_dir,
                config={"runtime": {"provider": "kubernetes", "mode": "attach"}},
                metadata={**metadata, "stage": "assessed"},
                findings=(),
            )
            self.assertEqual(incomplete["status"], "inconclusive")
            self.assertIn("rollback", incomplete["missing_evidence_ids"])

            failed = build_assessment_result(
                run_dir,
                config={"runtime": {"provider": "kubernetes", "mode": "deploy"}},
                metadata={**metadata, "stage": "failed"},
                findings=(),
            )
            self.assertEqual(failed["status"], "failed")

    def test_command_evidence_is_analyzed_without_trusting_invalid_records(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "analyze"
            evidence = run_dir / "evidence"
            evidence.mkdir(parents=True)
            commands = {
                "commands": [
                    {
                        "command": ["kubectl", "get", "pods", "-o", "json"],
                        "exit_status": 0,
                        "stdout": json.dumps(
                            {
                                "items": [
                                    {
                                        "metadata": {"name": "api"},
                                        "status": {
                                            "containerStatuses": [
                                                {
                                                    "name": "api",
                                                    "restartCount": 4,
                                                    "lastState": {
                                                        "terminated": {"reason": "OOMKilled"}
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        ),
                    },
                    {"command": ["kubectl", "get", "events"], "exit_status": 1, "stdout": ""},
                ]
            }
            write_json_atomic(evidence / "kubernetes-commands.json", commands)
            findings = analyze_guided_run(
                run_dir,
                config={"service": {"name": "api"}},
                metadata={"run_id": run_dir.name},
            )
            self.assertTrue(findings)
            self.assertTrue(all(item["recommendations"] for item in findings))

            write_json_atomic(evidence / "kubernetes-commands.json", {"commands": "invalid"})
            fallback = analyze_guided_run(
                run_dir,
                config={"service": {"name": "api"}},
                metadata={"run_id": run_dir.name},
            )
            self.assertEqual(fallback, ())


class ControlPlaneTests(unittest.TestCase):
    def test_html_api_plan_result_evidence_compare_and_security_contracts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            repo = _fixture_repo(root)
            app = create_app(workspace)
            with TestClient(app) as client:
                home = client.get("/", follow_redirects=False)
                self.assertEqual(home.status_code, 303)
                new_page = client.get("/new")
                self.assertIn("Choose the service to assess", new_page.text)
                self.assertIn("Running Kubernetes service", new_page.text)
                self.assertIn("Content-Security-Policy", new_page.headers)
                self.assertEqual(client.get("/runs").status_code, 200)
                self.assertEqual(client.get("/api/v1/capabilities").status_code, 200)
                csrf = client.cookies.get("ampule_csrf")
                self.assertIsNotNone(csrf)

                denied = client.post("/api/v1/inspect", json={"repo": str(repo)})
                self.assertEqual(denied.status_code, 403)
                inspected = client.post(
                    "/api/v1/inspect",
                    json={"repo": str(repo)},
                    headers={"X-CSRF-Token": str(csrf)},
                )
                self.assertEqual(inspected.status_code, 200)
                self.assertEqual(inspected.json()["service"]["name"], "target-service")
                invalid_plan = client.post(
                    "/api/v1/plans",
                    json={"config": {}},
                    headers={"X-CSRF-Token": str(csrf)},
                )
                self.assertEqual(invalid_plan.status_code, 400)
                self.assertEqual(
                    client.post(
                        "/api/v1/inspect",
                        json={"repo": str(root / "missing")},
                        headers={"X-CSRF-Token": str(csrf)},
                    ).status_code,
                    400,
                )

                invalid_form = client.post(
                    "/ui/plan",
                    data={"_csrf": csrf, "repo": str(root / "missing")},
                )
                self.assertEqual(invalid_form.status_code, 400)

                missing_local_repo = client.post(
                    "/ui/plan",
                    data={"_csrf": csrf, "execution_mode": "local"},
                )
                self.assertEqual(missing_local_repo.status_code, 400)

                planned = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "service_name": "payments",
                        "execution_mode": "local",
                        "runtime_mode": "deploy",
                        "service_port": "8080",
                        "traffic_path": "/health",
                        "agents_mode": "offline",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(planned.status_code, 303)
                run_location = planned.headers["location"]
                run_id = run_location.split("/")[2].split("?")[0]
                self.assertTrue((workspace / "runs" / run_id / "run.json").exists())

                for suffix in (
                    "",
                    "?tab=timeline",
                    "?tab=findings",
                    "?tab=configuration",
                    "?tab=agents",
                    "?tab=invalid",
                ):
                    run_page = client.get(f"/runs/{run_id}{suffix}")
                    self.assertEqual(run_page.status_code, 200)
                    self.assertIn("Start assessment", run_page.text)
                    self.assertIn("Review the generated configuration", run_page.text)
                listed = client.get("/api/v1/runs").json()["runs"]
                self.assertEqual(listed[0]["run_id"], run_id)
                self.assertEqual(client.get(f"/api/v1/runs/{run_id}").status_code, 200)

                run_dir = workspace / "runs" / run_id
                evidence_path = run_dir / "evidence/check.json"
                evidence_path.write_text('{"ok": true}', encoding="utf-8")
                item = refresh_evidence_manifest(run_dir)["entries"][0]
                evidence_response = client.get(
                    f"/api/v1/runs/{run_id}/evidence/{item['evidence_id']}"
                )
                self.assertEqual(evidence_response.status_code, 200)
                self.assertEqual(client.get(f"/runs/{run_id}?tab=evidence").status_code, 200)

                self.assertEqual(
                    client.get(f"/api/v1/runs/{run_id}/report?format=json").status_code,
                    200,
                )
                self.assertEqual(
                    client.get(f"/api/v1/runs/{run_id}/report?format=html").status_code,
                    200,
                )
                markdown = client.get(f"/api/v1/runs/{run_id}/report")
                self.assertEqual(markdown.status_code, 200)
                self.assertIn("# Ampule Chamber", markdown.text)
                comparison = client.post(
                    "/api/v1/compare",
                    json={"baseline_run_id": run_id, "candidate_run_id": run_id},
                    headers={"X-CSRF-Token": str(csrf)},
                )
                self.assertTrue(comparison.json()["compatible"])
                self.assertEqual(
                    client.get(f"/compare?baseline={run_id}&candidate={run_id}").status_code,
                    200,
                )
                self.assertEqual(client.get("/api/v1/runs/missing").status_code, 404)
                self.assertEqual(client.get("/runs/missing").status_code, 404)
                self.assertEqual(
                    client.post("/ui/runs/missing/start", data={"_csrf": csrf}).status_code, 404
                )
                self.assertEqual(client.get("/api/v1/runs/missing/events").status_code, 404)
                self.assertEqual(client.get("/api/v1/runs/missing/report").status_code, 404)
                self.assertEqual(
                    client.get(f"/api/v1/runs/{run_id}/evidence/missing").status_code,
                    404,
                )
                self.assertEqual(client.get("/runs/../secret").status_code, 404)

                invalid_compare = client.post(
                    "/api/v1/compare",
                    json={"baseline_run_id": "missing", "candidate_run_id": run_id},
                    headers={"X-CSRF-Token": str(csrf)},
                )
                self.assertEqual(invalid_compare.status_code, 400)
                self.assertEqual(
                    client.get(f"/compare?baseline=missing&candidate={run_id}").status_code,
                    200,
                )

                for runtime_mode in ("deploy", "attach"):
                    kubernetes_plan = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "service_name": "target-service",
                            "execution_mode": "kubernetes",
                            "runtime_mode": runtime_mode,
                            "kubernetes_context": "kind-ampule-chamber",
                            "namespace": "chamber-existing",
                            "service_port": "8080",
                            "traffic_path": "/health",
                            "traffic_profile": "stress",
                            "fault_type": (
                                "deployment_scale" if runtime_mode == "attach" else "none"
                            ),
                            "prometheus_url": "http://prometheus.local:9090",
                            "agents_mode": "offline",
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(kubernetes_plan.status_code, 303, kubernetes_plan.text)

                attached_without_repo = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": "",
                        "service_name": "payments-api",
                        "workload_name": "payments-worker",
                        "workload_kind": "StatefulSet",
                        "execution_mode": "kubernetes",
                        "runtime_mode": "attach",
                        "kubernetes_context": "kind-ampule-chamber",
                        "namespace": "payments-stage",
                        "service_port": "8080",
                        "traffic_path": "/health",
                        "traffic_profile": "baseline",
                        "fault_type": "none",
                        "agents_mode": "offline",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(attached_without_repo.status_code, 303)
                attached_id = attached_without_repo.headers["location"].split("/")[2].split("?")[0]
                attached_config = yaml.safe_load(
                    (workspace / "runs" / attached_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(attached_config["runtime"]["mode"], "attach")
                self.assertEqual(attached_config["runtime"]["namespace"], "payments-stage")
                self.assertEqual(attached_config["service"]["name"], "payments-api")
                self.assertEqual(
                    attached_config["deployment"]["workloads"],
                    [{"name": "payments-worker", "role": "target", "kind": "StatefulSet"}],
                )
                self.assertTrue(attached_config["service"]["repo"].startswith("kubernetes://"))

                missing_workload = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "service_name": "payments-api",
                        "execution_mode": "kubernetes",
                        "runtime_mode": "attach",
                    },
                )
                self.assertEqual(missing_workload.status_code, 400)

    def test_json_plan_start_job_cancel_and_event_endpoints(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            repo = _fixture_repo(root)
            app = create_app(workspace)
            with TestClient(app) as client:
                client.get("/new")
                csrf = str(client.cookies.get("ampule_csrf"))
                config = infer_config(repo)
                planned = client.post(
                    "/api/v1/plans",
                    json={"config": config},
                    headers={"X-CSRF-Token": csrf},
                )
                self.assertEqual(planned.status_code, 200)
                run_id = planned.json()["run_id"]
                config_path = workspace / "runs" / run_id / "chamber.yaml"

                with patch.object(
                    app.state.jobs,
                    "start",
                    return_value={"job_id": "job-1", "state": "queued"},
                ):
                    started = client.post(
                        "/api/v1/runs",
                        json={"config_path": str(config_path), "mode": "local"},
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(started.status_code, 202)
                    page_started = client.post(
                        f"/ui/runs/{run_id}/start",
                        data={"_csrf": csrf},
                        follow_redirects=False,
                    )
                    self.assertEqual(page_started.headers["location"], "/jobs/job-1")

                outside = client.post(
                    "/api/v1/runs",
                    json={"config_path": "/etc/hosts", "mode": "local"},
                    headers={"X-CSRF-Token": csrf},
                )
                self.assertEqual(outside.status_code, 400)

                job = app.state.jobs.start(config_path, mode="local")
                job_id = job["job_id"]
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    state = app.state.jobs.get(job_id)
                    if state["state"] in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(state["state"], "completed", state)
                self.assertIsNotNone(state["run_id"])
                self.assertEqual(client.get(f"/jobs/{job_id}").status_code, 200)
                self.assertEqual(client.get(f"/api/v1/jobs/{job_id}").status_code, 200)
                self.assertEqual(client.get("/jobs/missing").status_code, 404)
                self.assertEqual(client.get("/api/v1/jobs/missing").status_code, 404)
                self.assertEqual(client.get("/api/v1/jobs/missing/events").status_code, 404)
                cancelled = client.post(
                    f"/api/v1/jobs/{job_id}/cancel", headers={"X-CSRF-Token": csrf}
                )
                self.assertEqual(cancelled.status_code, 200)
                cancelled_page = client.post(
                    f"/ui/jobs/{job_id}/cancel",
                    data={"_csrf": csrf},
                    follow_redirects=False,
                )
                self.assertEqual(cancelled_page.status_code, 303)
                events = client.get(f"/api/v1/jobs/{job_id}/events")
                self.assertIn("event: job", events.text)
                run_events = client.get(f"/api/v1/runs/{state['run_id']}/events")
                self.assertIn("event: run", run_events.text)
                self.assertEqual(
                    client.post("/ui/jobs/missing/cancel", data={"_csrf": csrf}).status_code,
                    404,
                )
                invalid_json = root / "invalid.json"
                invalid_json.write_text("not-json", encoding="utf-8")
                self.assertEqual(_json_file(invalid_json), {})
                self.assertEqual(
                    client.post(
                        "/api/v1/jobs/missing/cancel", headers={"X-CSRF-Token": csrf}
                    ).status_code,
                    404,
                )

    def test_remote_binding_requires_explicit_opt_in(self) -> None:
        with self.assertRaisesRegex(ValueError, "--allow-remote"):
            run_server(
                host="0.0.0.0",
                port=8765,
                workspace=Path(".chamber"),
                open_browser=False,
            )

        with (
            patch("uvicorn.run") as uvicorn_run,
            patch("chamber.control_plane.server.threading.Timer") as timer,
        ):
            self.assertEqual(
                run_server(
                    host="0.0.0.0",
                    port=8765,
                    workspace=Path(".chamber"),
                    open_browser=True,
                    allow_remote=True,
                ),
                0,
            )
            timer.return_value.start.assert_called_once()
            uvicorn_run.assert_called_once()


class ApplicationFacadeTests(unittest.TestCase):
    def test_workspace_plan_read_and_comparison(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _fixture_repo(root)
            workspace = root / "custom-workspace"
            application = ChamberApplication(workspace)
            application.initialize()
            config_path = root / "chamber.yaml"
            save_config(infer_config(repo), config_path)
            first = application.plan(config_path)
            second = application.plan(config_path)
            write_json_atomic(
                first / "result.json",
                {
                    "status": "conditional",
                    "readiness_score": 82,
                    "evidence_coverage_percent": 75,
                },
            )
            self.assertEqual(first.parent, workspace / "runs")
            self.assertNotEqual(first, second)
            self.assertEqual(application.get_run(first.name)["run"]["run_id"], first.name)
            listed = {item["run_id"]: item for item in application.list_runs()}
            self.assertEqual(listed[first.name]["readiness_score"], 82)
            comparison = application.compare(first.name, second.name)
            self.assertTrue(comparison.compatible)
            with self.assertRaises(ValueError):
                application.run_path("../escape")

            config = infer_config(repo)
            config["service"]["name"] = "another-service"
            another_config = root / "another.yaml"
            save_config(config, another_config)
            another = application.plan(another_config)
            self.assertFalse(application.compare(first.name, another.name).compatible)
            self.assertEqual(application.inspect_repository(repo)["kind"], "ChamberConfig")
            output = root / "onboarded.yaml"
            self.assertEqual(application.onboard(repo, output=output), output)


class JobManagerUnitTests(unittest.TestCase):
    def test_cancel_command_and_process_failure_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            manager = AssessmentJobManager(workspace)
            with self.assertRaises(KeyError):
                manager.get("missing")
            with self.assertRaises(KeyError):
                manager.cancel("missing")

            job = AssessmentJob(
                job_id="queued",
                config_path=Path(tmp) / "chamber.yaml",
                mode="kubernetes",
                context="kind-test",
                prometheus_url="http://metrics:9090",
                created_at="now",
            )
            manager._jobs[job.job_id] = job
            cancelled = manager.cancel(job.job_id)
            self.assertEqual(cancelled["state"], "cancelling")
            manager._execute(job.job_id)
            self.assertEqual(manager.get(job.job_id)["state"], "cancelled")

            terminal = AssessmentJob(
                job_id="terminal",
                config_path=Path(tmp) / "chamber.yaml",
                mode="local",
                context=None,
                prometheus_url=None,
                created_at="now",
                state="completed",
            )
            manager._jobs[terminal.job_id] = terminal
            self.assertEqual(manager.cancel(terminal.job_id)["state"], "completed")

            failing = AssessmentJob(
                job_id="failing",
                config_path=Path(tmp) / "chamber.yaml",
                mode="local",
                context=None,
                prometheus_url=None,
                created_at="now",
            )
            manager._jobs[failing.job_id] = failing
            with patch("chamber.control_plane.jobs.subprocess.Popen", side_effect=OSError("boom")):
                manager._execute(failing.job_id)
            self.assertEqual(manager.get(failing.job_id)["error"], "boom")

            exited = AssessmentJob(
                job_id="exited",
                config_path=Path(tmp) / "chamber.yaml",
                mode="local",
                context=None,
                prometheus_url=None,
                created_at="now",
            )
            manager._jobs[exited.job_id] = exited
            process = MagicMock(spec=subprocess.Popen)
            process.poll.return_value = 1
            process.returncode = 1
            process.communicate.return_value = ("assessment failed\n", None)
            with patch("chamber.control_plane.jobs.subprocess.Popen", return_value=process):
                manager._execute(exited.job_id)
            self.assertEqual(manager.get(exited.job_id)["state"], "failed")
            self.assertEqual(manager.get(exited.job_id)["error"], "assessment failed")

            command = _command(job)
            self.assertIn("--context", command)
            self.assertIn("--prometheus-url", command)
            self.assertEqual(_last_line("one\n\n two \n"), "two")
            self.assertEqual(_last_line(""), "")

    def test_cancel_signals_running_process(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = AssessmentJobManager(Path(tmp) / ".chamber")
            process = MagicMock(spec=subprocess.Popen)
            process.poll.return_value = None
            job = AssessmentJob(
                job_id="running",
                config_path=Path(tmp) / "chamber.yaml",
                mode="local",
                context=None,
                prometheus_url=None,
                created_at="now",
                state="running",
                process=process,
            )
            manager._jobs[job.job_id] = job
            manager.cancel(job.job_id)
            process.send_signal.assert_called_once()


def _fixture_repo(root: Path) -> Path:
    repo = root / "target-service"
    manifests = repo / "manifests"
    manifests.mkdir(parents=True)
    (repo / "Dockerfile").write_text(
        'FROM python:3.13-alpine\nCMD ["python", "-m", "http.server", "8080"]\n',
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "target-service"},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "target-service"}},
            "template": {
                "metadata": {"labels": {"app": "target-service"}},
                "spec": {
                    "containers": [
                        {
                            "name": "target-service",
                            "image": "ampule/target-service:local",
                            "ports": [{"containerPort": 8080}],
                        }
                    ]
                },
            },
        },
    }
    (manifests / "app.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return repo
