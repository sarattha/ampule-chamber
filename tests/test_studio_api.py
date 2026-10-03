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
    def test_studio_credential_cannot_equal_admin_credential(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": ADMIN}),
        ):
            with self.assertRaisesRegex(ValueError, "STUDIO_TOKEN must differ"):
                create_app(Path(tmp), admin_token=ADMIN)
            with patch.dict("os.environ", {"AMPULE_CHAMBER_ADMIN_TOKEN": ADMIN}):
                with self.assertRaisesRegex(ValueError, "STUDIO_TOKEN must differ"):
                    create_app(Path(tmp))

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

    def test_studio_plans_require_managed_multipart_paths(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            headers = {"Authorization": f"Bearer {STUDIO}"}
            config = infer_config(_fixture_repo(root))
            with TestClient(app) as client:
                uploaded = client.post(
                    "/api/v1/uploads",
                    headers=headers,
                    files={"file": ("sample.txt", b"approved upload", "text/plain")},
                    data={"field": "file"},
                )
                self.assertEqual(uploaded.status_code, 201)
                descriptor = uploaded.json()["file"]
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
                response = client.post("/api/v1/plans", headers=headers, json={"config": config})
                self.assertEqual(response.status_code, 200, response.text)
                plan_id = response.json()["run_id"]
                with patch.object(app.state.jobs, "_execute"):
                    started = client.post(
                        "/api/v1/runs", headers=headers, json={"plan_id": plan_id, "mode": "local"}
                    )
                    self.assertEqual(started.status_code, 202, started.text)
                outside = root / "private.txt"
                outside.write_text("private container file")
                for path in (descriptor["path"], str(outside)):
                    tokenless = copy.deepcopy(config)
                    item = tokenless["traffic"]["journeys"][0]["multipart"]["files"][0]
                    item.pop("pathToken")
                    item.pop("size")
                    item["path"] = path
                    response = client.post(
                        "/api/v1/plans", headers=headers, json={"config": tokenless}
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("managed upload path token", response.json()["detail"])
                    admin_response = client.post(
                        "/api/v1/plans",
                        headers={"Authorization": f"Bearer {ADMIN}"},
                        json={"config": tokenless},
                    )
                    self.assertEqual(admin_response.status_code, 200, admin_response.text)
                    if path == str(outside):
                        with patch.object(app.state.jobs, "start") as start:
                            rejected = client.post(
                                "/api/v1/runs",
                                headers=headers,
                                json={"plan_id": admin_response.json()["run_id"], "mode": "local"},
                            )
                            self.assertEqual(rejected.status_code, 409)
                            start.assert_not_called()
                            stored = root / "workspace" / "external.yaml"
                            stored.write_text(yaml.safe_dump(tokenless))
                            rejected = client.post(
                                "/api/v1/runs",
                                headers=headers,
                                json={"config_path": str(stored), "mode": "local"},
                            )
                            self.assertEqual(rejected.status_code, 409)
                            start.assert_not_called()
                optional = copy.deepcopy(config)
                optional["traffic"]["journeys"][0]["multipart"]["files"].append(
                    {"field": "optional", "required": False}
                )
                response = client.post("/api/v1/plans", headers=headers, json={"config": optional})
                self.assertEqual(response.status_code, 200, response.text)

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
                "X-Auth": "private-x-auth",
                "Authentication": "private-authentication",
                "X-Credential": "private-credential",
                "X-Custom-Token": "private-custom-token",
                "X-Service-Api-Key": "private-service-key",
                "X-Client-Secret": "private-client-secret",
                "Accept": "application/json",
                "X-Idempotency-Key": "approved-request-id",
            }
            journey["headersFromEnv"] = {"Authorization": "API_TOKEN", "X-Auth": "TARGET_AUTH"}
            journey["body"] = {
                "password": "private-password",
                "privateKey": "private-key-material",
                "accessKey": "private-access-key",
                "signingKey": "private-signing-key",
                "message": "approved input",
                "callback": "https://operator:private-url@callback.test/task?token=private-query&auth=private-auth-query&credential=private-credential-query&x-auth=private-custom-query&mode=dev",
            }
            journey["requestEncoding"] = "json"
            validate_experiment(config)
            validate_suite(config["traffic"])
            with TestClient(app) as client:
                validated = client.post(
                    "/api/v1/scenarios/validate-document",
                    headers=headers,
                    json={"content": yaml.safe_dump(config), "service_name": "another-service"},
                )
                self.assertEqual(validated.status_code, 200, validated.text)
                validation = validated.json()
                self.assertEqual(validation["document"]["experiment"], config["experiment"])
                self.assertEqual(validation["document"]["chamber"], config["chamber"])
                self.assertEqual(validation["document"]["agents"], config["agents"])
                self.assertEqual(validation["document"]["runtime"], config["runtime"])
                self.assertEqual(
                    validation["document"]["traffic"]["load"], config["traffic"]["load"]
                )
                self.assertEqual(validation["source"], "imported")
                self.assertEqual(validation["normalized"]["kind"], "ChamberConfig")
                self.assertNotIn("private-", validated.text)
                self.assertEqual(
                    validation["normalized"]["journeys"][0]["body"]["password"], "[redacted]"
                )
                self.assertTrue(any("another-service" in item for item in validation["warnings"]))
                self.assertTrue(validation["redacted_fields"])
                legacy = client.post(
                    "/api/v1/scenarios/validate",
                    headers=headers,
                    json={"content": yaml.safe_dump(config)},
                )
                self.assertEqual(legacy.status_code, 200)
                self.assertIn("journeys", legacy.json())
                self.assertNotIn("document", legacy.json())
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
                self.assertEqual(actual["headersFromEnv"], journey["headersFromEnv"])
                self.assertEqual(actual["headers"]["Accept"], "application/json")
                self.assertEqual(actual["headers"]["Authorization"], "[redacted]")
                self.assertEqual(actual["headers"]["X-Idempotency-Key"], "approved-request-id")
                for name in (
                    "X-Auth",
                    "Authentication",
                    "X-Credential",
                    "X-Custom-Token",
                    "X-Service-Api-Key",
                    "X-Client-Secret",
                ):
                    self.assertEqual(actual["headers"][name], "[redacted]")
                    self.assertEqual(
                        validation["normalized"]["journeys"][0]["headers"][name], "[redacted]"
                    )
                    self.assertIn(
                        f"/traffic/journeys/0/headers/{name}", envelope["redacted_fields"]
                    )
                for name in ("privateKey", "accessKey", "signingKey"):
                    self.assertEqual(actual["body"][name], "[redacted]")
                    self.assertEqual(
                        validation["normalized"]["journeys"][0]["body"][name], "[redacted]"
                    )
                    self.assertIn(f"/traffic/journeys/0/body/{name}", envelope["redacted_fields"])
                self.assertEqual(actual["body"]["message"], "approved input")
                self.assertNotIn("private-", response.text)
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
                validated = client.post(
                    "/api/v1/scenarios/validate-document",
                    headers=headers,
                    json={"content": json.dumps(document)},
                )
                self.assertEqual(validated.status_code, 200, validated.text)
                self.assertIn(
                    "pathToken",
                    validated.json()["document"]["traffic"]["journeys"][0]["multipart"]["files"][0],
                )
                self.assertIn(
                    "pathToken",
                    validated.json()["normalized"]["journeys"][0]["multipart"]["files"][0],
                )
                planned = client.post("/api/v1/plans", headers=headers, json={"config": document})
                self.assertEqual(planned.status_code, 200, planned.text)

    def test_studio_legacy_and_run_projections_redact_credentials_only_for_studio(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            admin = {"Authorization": f"Bearer {ADMIN}"}
            studio = {"Authorization": f"Bearer {STUDIO}"}
            config = infer_config(_fixture_repo(root))
            config["scenarioId"] = "legacy-credentials"
            journey = config["traffic"]["journeys"][0]
            journey.update(
                method="POST",
                requestEncoding="json",
                headers={"Authorization": "Bearer private-auth", "X-Credential": "private-custom"},
                headersFromEnv={"X-Auth": "TARGET_AUTH"},
                body={
                    "password": "private-body",
                    "message": "approved input",
                    "auth": "private-body-auth",
                    "credential": "private-body-credential",
                    "privateKey": "private-key-material",
                    "accessKey": "private-access-key",
                    "signingKey": "private-signing-key",
                    "callback": "https://target.test/callback?auth=private-url-auth&credential=private-url-credential&format=json",
                },
            )
            with TestClient(app) as client:
                saved = client.post("/api/v1/scenarios", headers=admin, json={"document": config})
                self.assertEqual(saved.status_code, 201, saved.text)
                self.assertIn("private-auth", saved.text)
                scenario_path = "/api/v1/scenarios/user/legacy-credentials"
                response = client.get(scenario_path, headers=studio)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("private-", response.text)
                self.assertIn(
                    "/journeys/0/headers/Authorization", response.json()["redacted_fields"]
                )
                self.assertEqual(
                    response.json()["journeys"][0]["headersFromEnv"], {"X-Auth": "TARGET_AUTH"}
                )
                self.assertTrue(response.json()["warnings"])
                self.assertIn("private-auth", client.get(scenario_path, headers=admin).text)
                for path, payload in (
                    ("/api/v1/scenarios/validate", {"content": yaml.safe_dump(config)}),
                    ("/api/v1/scenarios", {"document": config, "replace": True}),
                ):
                    response = client.post(path, headers=studio, json=payload)
                    self.assertIn(response.status_code, (200, 201), response.text)
                    self.assertNotIn("private-", response.text)
                    self.assertTrue(response.json()["redacted_fields"])
                planned = client.post("/api/v1/plans", headers=admin, json={"config": config})
                self.assertEqual(planned.status_code, 200, planned.text)
                run_id = planned.json()["run_id"]
                run_path = f"/api/v1/runs/{run_id}"
                directory = app.state.chamber.run_path(run_id)
                stored = yaml.safe_load((directory / "chamber.yaml").read_text())
                stored["runtime"]["secretEnv"] = ["CUSTOM_SETTING"]
                stored["runtime"]["config"] = {"CUSTOM_SETTING": "private-environment"}
                (directory / "chamber.yaml").write_text(yaml.safe_dump(stored))
                for detail in ("true", "false"):
                    suffix = f"?include_task_details={detail}"
                    response = client.get(run_path + suffix, headers=studio)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertNotIn("private-", response.text)
                    projected = response.json()["config"]
                    self.assertEqual(projected["runtime"]["config"]["CUSTOM_SETTING"], "[redacted]")
                    self.assertEqual(
                        projected["traffic"]["journeys"][0]["body"]["message"], "approved input"
                    )
                    for operation, payload in (
                        ("archive", {"archived": True}),
                        ("tags", {"tags": ["safe"]}),
                    ):
                        response = client.post(
                            run_path + "/" + operation + suffix, headers=studio, json=payload
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertNotIn("private-", response.text)
                    response = client.get(run_path + suffix, headers=admin)
                    self.assertIn("private-auth", response.text)
                    self.assertIn("private-environment", response.text)
                    self.assertNotIn("redacted_fields", response.json())
                self.assertIn("private-auth", (directory / "chamber.yaml").read_text())
                report = client.get(run_path + "/report?format=html", headers=studio)
                self.assertEqual(report.status_code, 200, report.text)
                self.assertNotIn("private-", report.text)
                admin_report = client.get(run_path + "/report?format=html", headers=admin)
                self.assertIn("private-auth", admin_report.text)

    def test_studio_comparison_omits_serialized_credentials_without_changing_compatibility(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            workspace = Path(tmp)
            app = create_app(workspace, admin_token=ADMIN)
            ids = []
            for credential in ("private-baseline", "private-candidate"):
                directory = new_run_directory(workspace / "runs", "comparison")
                initialize_run_record(directory)
                config = {
                    "service": {"name": "orders"},
                    "scenario": {"id": "baseline", "revision": "same"},
                    "runtime": {
                        "provider": "kubernetes",
                        "mode": "attach",
                        "prometheusUrl": "https://metrics-user:private-metrics@prometheus.test",
                    },
                    "traffic": {
                        "load": {"model": "arrival"},
                        "journeys": [{"headers": {"Authorization": credential}}],
                    },
                    "experiment": {"family": "dependency_outage", "password": credential},
                }
                (directory / "chamber.yaml").write_text(yaml.safe_dump(config))
                ids.append(directory.name)
            with TestClient(app) as client:
                payload = {"baseline_run_id": ids[0], "candidate_run_id": ids[1]}
                admin = client.post(
                    "/api/v1/compare", headers={"Authorization": f"Bearer {ADMIN}"}, json=payload
                )
                studio = client.post(
                    "/api/v1/compare", headers={"Authorization": f"Bearer {STUDIO}"}, json=payload
                )
                self.assertEqual(admin.status_code, 200, admin.text)
                self.assertEqual(studio.status_code, 200, studio.text)
                self.assertIn("private-baseline", admin.text)
                self.assertNotIn("private-", studio.text)
                self.assertEqual(admin.json()["compatible"], studio.json()["compatible"])
                self.assertFalse(studio.json()["compatible"])
                self.assertEqual(
                    [item["compatible"] for item in admin.json()["compatibility_reasons"]],
                    [item["compatible"] for item in studio.json()["compatibility_reasons"]],
                )

    def test_studio_chamber_responses_redact_url_credentials_and_preserve_admin(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            app = create_app(Path(tmp), admin_token=ADMIN)
            studio = {"Authorization": f"Bearer {STUDIO}"}
            admin = {"Authorization": f"Bearer {ADMIN}"}
            profile = {
                "name": "metrics",
                "context": "dev",
                "namespace": "dev",
                "service": "orders",
                "workload": "orders",
                "prometheus_url": "HTTPS://operator:private-userinfo@metrics.test/api?auth=private-auth&credential=private-credential&x-token=private-token&client-secret=private-secret&format=json",
            }
            with TestClient(app) as client:
                created = client.post("/api/v1/chambers", headers=admin, json=profile)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertIn("private-userinfo", created.text)
                studio_created = client.post("/api/v1/chambers", headers=studio, json=profile)
                self.assertEqual(studio_created.status_code, 201, studio_created.text)
                self.assertNotIn("private-", studio_created.text)
                self.assertIn("/prometheus_url", studio_created.json()["redacted_fields"])
                malformed = {
                    **profile,
                    "prometheus_url": "https://operator:private-broken@[invalid",
                }
                self.assertEqual(
                    client.post("/api/v1/chambers", headers=admin, json=malformed).status_code, 201
                )
                response = client.get("/api/v1/chambers", headers=studio)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("private-", response.text)
                self.assertTrue(response.json()["redacted_fields"])
                self.assertTrue(response.json()["warnings"])
                for chamber in response.json()["chambers"]:
                    if chamber["prometheus_url"] != "[redacted]":
                        self.assertIn("format=json", chamber["prometheus_url"])
                        self.assertNotIn("operator", chamber["prometheus_url"])
                self.assertIn("private-auth", client.get("/api/v1/chambers", headers=admin).text)
                self.assertIn(
                    "private-auth",
                    client.get(f"/chambers?clone={created.json()['id']}", headers=admin).text,
                )
                persisted = Path(tmp) / "chambers" / f"{studio_created.json()['id']}.json"
                self.assertIn("private-userinfo", persisted.read_text())

    def test_studio_job_responses_redact_credentials_and_preserve_persisted_jobs(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            root = Path(tmp)
            app = create_app(root / "workspace", admin_token=ADMIN)
            admin = {"Authorization": f"Bearer {ADMIN}"}
            studio = {"Authorization": f"Bearer {STUDIO}"}
            url = "https://operator:private-userinfo@metrics.test?token=private-query&accessKey=private-access-key&mode=dev"
            config = infer_config(_fixture_repo(root))
            with TestClient(app) as client:
                planned = client.post("/api/v1/plans", headers=admin, json={"config": config})
                with patch.object(app.state.jobs, "_execute"):
                    started = client.post(
                        "/api/v1/runs",
                        headers=studio,
                        json={
                            "plan_id": planned.json()["run_id"],
                            "mode": "local",
                            "prometheus_url": url,
                        },
                    )
                self.assertEqual(started.status_code, 202, started.text)
                self.assertNotIn("private-", started.text)
                job_id = started.json()["job_id"]
                job = app.state.jobs._jobs[job_id]
                job.state = "failed"
                job.cleanup_required = True
                job.output = f"Metrics request failed ({url})."
                job.error = f"Try {url} again."
                app.state.jobs._persist(job)
                path = f"/api/v1/jobs/{job_id}"
                for suffix in ("", "/events"):
                    response = client.get(path + suffix, headers=studio)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertNotIn("private-", response.text)
                    self.assertIn("Metrics request failed", response.text)
                    self.assertIn("private-userinfo", client.get(path + suffix, headers=admin).text)
                for operation, payload in (
                    ("cancel", {}),
                    ("cleanup-verified", {"confirmed": True}),
                ):
                    response = client.post(path + "/" + operation, headers=studio, json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertNotIn("private-", response.text)
                    self.assertEqual(response.json()["state"], "failed")
                self.assertIn("private-userinfo", app.state.jobs.get(job_id)["prometheus_url"])
                persisted = app.state.jobs.directory / f"{job_id}.json"
                self.assertIn("private-userinfo", persisted.read_text())

    def test_studio_run_event_stream_redacts_credentials_without_changing_admin_stream(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            workspace = Path(tmp)
            app = create_app(workspace, admin_token=ADMIN)
            directory = new_run_directory(workspace / "runs", "events")
            initialize_run_record(directory)
            record = json.loads((directory / "run.json").read_text())
            record["state"] = "completed"
            write_json_atomic(directory / "run.json", record)
            event = {
                "state": "completed",
                "privateKey": "private-key-material",
                "message": (
                    "Metrics https://operator:private-url@metrics.test?auth=private-query failed."
                ),
            }
            (directory / "events.jsonl").write_text(
                json.dumps(event) + "\ninvalid private-record\n"
            )
            with TestClient(app) as client:
                path = f"/api/v1/runs/{directory.name}/events"
                response = client.get(path, headers={"Authorization": f"Bearer {STUDIO}"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("private-", response.text)
                self.assertIn("id: 1", response.text)
                self.assertIn("completed", response.text)
                admin = client.get(path, headers={"Authorization": f"Bearer {ADMIN}"})
                self.assertIn("private-key-material", admin.text)
                self.assertIn("invalid private-record", admin.text)

    def test_run_summary_bounds_large_task_events_http_views_and_preserves_default(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            app = create_app(workspace, admin_token=ADMIN)
            directory = new_run_directory(workspace / "runs", "large-run")
            initialize_run_record(directory)
            (directory / "evidence").mkdir(exist_ok=True)
            tasks = [
                {
                    "task_id": f"task-{i:03d}",
                    "success": i % 2 == 0,
                    "terminal_status": "completed" if i % 2 == 0 else "failed",
                    "total_duration_ms": i * 1.5,
                    "events": [
                        {
                            "status": "processing",
                            "timestamp": "2026-10-03T01:00:00+00:00",
                            "worker_id": "worker-12345678",
                            "sequence": n,
                        }
                        for n in range(200)
                    ],
                }
                for i in range(305)
            ]
            write_json_atomic(
                directory / "evidence/relayna-summary.json",
                {"success": True, "task_count": 305, "tasks": tasks},
            )
            write_json_atomic(
                directory / "result.json",
                {
                    "load": {"windows": [{"details": "w" * 30000, "index": n} for n in range(120)]},
                    "status": "ready",
                    "readiness_score": 100,
                    "evidence_coverage_percent": 100,
                },
            )
            write_json_atomic(directory / "caller-origin.json", ORIGIN)
            (directory / "report.md").write_text("r" * 100000)
            (directory / "events.jsonl").write_text(
                "\n".join(
                    json.dumps({"sequence": n, "state": "running", "details": "event" * 100})
                    for n in range(500)
                )
            )
            headers = {"Authorization": f"Bearer {ADMIN}"}
            with TestClient(app) as client:
                response = client.get(
                    f"/api/v1/runs/{directory.name}?include_task_details=false", headers=headers
                )
                self.assertEqual(response.status_code, 200, response.text[:1000])
                self.assertLess(len(response.content), 2 * 1024 * 1024)
                summary = response.json()
                self.assertEqual(summary["relayna"]["total_task_count"], 305)
                self.assertEqual(summary["total_task_count"], 305)
                self.assertTrue(summary["tasks_truncated"])
                self.assertTrue(summary["relayna"]["tasks_truncated"])
                self.assertEqual(len(summary["relayna"]["tasks"]), 25)
                self.assertEqual(summary["relayna"]["tasks"][24]["task_id"], "task-024")
                self.assertNotIn("events", summary["relayna"]["tasks"][0])
                self.assertEqual(summary["result"]["status"], "ready")
                self.assertEqual(summary["result"]["readiness_score"], 100)
                self.assertEqual(summary["metadata"]["origin"], ORIGIN)
                self.assertTrue(summary["summary"]["truncated_fields"])
                self.assertLessEqual(len(summary["events"]), 100)
                self.assertLess(len(summary["report_markdown"]), 100000)
                self.assertTrue(
                    any(
                        item["path"].startswith("/result/load")
                        for item in summary["summary"]["truncated_fields"]
                    )
                )
                for operation, payload, field, expected in (
                    ("archive", {"archived": True}, "archived", True),
                    ("tags", {"tags": ["summary", "native"]}, "tags", ["summary", "native"]),
                ):
                    bounded = client.post(
                        f"/api/v1/runs/{directory.name}/{operation}?include_task_details=false",
                        headers=headers,
                        json=payload,
                    )
                    self.assertEqual(bounded.status_code, 200, bounded.text[:1000])
                    self.assertLess(len(bounded.content), 2 * 1024 * 1024)
                    changed = bounded.json()
                    self.assertEqual(changed["run"][field], expected)
                    self.assertEqual(changed["total_task_count"], 305)
                    self.assertEqual(len(changed["relayna"]["tasks"]), 25)
                    self.assertNotIn("events", changed["relayna"]["tasks"][0])
                    self.assertTrue(changed["summary"]["truncated_fields"])
                    compatible = client.post(
                        f"/api/v1/runs/{directory.name}/{operation}",
                        headers=headers,
                        json=payload,
                    ).json()
                    self.assertEqual(compatible["run"][field], expected)
                    self.assertEqual(len(compatible["relayna"]["tasks"]), 305)
                    self.assertEqual(len(compatible["relayna"]["tasks"][0]["events"]), 200)
                    self.assertEqual(len(compatible["result"]["load"]["windows"]), 120)
                    self.assertNotIn("summary", compatible)
                default = client.get(f"/api/v1/runs/{directory.name}", headers=headers).json()
                self.assertEqual(len(default["relayna"]["tasks"]), 305)
                self.assertEqual(len(default["relayna"]["tasks"][0]["events"]), 200)
                self.assertEqual(len(default["result"]["load"]["windows"]), 120)
                self.assertEqual(len(default["events"]), 500)
                self.assertEqual(len(default["report_markdown"]), 100000)
                self.assertNotIn("summary", default)
                self.assertIn(
                    "run_summary",
                    client.get("/api/v1/capabilities", headers=headers).json()["api_features"],
                )

    def test_rerun_inherits_source_caller_origin(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), admin_token=ADMIN)
            config = yaml.safe_load(Path("examples/sample-service/chamber-attach.yaml").read_text())
            headers = {"Authorization": f"Bearer {ADMIN}"}
            with TestClient(app) as client:
                plan = client.post(
                    "/api/v1/plans", headers=headers, json={"config": config, "origin": ORIGIN}
                ).json()["run_id"]
                source = app.state.chamber.run_path(plan)
                write_json_atomic(source / "result.json", {"status": "ready"})
                rerun = client.post(f"/api/v1/runs/{plan}/rerun", headers=headers, json={})
                self.assertEqual(rerun.status_code, 201, rerun.text)
                run_id = rerun.json()["run_id"]
                detail = client.get(
                    f"/api/v1/runs/{run_id}?include_task_details=false", headers=headers
                ).json()
                self.assertEqual(detail["metadata"]["origin"], ORIGIN)
                self.assertEqual(detail["run"]["parent_run_id"], plan)
                self.assertEqual(detail["total_task_count"], 0)
                self.assertFalse(detail["tasks_truncated"])
                with patch.object(app.state.jobs, "_execute"):
                    started = client.post(
                        "/api/v1/runs",
                        headers=headers,
                        json={"plan_id": run_id, "mode": "kubernetes"},
                    )
                    self.assertEqual(started.status_code, 202, started.text)
                    self.assertEqual(started.json()["origin"], ORIGIN)

    def test_validate_document_requires_auth_csrf_and_valid_bounded_input(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"AMPULE_CHAMBER_STUDIO_TOKEN": STUDIO}),
        ):
            app = create_app(Path(tmp), admin_token=ADMIN)
            config = Path("examples/sample-service/chamber-attach.yaml").read_text()
            headers = {"Authorization": f"Bearer {STUDIO}"}
            with TestClient(app) as client:
                path = "/api/v1/scenarios/validate-document"
                self.assertEqual(client.post(path, json={"content": config}).status_code, 401)
                self.assertEqual(
                    client.post(path, headers=headers, json={"content": config}).status_code, 200
                )
                for bad in ("bad: [", "[1, 2, 3]", "kind: Wrong", "x" * (256 * 1024 + 1)):
                    self.assertEqual(
                        client.post(path, headers=headers, json={"content": bad}).status_code, 400
                    )
                capabilities = client.get("/api/v1/capabilities", headers=headers).json()
                self.assertIn("validate_document", capabilities["api_features"])
                client.get("/login")
                csrf = client.cookies["ampule_csrf"]
                client.post("/login", data={"_csrf": csrf, "admin_token": ADMIN})
                self.assertEqual(client.post(path, json={"content": config}).status_code, 403)
                self.assertEqual(
                    client.post(
                        path, headers={"X-CSRF-Token": csrf}, json={"content": config}
                    ).status_code,
                    200,
                )
                self.assertFalse(
                    any(
                        item["source"] == "user"
                        for item in client.get("/api/v1/scenarios").json()["scenarios"]
                    )
                )
