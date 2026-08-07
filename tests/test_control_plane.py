from __future__ import annotations

import asyncio
import io
import json
import subprocess
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import yaml
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from chamber.application.results import analyze_guided_run, build_assessment_result
from chamber.application.service import ChamberApplication
from chamber.control_plane.jobs import AssessmentJob, AssessmentJobManager, _command, _last_line
from chamber.control_plane.server import (
    _json_file,
    _persist_journey_files,
    _ui_journeys,
    create_app,
    run_server,
)
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
            self.assertEqual(result["status"], "local_only")
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
                self.assertIn("data-add-journey", new_page.text)
                self.assertIn("data-add-multipart-file", new_page.text)
                self.assertIn("data-review-files", new_page.text)
                self.assertIn("Expected status", new_page.text)
                self.assertIn("Custom stages", new_page.text)
                self.assertIn('enctype="multipart/form-data"', new_page.text)
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
                        "journey_type": "relayna",
                        "traffic_path": "/translations",
                        "traffic_profile": "smoke",
                        "request_body": json.dumps(
                            {
                                "text": "Hello from Ampule Chamber.",
                                "language_target": "Thai",
                            }
                        ),
                        "events_path": "/events/{task_id}",
                        "task_id_path": "task_id",
                        "relayna_timeout_seconds": "120",
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
                relayna_journey = attached_config["traffic"]["journeys"][0]
                self.assertEqual(relayna_journey["adapter"], "relayna")
                self.assertEqual(relayna_journey["expectedStatus"], 202)
                self.assertEqual(relayna_journey["relayna"]["eventsPath"], "/events/{task_id}")
                self.assertEqual(relayna_journey["relayna"]["timeoutSeconds"], 120)
                self.assertEqual(
                    attached_config["deployment"]["workloads"],
                    [{"name": "payments-worker", "role": "target", "kind": "StatefulSet"}],
                )
                self.assertTrue(attached_config["service"]["repo"].startswith("kubernetes://"))

                journeys = [
                    {
                        "name": "read-backpressure",
                        "method": "GET",
                        "path": "/relayna/runtime/backpressure",
                        "expectedStatus": 204,
                        "tool": "k6",
                        "stages": [
                            {"duration": "5s", "targetVus": 2},
                            {"duration": "3s", "targetVus": 0},
                        ],
                    },
                    {
                        "name": "submit-translation",
                        "method": "POST",
                        "path": "/translations",
                        "expectedStatus": 201,
                        "body": {"text": "Hello", "language_target": "Thai"},
                        "vus": 1,
                        "iterations": 2,
                        "durationSeconds": 10,
                        "followUps": [
                            {
                                "name": "translation-status",
                                "type": "http_status",
                                "target": "/translations/{task_id}",
                                "expected": "completed",
                                "serviceName": "target-service",
                            }
                        ],
                    },
                ]
                multiple = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "service_name": "target-service",
                        "execution_mode": "kubernetes",
                        "runtime_mode": "deploy",
                        "kubernetes_context": "kind-ampule-chamber",
                        "service_port": "8080",
                        "journeys_json": json.dumps(journeys),
                        "agents_mode": "offline",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(multiple.status_code, 303, multiple.text)
                multiple_id = multiple.headers["location"].split("/")[2].split("?")[0]
                multiple_config = yaml.safe_load(
                    (workspace / "runs" / multiple_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                expected_journeys = [
                    dict(journey, requestEncoding="json" if "body" in journey else "none")
                    for journey in journeys
                ]
                self.assertEqual(multiple_config["traffic"]["journeys"], expected_journeys)

                ocr_journey = {
                    "name": "ocr-file-admission",
                    "method": "POST",
                    "path": "/ocr",
                    "expectedStatus": 202,
                    "requestEncoding": "multipart",
                    "multipart": {
                        "fields": {
                            "engine": "internal",
                            "mode": "layout",
                            "force_ocr": False,
                            "priority": 5,
                        },
                        "files": [
                            {
                                "field": "file",
                                "uploadIndex": 0,
                                "path": "/etc/hosts",
                                "filename": "stolen.txt",
                                "contentType": "text/plain",
                            }
                        ],
                    },
                    "iterations": 1,
                    "vus": 1,
                    "durationSeconds": 1,
                }
                ocr_plan = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "service_name": "ocr-service",
                        "execution_mode": "kubernetes",
                        "runtime_mode": "deploy",
                        "kubernetes_context": "kind-ampule-chamber",
                        "service_port": "8080",
                        "journeys_json": json.dumps([ocr_journey]),
                        "agents_mode": "offline",
                    },
                    files={"journey_files": ("invoice.pdf", b"%PDF-1.7 test", "application/pdf")},
                    follow_redirects=False,
                )
                self.assertEqual(ocr_plan.status_code, 303, ocr_plan.text)
                ocr_id = ocr_plan.headers["location"].split("/")[2].split("?")[0]
                ocr_config = yaml.safe_load(
                    (workspace / "runs" / ocr_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                uploaded_file = ocr_config["traffic"]["journeys"][0]["multipart"]["files"][0]
                self.assertEqual(uploaded_file["field"], "file")
                self.assertEqual(uploaded_file["filename"], "stolen.txt")
                self.assertEqual(uploaded_file["contentType"], "text/plain")
                self.assertEqual(Path(uploaded_file["path"]).read_bytes(), b"%PDF-1.7 test")

                crafted_path = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "journeys_json": json.dumps(
                            [
                                {
                                    "name": "crafted-path",
                                    "method": "POST",
                                    "path": "/upload",
                                    "expectedStatus": 200,
                                    "requestEncoding": "multipart",
                                    "multipart": {
                                        "fields": {},
                                        "files": [{"field": "file", "path": "/etc/hosts"}],
                                    },
                                    "iterations": 1,
                                }
                            ]
                        ),
                    },
                )
                self.assertEqual(crafted_path.status_code, 400)
                self.assertIn("require a browser upload", crafted_path.text)

                invalid_status = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "journeys_json": json.dumps(
                            [
                                {
                                    "name": "invalid",
                                    "method": "GET",
                                    "path": "/health",
                                    "expectedStatus": 700,
                                }
                            ]
                        ),
                    },
                )
                self.assertEqual(invalid_status.status_code, 400)
                self.assertIn("between 100 and 599", invalid_status.text)

                invalid_loads = (
                    (
                        {"stages": [{"duration": "30s", "target": 4}]},
                        "targetVus must be a non-negative integer",
                    ),
                    (
                        {"stages": [{"duration": "30s", "targetVus": "4"}]},
                        "targetVus must be a non-negative integer",
                    ),
                    ({"vus": 0, "iterations": 1}, "vus must be a positive integer"),
                )
                for load, message in invalid_loads:
                    invalid_journey = {
                        "name": "invalid-load",
                        "method": "GET",
                        "path": "/health",
                        "expectedStatus": 200,
                        **load,
                    }
                    invalid_load = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "journeys_json": json.dumps([invalid_journey]),
                        },
                    )
                    self.assertEqual(invalid_load.status_code, 400)
                    self.assertIn(message, invalid_load.text)

                invalid_follow_up = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "journeys_json": json.dumps(
                            [
                                {
                                    "name": "invalid-follow-up",
                                    "method": "GET",
                                    "path": "/health",
                                    "expectedStatus": 200,
                                    "followUps": [
                                        {
                                            "name": "status",
                                            "method": "GET",
                                            "path": "/status",
                                        }
                                    ],
                                }
                            ]
                        ),
                    },
                )
                self.assertEqual(invalid_follow_up.status_code, 400)
                self.assertIn("requires type", invalid_follow_up.text)

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

    def test_request_encoding_validation_and_upload_safety(self) -> None:
        base = {
            "name": "request",
            "method": "POST",
            "path": "/submit",
            "expectedStatus": 200,
            "iterations": 1,
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload_path = root / "invoice.pdf"
            upload_path.write_bytes(b"%PDF")
            valid = (
                dict(base, requestEncoding="form", form={"query": "hello"}),
                dict(
                    base,
                    requestEncoding="raw",
                    body="hello",
                    contentType="text/plain",
                ),
                dict(
                    base,
                    requestEncoding="multipart",
                    multipart={
                        "fields": {"mode": "layout"},
                        "files": [{"field": "file", "path": str(upload_path)}],
                    },
                ),
                dict(
                    base,
                    adapter="relayna",
                    expectedStatus=202,
                    requestEncoding="multipart",
                    multipart={
                        "fields": {
                            "priority": 5,
                            "schema": {
                                "encoding": "json",
                                "value": {"fields": ["invoice_number"]},
                            },
                        },
                        "files": [
                            {
                                "field": "file",
                                "path": str(upload_path),
                                "filename": "invoice.pdf",
                                "contentType": "application/pdf",
                                "required": True,
                            }
                        ],
                    },
                    relayna={
                        "taskIdPath": "task_id",
                        "eventsPath": "/events/{task_id}",
                        "terminalStatuses": ["completed", "failed"],
                        "successStatuses": ["completed"],
                        "timeoutSeconds": 30,
                    },
                ),
            )
            for journey in valid:
                self.assertEqual(_ui_journeys(json.dumps([journey]), workspace=root)[0], journey)

            invalid = (
                (dict(base, requestEncoding="xml"), "requestEncoding"),
                (dict(base, requestEncoding="form", form=[]), "form must be"),
                (dict(base, requestEncoding="raw", body=1, contentType="text/plain"), "raw body"),
                (dict(base, requestEncoding="raw", body="hello"), "requires contentType"),
                (dict(base, requestEncoding="multipart", multipart=[]), "multipart must be"),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={"fields": [], "files": []},
                    ),
                    "multipart fields",
                ),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={"fields": {}, "files": []},
                    ),
                    "requires at least one file",
                ),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={"fields": {}, "files": ["file"]},
                    ),
                    "must be a JSON object",
                ),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={"fields": {}, "files": [{"field": "", "path": "x"}]},
                    ),
                    "requires field",
                ),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={
                            "fields": {},
                            "files": [
                                {"field": "file", "path": str(upload_path)},
                                {"field": "file", "path": str(upload_path)},
                            ],
                        },
                    ),
                    "fields must be unique",
                ),
                (
                    dict(
                        base,
                        requestEncoding="multipart",
                        multipart={"fields": {}, "files": [{"field": "file", "path": "x"}]},
                    ),
                    "path is not readable",
                ),
                (
                    dict(
                        base,
                        adapter="relayna",
                        requestEncoding="multipart",
                        multipart={"fields": {}, "files": []},
                    ),
                    "requires at least one file",
                ),
            )
            for journey, message in invalid:
                with self.assertRaisesRegex(ValueError, message):
                    _ui_journeys(json.dumps([journey]))

            malformed = (
                ("not-json", "valid JSON"),
                ("[]", "At least one"),
                ("[1]", "must be a JSON object"),
                (json.dumps([dict(base, name="")]), "requires a name"),
                (json.dumps([dict(base, path="submit")]), "path must start"),
                (json.dumps([dict(base, method="")]), "requires an HTTP method"),
                (json.dumps([dict(base, expectedStatus=True)]), "must be an integer"),
                (json.dumps([dict(base, adapter="smtp")]), "adapter must be"),
                (
                    json.dumps([dict(base, adapter="relayna", requestEncoding="form", form={})]),
                    "supports JSON or multipart",
                ),
                (
                    json.dumps(
                        [base, dict(base, name="relayna", adapter="relayna", body={"x": 1})]
                    ),
                    "cannot mix",
                ),
            )
            for raw, message in malformed:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        _ui_journeys(raw)

            def uploaded(content: bytes) -> UploadFile:
                return UploadFile(
                    io.BytesIO(content),
                    filename="invoice.pdf",
                    headers=Headers({"content-type": "application/pdf"}),
                )

            with self.assertRaisesRegex(ValueError, "valid JSON"):
                asyncio.run(_persist_journey_files(root, "not-json", [uploaded(b"data")]))
            with self.assertRaisesRegex(ValueError, "JSON array"):
                asyncio.run(_persist_journey_files(root, "{}", [uploaded(b"data")]))
            with self.assertRaisesRegex(ValueError, "missing its browser upload"):
                asyncio.run(
                    _persist_journey_files(
                        root,
                        json.dumps([{"multipart": {"files": [{"field": "file"}]}}]),
                        [uploaded(b"data")],
                    )
                )
            with self.assertRaisesRegex(ValueError, "required state"):
                asyncio.run(
                    _persist_journey_files(
                        root,
                        json.dumps(
                            [
                                {
                                    "multipart": {
                                        "files": [
                                            {
                                                "field": "file",
                                                "required": "yes",
                                                "uploadIndex": 0,
                                            }
                                        ]
                                    }
                                }
                            ]
                        ),
                        [uploaded(b"data")],
                    )
                )
            optional, _ = asyncio.run(
                _persist_journey_files(
                    root,
                    json.dumps(
                        [
                            {
                                "multipart": {
                                    "files": [
                                        {
                                            "field": "file",
                                            "uploadIndex": 0,
                                        },
                                        {
                                            "field": "roi",
                                            "required": False,
                                            "filename": "ignored.pdf",
                                            "contentType": "application/pdf",
                                        },
                                    ]
                                }
                            }
                        ]
                    ),
                    [uploaded(b"data")],
                )
            )
            self.assertNotIn("path", json.loads(optional)[0]["multipart"]["files"][1])
            crafted_path = json.dumps(
                [
                    {
                        "multipart": {
                            "files": [{"field": "file", "path": "/etc/hosts"}],
                        }
                    }
                ]
            )
            with self.assertRaisesRegex(ValueError, "require a browser upload"):
                asyncio.run(_persist_journey_files(root, crafted_path, []))
            with self.assertRaisesRegex(ValueError, "Every browser-uploaded"):
                asyncio.run(_persist_journey_files(root, "[]", [uploaded(b"data")]))
            missing_reference = json.dumps(
                [
                    {
                        "multipart": {
                            "files": [{"field": "file", "uploadIndex": 1}],
                        }
                    }
                ]
            )
            with self.assertRaisesRegex(ValueError, "unavailable browser upload"):
                asyncio.run(_persist_journey_files(root, missing_reference, [uploaded(b"data")]))
            empty_file = missing_reference.replace('"uploadIndex": 1', '"uploadIndex": 0')
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                asyncio.run(_persist_journey_files(root, empty_file, [uploaded(b"")]))
            with patch("chamber.control_plane.server.MAX_UI_UPLOAD_BYTES", 2):
                with self.assertRaisesRegex(ValueError, "limited to 128 MiB"):
                    asyncio.run(_persist_journey_files(root, empty_file, [uploaded(b"data")]))
            unsafe_filename = empty_file.replace(
                '"uploadIndex": 0',
                '"uploadIndex": 0, "filename": "../invoice.pdf"',
            )
            with self.assertRaisesRegex(ValueError, "must not contain paths"):
                asyncio.run(_persist_journey_files(root, unsafe_filename, [uploaded(b"data")]))

            multiple_files = json.dumps(
                [
                    {
                        "multipart": {
                            "files": [
                                {
                                    "field": "file",
                                    "filename": "document.pdf",
                                    "contentType": "application/pdf",
                                    "required": True,
                                    "uploadIndex": 0,
                                },
                                {
                                    "field": "roi",
                                    "filename": "roi.pdf",
                                    "contentType": "application/pdf",
                                    "required": False,
                                    "uploadIndex": 1,
                                },
                            ]
                        }
                    }
                ]
            )
            persisted, upload_dir = asyncio.run(
                _persist_journey_files(
                    root,
                    multiple_files,
                    [uploaded(b"document"), uploaded(b"roi")],
                )
            )
            persisted_files = json.loads(persisted)[0]["multipart"]["files"]
            self.assertEqual([item["field"] for item in persisted_files], ["file", "roi"])
            self.assertTrue(
                all(
                    Path(item["path"]).resolve().is_relative_to(root.resolve())
                    for item in persisted_files
                )
            )
            self.assertEqual([item["required"] for item in persisted_files], [True, False])
            self.assertIsNotNone(upload_dir)

            with patch("chamber.control_plane.server.MAX_UI_TOTAL_UPLOAD_BYTES", 5):
                with self.assertRaisesRegex(ValueError, "256 MiB total"):
                    asyncio.run(
                        _persist_journey_files(
                            root,
                            multiple_files,
                            [uploaded(b"123"), uploaded(b"456")],
                        )
                    )

    def test_ui_persists_multiple_relayna_multipart_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            repo = _fixture_repo(root)
            app = create_app(workspace)
            journey = {
                "name": "document-lifecycle",
                "adapter": "relayna",
                "method": "POST",
                "path": "/tasks",
                "expectedStatus": 202,
                "requestEncoding": "multipart",
                "multipart": {
                    "fields": {
                        "priority": 5,
                        "extraction_fields": {
                            "encoding": "json",
                            "value": [{"name": "invoice_number"}],
                        },
                    },
                    "files": [
                        {
                            "field": "file",
                            "filename": "document.png",
                            "contentType": "image/png",
                            "required": True,
                            "uploadIndex": 0,
                        },
                        {
                            "field": "roi",
                            "filename": "roi.png",
                            "contentType": "image/png",
                            "required": False,
                            "uploadIndex": 1,
                        },
                    ],
                },
                "vus": 1,
                "iterations": 1,
                "durationSeconds": 1,
                "relayna": {
                    "taskIdPath": "task_id",
                    "eventsPath": "/events/{task_id}",
                    "terminalStatuses": ["completed", "failed"],
                    "successStatuses": ["completed"],
                    "timeoutSeconds": 30,
                },
            }
            with TestClient(app) as client:
                client.get("/new")
                csrf = str(client.cookies.get("ampule_csrf"))
                response = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "service_name": "document-service",
                        "execution_mode": "kubernetes",
                        "runtime_mode": "deploy",
                        "kubernetes_context": "kind-ampule-chamber",
                        "service_port": "8080",
                        "journeys_json": json.dumps([journey]),
                        "agents_mode": "offline",
                    },
                    files=[
                        ("journey_files", ("document.png", b"document", "image/png")),
                        ("journey_files", ("roi.png", b"roi", "image/png")),
                    ],
                    follow_redirects=False,
                )

            self.assertEqual(response.status_code, 303, response.text)
            run_id = response.headers["location"].split("/")[2].split("?")[0]
            config = yaml.safe_load(
                (workspace / "runs" / run_id / "chamber.yaml").read_text(encoding="utf-8")
            )
            persisted = config["traffic"]["journeys"][0]
            self.assertEqual(persisted["adapter"], "relayna")
            self.assertEqual(persisted["requestEncoding"], "multipart")
            self.assertEqual(
                [item["field"] for item in persisted["multipart"]["files"]],
                ["file", "roi"],
            )
            self.assertTrue(
                all(
                    Path(item["path"]).resolve().is_relative_to((workspace / "uploads").resolve())
                    for item in persisted["multipart"]["files"]
                )
            )

    def test_saved_multipart_paths_survive_ui_planning_without_reupload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            repo = _fixture_repo(root)
            managed_uploads = workspace / "uploads" / "fixture"
            managed_uploads.mkdir(parents=True)
            invoice = managed_uploads / "invoice.pdf"
            mask = managed_uploads / "mask.png"
            outside = root / "outside.pdf"
            invoice.write_bytes(b"%PDF")
            mask.write_bytes(b"PNG")
            outside.write_bytes(b"outside")
            config = infer_config(repo)
            config["scenarioId"] = "saved-multipart"
            config["scenario"] = {
                "id": "saved-multipart",
                "name": "Saved multipart",
                "description": "Reusable local multipart fixture.",
                "tags": ["multipart"],
                "source": "custom",
                "revision": "draft",
                "requiredSignals": ["logs"],
            }
            config["traffic"]["journeys"] = [
                {
                    "name": "ocr",
                    "method": "POST",
                    "path": "/ocr",
                    "expectedStatus": 202,
                    "requestEncoding": "multipart",
                    "multipart": {
                        "fields": {"mode": "layout"},
                        "files": [
                            {"field": "file", "path": str(invoice)},
                            {"field": "mask", "path": str(mask)},
                        ],
                    },
                    "vus": 1,
                    "iterations": 1,
                    "durationSeconds": 1,
                }
            ]

            with TestClient(create_app(workspace)) as client:
                new_page = client.get("/new")
                self.assertIn("data-existing-multipart-file", new_page.text)
                csrf = str(client.cookies["ampule_csrf"])
                headers = {"X-CSRF-Token": csrf}
                imported = client.post(
                    "/api/v1/scenarios/validate",
                    json={"content": yaml.safe_dump(config), "service_name": "target-service"},
                    headers=headers,
                )
                self.assertEqual(imported.status_code, 200, imported.text)
                imported_files = imported.json()["journeys"][0]["multipart"]["files"]
                self.assertTrue(all(item.get("pathToken") for item in imported_files))

                traversal = workspace / "uploads" / ".." / ".." / outside.name
                symlink_escape = workspace / "uploads" / "escape.pdf"
                untrusted_paths = [outside, traversal]
                try:
                    symlink_escape.symlink_to(outside)
                    untrusted_paths.append(symlink_escape)
                except OSError:
                    pass
                for untrusted_path in untrusted_paths:
                    untrusted = json.loads(json.dumps(config))
                    untrusted["traffic"]["journeys"][0]["multipart"]["files"] = [
                        {
                            "field": "file",
                            "path": str(untrusted_path),
                            "pathToken": "attacker-supplied",
                        }
                    ]
                    validated = client.post(
                        "/api/v1/scenarios/validate",
                        json={"content": yaml.safe_dump(untrusted)},
                        headers=headers,
                    )
                    self.assertEqual(validated.status_code, 200, validated.text)
                    validated_file = validated.json()["journeys"][0]["multipart"]["files"][0]
                    self.assertNotIn("pathToken", validated_file)
                    rejected_untrusted = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "journeys_json": json.dumps(validated.json()["journeys"]),
                        },
                    )
                    self.assertEqual(rejected_untrusted.status_code, 400)
                    self.assertIn("require a browser upload", rejected_untrusted.text)

                saved = client.post(
                    "/api/v1/scenarios",
                    json={"document": config},
                    headers=headers,
                )
                self.assertEqual(saved.status_code, 201, saved.text)
                projection = client.get("/api/v1/scenarios/user/saved-multipart")
                self.assertEqual(projection.status_code, 200, projection.text)
                journeys = projection.json()["journeys"]
                saved_files = journeys[0]["multipart"]["files"]
                self.assertTrue(all(item.get("pathToken") for item in saved_files))

                planned = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "service_name": "target-service",
                        "journeys_json": json.dumps(journeys),
                    },
                    follow_redirects=False,
                )
                self.assertEqual(planned.status_code, 303, planned.text)
                run_id = planned.headers["location"].split("/")[2].split("?")[0]
                planned_config = yaml.safe_load(
                    (workspace / "runs" / run_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                planned_files = planned_config["traffic"]["journeys"][0]["multipart"]["files"]
                self.assertEqual(
                    planned_files,
                    [
                        {"field": "file", "path": str(invoice.resolve())},
                        {"field": "mask", "path": str(mask.resolve())},
                    ],
                )

                forged = json.loads(json.dumps(journeys))
                forged[0]["multipart"]["files"][0]["path"] = "/etc/hosts"
                rejected = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(repo),
                        "journeys_json": json.dumps(forged),
                    },
                )
                self.assertEqual(rejected.status_code, 400)
                self.assertIn("require a browser upload", rejected.text)

            script = (Path(__file__).parents[1] / "chamber/control_plane/static/app.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("row.dataset.retainedMultipartFile = JSON.stringify(item);", script)
            self.assertIn("pathToken: retained.pathToken", script)
            self.assertIn("Object.assign(item, {filename, contentType, uploadIndex});", script)

    def test_failed_plan_does_not_save_scenario_or_retain_uploaded_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / ".chamber"
            repo = _fixture_repo(root)
            app = create_app(workspace)
            with TestClient(app) as client:
                client.get("/new")
                csrf = str(client.cookies["ampule_csrf"])
                journey = {
                    "name": "failed-upload",
                    "method": "POST",
                    "path": "/upload",
                    "expectedStatus": 202,
                    "requestEncoding": "multipart",
                    "multipart": {
                        "fields": {"mode": "layout"},
                        "files": [{"field": "file", "uploadIndex": 0}],
                    },
                    "vus": 1,
                    "iterations": 1,
                    "durationSeconds": 1,
                }
                with patch.object(app.state.chamber, "plan", side_effect=RuntimeError("forced")):
                    failed = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "scenario_id": "failed-upload-save",
                            "scenario_name": "Failed upload save",
                            "save_scenario": "new",
                            "journeys_json": json.dumps([journey]),
                        },
                        files={
                            "journey_files": (
                                "invoice.pdf",
                                b"%PDF-1.7 test",
                                "application/pdf",
                            )
                        },
                    )
                self.assertEqual(failed.status_code, 400)
                self.assertIn("forced", failed.text)
                self.assertEqual(
                    client.get("/api/v1/scenarios/user/failed-upload-save").status_code,
                    404,
                )

                with patch.object(
                    app.state.scenarios,
                    "save",
                    side_effect=FileExistsError("simulated publish collision"),
                ):
                    publish_failed = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "scenario_id": "publish-collision",
                            "scenario_name": "Publish collision",
                            "save_scenario": "new",
                        },
                    )
                self.assertEqual(publish_failed.status_code, 400)
                self.assertIn("simulated publish collision", publish_failed.text)

                existing = infer_config(repo)
                existing["scenarioId"] = "existing-save"
                existing["scenario"] = {
                    "id": "existing-save",
                    "name": "Existing save",
                    "description": "Already published.",
                    "tags": [],
                    "source": "custom",
                    "revision": "draft",
                    "requiredSignals": [],
                }
                app.state.scenarios.save(
                    existing,
                    replace=False,
                    validate_journeys=_ui_journeys,
                )
                with patch.object(app.state.chamber, "plan") as plan:
                    existing_collision = client.post(
                        "/ui/plan",
                        data={
                            "_csrf": csrf,
                            "repo": str(repo),
                            "scenario_id": "existing-save",
                            "scenario_name": "Existing save",
                            "save_scenario": "new",
                        },
                    )
                self.assertEqual(existing_collision.status_code, 400)
                self.assertIn("confirm replacement explicitly", existing_collision.text)
                plan.assert_not_called()

            self.assertFalse((workspace / "scenarios/failed-upload-save.yaml").exists())
            self.assertFalse(list((workspace / "scenarios").glob("*.tmp")))
            self.assertFalse(list((workspace / "uploads").glob("**/*")))
            self.assertFalse(list((workspace / "drafts").glob("*.yaml")))
            self.assertFalse(list((workspace / "runs").glob("*")))

    def test_wizard_preserves_optional_json_body_and_initializes_skipped_identity(self) -> None:
        script = (Path(__file__).parents[1] / "chamber/control_plane/static/app.js").read_text(
            encoding="utf-8"
        )
        populate_start = script.index("const populateJourney = (card, journey) =>")
        populate_end = script.index("const selectedServiceName", populate_start)
        populate_source = script[populate_start:populate_end]
        self.assertIn('Object.hasOwn(journey, "body")', populate_source)
        self.assertIn("? JSON.stringify(journey.body, null, 2)", populate_source)
        self.assertIn(': "";', populate_source)

        serialize_start = script.index(
            "const serializeJourneys = ({prepareUploads = false} = {}) =>"
        )
        serialize_end = script.index("const selectModeCard", serialize_start)
        serialize_source = script[serialize_start:serialize_end]
        self.assertIn('const bodyControl = field(card, "body");', serialize_source)
        self.assertIn("if (bodyControl.value.trim()) journey.body = body;", serialize_source)
        self.assertNotIn("if (body !== null) journey.body = body;", serialize_source)

        identity_start = script.index("const ensureScenarioIdentity = () =>")
        render_start = script.index("const render = () =>", identity_start)
        review_start = script.index("if (current === panels.length - 1) updateReview(form)")
        self.assertLess(identity_start, render_start)
        self.assertIn(
            "if (current >= 2) ensureScenarioIdentity();",
            script[render_start:review_start],
        )
        self.assertIn(
            "if (!form.elements.scenario_id.value.trim()) {", script[identity_start:render_start]
        )
        self.assertIn(
            "if (!form.elements.scenario_name.value.trim()) {",
            script[identity_start:render_start],
        )
        self.assertIn('submit.addEventListener("click", ensureScenarioIdentity);', script)

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

        with self.assertRaisesRegex(ValueError, "AMPULE_CHAMBER_ADMIN_TOKEN"):
            run_server(
                host="0.0.0.0",
                port=8765,
                workspace=Path(".chamber"),
                open_browser=False,
                allow_remote=True,
            )

        with (
            patch("uvicorn.run") as uvicorn_run,
            patch("chamber.control_plane.server.threading.Timer") as timer,
            patch.dict(
                "os.environ",
                {"AMPULE_CHAMBER_ADMIN_TOKEN": "op_live_ampule_test_token_123456789"},
            ),
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
