"""Native Studio API authorization, safe uploads and operational lifecycle."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from chamber.chaos.experiments import validate_experiment
from chamber.control_plane.jobs import AssessmentJob
from chamber.control_plane.security import load_studio_auth, studio_api_allowed
from chamber.control_plane.server import create_app
from chamber.load.suite import validate_suite
from chamber.runs import initialize_run_record, new_run_directory, write_json_atomic
from chamber.workflow import infer_config
from tests.test_control_plane import _fixture_repo

ADMIN = "op_live_admin_credential_for_testing"
STUDIO = "op_live_studio_credential_for_testing"
ORIGIN = {
    "studio_service_id": "orders",
    "studio_environment": "dev",
    "studio_reference": "studio-run-123",
    "actor": "admin",
}


class StudioApiTests(unittest.TestCase):
    def test_scoped_token_auth_and_browser_csrf(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            app = create_app(Path(tmp), admin_token=ADMIN)
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/v1/capabilities").status_code, 401)
                headers = {"Authorization": f"Bearer {STUDIO}"}
                capabilities = client.get("/api/v1/capabilities", headers=headers)
                self.assertEqual(capabilities.status_code, 200)
                self.assertEqual(capabilities.json()["version"], "1.11.0")
                self.assertEqual(capabilities.json()["readiness"]["cluster"], "unchecked")
                self.assertIn("scoped_integration_token", capabilities.json()["api_features"])
                self.assertGreater(len(capabilities.json()["goals"]), 0)
                self.assertEqual(
                    client.get("/runs", headers=headers, follow_redirects=False).status_code, 303
                )
                self.assertEqual(client.get("/api/v1/unknown", headers=headers).status_code, 401)
                self.assertEqual(
                    client.post(
                        "/api/v1/inspect", headers=headers, json={"repo": "/missing"}
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.post("/api/v1/plans", headers=headers, json={"config": {}}).status_code,
                    400,
                )
                page = client.get("/login")
                self.assertEqual(page.status_code, 200)
                login = client.post(
                    "/login",
                    data={"_csrf": client.cookies["ampule_csrf"], "admin_token": STUDIO},
                    follow_redirects=False,
                )
                self.assertEqual(login.status_code, 401)
                login = client.post(
                    "/login",
                    data={"_csrf": client.cookies["ampule_csrf"], "admin_token": ADMIN},
                    follow_redirects=False,
                )
                self.assertEqual(login.status_code, 303)
                self.assertEqual(client.post("/api/v1/plans", json={"config": {}}).status_code, 403)
                self.assertEqual(
                    client.post(
                        "/api/v1/uploads",
                        files={"file": ("x.txt", b"x", "text/plain")},
                        data={"field": "file"},
                    ).status_code,
                    403,
                )
                self.assertEqual(client.get("/runs").status_code, 200)
            self.assertTrue(studio_api_allowed("POST", "/api/v1/compare"))
            self.assertFalse(studio_api_allowed("DELETE", "/api/v1/runs/abc"))
            self.assertFalse(studio_api_allowed("POST", "/ui/jobs/abc/cancel"))
        with patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": "bad"}):
            with self.assertRaisesRegex(ValueError, "AMPULE_CHAMBER_STUDIO_TOKEN"):
                load_studio_auth()

    def test_managed_upload_token_planning_and_rejections(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            headers = {"Authorization": f"Bearer {ADMIN}"}
            config = infer_config(_fixture_repo(root))
            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/v1/uploads",
                    headers=headers,
                    files={"file": ("invoice.txt", b"safe sample", "text/plain")},
                    data={"field": "file"},
                )
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                descriptor = uploaded.json()["file"]
                self.assertEqual(descriptor["size"], 11)
                self.assertEqual(Path(descriptor["path"]).read_bytes(), b"safe sample")
                config["traffic"]["journeys"] = [
                    {
                        "name": "upload",
                        "method": "POST",
                        "path": "/upload",
                        "expectedStatus": 200,
                        "requestEncoding": "multipart",
                        "multipart": {"files": [descriptor], "fields": {}},
                        "vus": 1,
                        "iterations": 1,
                        "durationSeconds": 1,
                    }
                ]
                planned = client.post(
                    "/api/v1/plans", headers=headers, json={"config": config, "origin": ORIGIN}
                )
                self.assertEqual(planned.status_code, 200, planned.text)
                run_id = planned.json()["run_id"]
                run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
                self.assertEqual(run["metadata"]["origin"], ORIGIN)
                self.assertNotIn(
                    "pathToken", run["config"]["traffic"]["journeys"][0]["multipart"]["files"][0]
                )
                for invalid in ("bad-token", None):
                    bad = copy.deepcopy(config)
                    bad["traffic"]["journeys"][0]["multipart"]["files"][0]["pathToken"] = invalid
                    self.assertEqual(
                        client.post(
                            "/api/v1/plans", headers=headers, json={"config": bad}
                        ).status_code,
                        400,
                    )
                for filename, content in (("empty.txt", b""),):
                    self.assertEqual(
                        client.post(
                            "/api/v1/uploads",
                            headers=headers,
                            files={"file": (filename, content, "text/plain")},
                            data={"field": "file"},
                        ).status_code,
                        400,
                    )
                with patch("chamber.control_plane.server.MAX_UI_UPLOAD_BYTES", 3):
                    self.assertEqual(
                        client.post(
                            "/api/v1/uploads",
                            headers=headers,
                            files={"file": ("large.txt", b"1234", "text/plain")},
                            data={"field": "file"},
                        ).status_code,
                        400,
                    )
                with patch("chamber.control_plane.server.MAX_UI_TOTAL_UPLOAD_BYTES", 3):
                    self.assertEqual(
                        client.post(
                            "/api/v1/plans", headers=headers, json={"config": config}
                        ).status_code,
                        400,
                    )
                changed = copy.deepcopy(config)
                changed["traffic"]["journeys"][0]["multipart"]["files"][0]["path"] = str(
                    root / "outside"
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/plans", headers=headers, json={"config": changed}
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/plans",
                        headers=headers,
                        json={
                            "config": config,
                            "origin": {**ORIGIN, "studio_reference": "bad\norigin"},
                        },
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.get("/api/v1/runs/missing/tasks", headers=headers).status_code, 404
                )
                for malformed in (
                    {"traffic": []},
                    {"traffic": {"journeys": [1]}},
                    {"traffic": {"journeys": [{"multipart": []}]}},
                    {"traffic": {"journeys": [{"multipart": {"files": [1]}}]}},
                ):
                    self.assertEqual(
                        client.post(
                            "/api/v1/plans", headers=headers, json={"config": malformed}
                        ).status_code,
                        400,
                    )
                legacy = copy.deepcopy(config)
                legacy["traffic"]["journeys"][0]["multipart"]["files"][0].pop("pathToken")
                self.assertEqual(
                    client.post(
                        "/api/v1/plans", headers=headers, json={"config": legacy}
                    ).status_code,
                    200,
                )

    def test_task_pagination_preserves_all_exact_ids_and_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            app = create_app(workspace, admin_token=ADMIN)
            directory = new_run_directory(workspace / "runs", "orders")
            initialize_run_record(directory)
            (directory / "evidence").mkdir(exist_ok=True)
            tasks: list[dict[str, Any]] = [
                {
                    "task_id": f"task-{i:03d}",
                    "success": i % 2 == 0,
                    "terminal_status": "completed" if i % 2 == 0 else "failed",
                    "total_duration_ms": i * 1.5,
                }
                for i in range(305)
            ]
            tasks.extend(
                [
                    {
                        "task_id": "unassigned",
                        "success": False,
                        "total_duration_ms": float("nan"),
                        "iteration": {"unsafe": True},
                        "journey": {"unsafe": True},
                        "terminal_status": 123,
                    },
                    {"task_id": None},
                    {"task_id": "bad\nID"},
                ]
            )
            write_json_atomic(directory / "evidence/relayna-summary.json", {"tasks": tasks})
            headers = {"Authorization": f"Bearer {ADMIN}"}
            with TestClient(app) as client:
                collected = []
                for page in range(1, 5):
                    result = client.get(
                        f"/api/v1/runs/{directory.name}/tasks?page={page}&page_size=100",
                        headers=headers,
                    ).json()
                    self.assertEqual(result["total_count"], 306)
                    collected.extend(result["items"])
                self.assertEqual(len(collected), 306)
                self.assertEqual(collected[304]["task_id"], "task-304")
                self.assertEqual(collected[304]["total_duration_ms"], 456.0)
                self.assertEqual(collected[305]["task_id"], "unassigned")
                self.assertIsNone(collected[305]["total_duration_ms"])
                self.assertIsNone(collected[305]["journey"])
                self.assertIsNone(collected[305]["iteration"])
                self.assertIsNone(collected[305]["terminal_status"])
                failed = client.get(
                    f"/api/v1/runs/{directory.name}/tasks?status=failed&search=task-2&failed_first=true",
                    headers=headers,
                ).json()
                self.assertEqual(failed["total_count"], 50)
                self.assertTrue(
                    all(item["terminal_status"] == "failed" for item in failed["items"])
                )
                first = client.get(
                    f"/api/v1/runs/{directory.name}/tasks?failed_first=true", headers=headers
                ).json()["items"][0]
                self.assertFalse(first["success"])
                self.assertEqual(
                    client.get(
                        f"/api/v1/runs/{directory.name}/tasks?page_size=101", headers=headers
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        f"/api/v1/runs/{directory.name}/archive",
                        headers=headers,
                        json={"archived": True},
                    ).json()["run"]["archived"],
                    True,
                )
                self.assertEqual(
                    client.post(
                        f"/api/v1/runs/{directory.name}/archive",
                        headers=headers,
                        json={"archived": False},
                    ).json()["run"]["archived"],
                    False,
                )
                tagged = client.post(
                    f"/api/v1/runs/{directory.name}/tags",
                    headers=headers,
                    json={"tags": [" release ", "release", "staging"]},
                )
                self.assertEqual(tagged.json()["run"]["tags"], ["release", "staging"])
                self.assertEqual(
                    client.post(
                        f"/api/v1/runs/{directory.name}/tags",
                        headers=headers,
                        json={"tags": ["x" * 101]},
                    ).status_code,
                    422,
                )
                for operation, payload in (("archive", {"archived": True}), ("tags", {"tags": []})):
                    self.assertEqual(
                        client.post(
                            f"/api/v1/runs/missing/{operation}", headers=headers, json=payload
                        ).status_code,
                        404,
                    )

    def test_cleanup_requires_terminal_and_explicit_confirmation(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), admin_token=ADMIN)
            job = AssessmentJob(
                "a" * 32,
                Path(tmp) / "config.yaml",
                "kubernetes",
                None,
                None,
                "2026-10-03",
                state="running",
                cleanup_required=True,
            )
            app.state.jobs._jobs[job.job_id] = job
            app.state.jobs._persist(job)
            headers = {"Authorization": f"Bearer {ADMIN}"}
            path = f"/api/v1/jobs/{job.job_id}/cleanup-verified"
            with TestClient(app) as client:
                self.assertEqual(
                    client.post(path, headers=headers, json={"confirmed": False}).status_code, 400
                )
                self.assertEqual(
                    client.post(path, headers=headers, json={"confirmed": "true"}).status_code, 422
                )
                self.assertEqual(
                    client.post(path, headers=headers, json={"confirmed": True}).status_code, 409
                )
                job.state = "failed"
                app.state.jobs._persist(job)
                verified = client.post(path, headers=headers, json={"confirmed": True})
                self.assertEqual(verified.status_code, 200)
                self.assertFalse(verified.json()["cleanup_required"])
                self.assertEqual(verified.json()["state"], "failed")
                self.assertTrue((app.state.jobs.directory / f"{job.job_id}.cleanup").exists())
                self.assertEqual(
                    client.post(
                        "/api/v1/jobs/missing/cleanup-verified",
                        headers=headers,
                        json={"confirmed": True},
                    ).status_code,
                    404,
                )

    def test_origin_inheritance_restart_and_idempotency(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            config = infer_config(_fixture_repo(root))
            headers = {
                "Authorization": f"Bearer {ADMIN}",
                "Idempotency-Key": "studio-reference-123",
            }
            with TestClient(app) as client:
                plan = client.post(
                    "/api/v1/plans", headers=headers, json={"config": config, "origin": ORIGIN}
                ).json()["run_id"]
                with patch.object(app.state.jobs, "_execute"):
                    first = client.post(
                        "/api/v1/runs", headers=headers, json={"plan_id": plan, "mode": "local"}
                    )
                    self.assertEqual(first.status_code, 202, first.text)
                    job = first.json()
                    repeated = client.post(
                        "/api/v1/runs", headers=headers, json={"plan_id": plan, "mode": "local"}
                    )
                    self.assertEqual(repeated.json()["job_id"], job["job_id"])
                    self.assertEqual(job["origin"], ORIGIN)
                    conflict = client.post(
                        "/api/v1/runs",
                        headers=headers,
                        json={
                            "plan_id": plan,
                            "mode": "local",
                            "origin": {**ORIGIN, "actor": "other"},
                        },
                    )
                    self.assertEqual(conflict.status_code, 409)
                    origin_file = root / "workspace/runs" / job["run_id"] / "caller-origin.json"
                    self.assertEqual(json.loads(origin_file.read_text()), ORIGIN)
                    self.assertEqual(
                        client.get(f"/api/v1/runs/{job['run_id']}", headers=headers).json()[
                            "metadata"
                        ]["origin"],
                        ORIGIN,
                    )
                self.assertEqual(
                    client.post(
                        "/api/v1/runs",
                        headers=headers,
                        json={"plan_id": "missing", "mode": "local"},
                    ).status_code,
                    404,
                )

    def test_full_scenario_document_preserves_advanced_fields_and_redacts_auth(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            app = create_app(Path(tmp), admin_token=ADMIN)
            headers = {"Authorization": f"Bearer {STUDIO}"}
            config = yaml.safe_load(Path("examples/sample-service/chamber-attach.yaml").read_text())
            config["scenario"] = {
                "id": "full.assessment",
                "name": "Full assessment",
                "source": "custom",
                "revision": "draft",
                "tags": ["native"],
                "requiredSignals": ["logs"],
            }
            config["agents"] = {"mode": "live", "exclude": ["traffic-chaos-agent"]}
            config["runtime"]["prometheusUrl"] = "http://metrics.monitoring:9090"
            config["runtime"]["secretEnv"] = ["API_TOKEN"]
            config["experiment"] = {
                "family": "memory_pressure",
                "pod": "api-pod",
                "container": "api",
                "memoryMiB": 64,
                "durationSeconds": 10,
                "recoverySeconds": 5,
            }
            config["chamber"] = {
                "id": "a" * 32,
                "revision": 1,
                "name": "Dev chamber",
                "context": config["runtime"]["kubernetesContext"],
                "namespace": config["runtime"]["namespace"],
                "service": "sample-service",
                "workload": "sample-service",
                "prometheus_url": config["runtime"]["prometheusUrl"],
                "allow_faults": True,
                "chaos_mesh": True,
                "max_vus": 25,
                "max_duration_seconds": 300,
            }
            config["traffic"]["load"] = {
                "model": "arrival",
                "durationSeconds": 10,
                "ratePerSecond": 1,
                "timeoutSeconds": 2,
                "maxInFlight": 2,
            }
            journey = config["traffic"]["journeys"][0]
            journey["headers"] = {
                "Authorization": "Bearer embedded-private",
                "X-Api-Key": "embedded-key",
                "Accept": "application/json",
            }
            journey["headersFromEnv"] = {"Authorization": "API_TOKEN"}
            journey["body"] = {
                "password": "private-password",
                "message": "approved input",
                "callback": "https://operator:private-url@callback.test/task?token=private-query&mode=dev",
            }
            journey["requestEncoding"] = "json"
            validate_experiment(config)
            validate_suite(config["traffic"])
            with TestClient(app) as client:
                saved = client.post("/api/v1/scenarios", headers=headers, json={"document": config})
                self.assertEqual(saved.status_code, 201, saved.text)
                response = client.get(
                    "/api/v1/scenarios/user/full.assessment/document", headers=headers
                )
                self.assertEqual(response.status_code, 200, response.text)
                envelope = response.json()
                document = envelope["document"]
                self.assertEqual(
                    envelope["schema_version"], "chamber.ampule.dev/scenario-document/v1"
                )
                self.assertEqual(document["experiment"], config["experiment"])
                self.assertEqual(document["chamber"], config["chamber"])
                self.assertEqual(document["deployment"], config["deployment"])
                self.assertEqual(document["agents"], config["agents"])
                self.assertEqual(document["runtime"], config["runtime"])
                self.assertEqual(document["traffic"]["load"], config["traffic"]["load"])
                actual = document["traffic"]["journeys"][0]
                self.assertEqual(actual["headersFromEnv"], {"Authorization": "API_TOKEN"})
                self.assertEqual(actual["headers"]["Accept"], "application/json")
                self.assertEqual(actual["headers"]["Authorization"], "[redacted]")
                self.assertEqual(actual["body"]["message"], "approved input")
                self.assertNotIn("private", response.text)
                self.assertIn(
                    "/traffic/journeys/0/headers/Authorization", envelope["redacted_fields"]
                )
                self.assertIn("/traffic/journeys/0/body/callback", envelope["redacted_fields"])
                self.assertTrue(envelope["warnings"])
                self.assertIn(
                    "scenario_document",
                    client.get("/api/v1/capabilities", headers=headers).json()["api_features"],
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/scenarios/user/missing/document", headers=headers
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/scenarios/unknown/valid/document", headers=headers
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.get("/api/v1/scenarios/user/full.assessment/document").status_code, 401
                )
                bundled = client.get(
                    "/api/v1/scenarios/bundled/baseline-health-001/document", headers=headers
                ).json()
                self.assertEqual(bundled["document"]["kind"], "Scenario")
                self.assertEqual(bundled["redacted_fields"], [])
                self.assertIn("safety", bundled["document"])
                (app.state.scenarios.user_dir / "invalid.yaml").write_text("kind: Wrong")
                self.assertEqual(
                    client.get(
                        "/api/v1/scenarios/user/invalid/document", headers=headers
                    ).status_code,
                    400,
                )

    def test_document_managed_upload_descriptor_can_be_redeemed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            headers = {"Authorization": f"Bearer {ADMIN}"}
            config = infer_config(_fixture_repo(root))
            config["scenarioId"] = "managed-document"
            with TestClient(app) as client:
                descriptor = client.post(
                    "/api/v1/uploads",
                    headers=headers,
                    files={"file": ("sample.txt", b"safe sample", "text/plain")},
                    data={"field": "file"},
                ).json()["file"]
                descriptor.pop("pathToken")
                descriptor.pop("size")
                config["traffic"]["journeys"] = [
                    {
                        "name": "upload",
                        "method": "POST",
                        "path": "/upload",
                        "expectedStatus": 200,
                        "requestEncoding": "multipart",
                        "multipart": {"files": [descriptor], "fields": {}},
                        "vus": 1,
                        "iterations": 1,
                        "durationSeconds": 1,
                    }
                ]
                saved = client.post("/api/v1/scenarios", headers=headers, json={"document": config})
                self.assertEqual(saved.status_code, 201, saved.text)
                response = client.get(
                    "/api/v1/scenarios/user/managed-document/document", headers=headers
                )
                document = response.json()["document"]
                self.assertIn(
                    "pathToken", document["traffic"]["journeys"][0]["multipart"]["files"][0]
                )
                planned = client.post("/api/v1/plans", headers=headers, json={"config": document})
                self.assertEqual(planned.status_code, 200, planned.text)
