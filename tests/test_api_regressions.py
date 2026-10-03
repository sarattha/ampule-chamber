"""Regression contracts for malformed intake and interrupted supervisors."""

from __future__ import annotations

import asyncio
import copy
import io
import json
import signal
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from chamber.control_plane import scenarios, server
from chamber.control_plane.discovery import (
    DiscoveryError,
    DiscoverySettings,
    KubernetesDiscovery,
    SubprocessDiscoveryRunner,
)
from chamber.control_plane.goals import propose_goal
from chamber.control_plane.jobs import AssessmentJob, AssessmentJobManager, _command, _signal
from chamber.runs import initialize_run_record, new_run_directory, write_json_atomic
from tests.test_scenario_catalog import BUNDLED


class IntakeRegressionTests(unittest.TestCase):
    def test_goal_discovery_context_and_dependency_assumptions(self) -> None:
        proposal = propose_goal(
            "dependency_degradation",
            service_name="orders",
            workload_name="worker",
            service_port=8080,
            attach_mode=True,
            repository_available=True,
            discovery_complete=True,
            dependency_names=(" redis ", ""),
        )
        self.assertIn("Declared dependencies: redis.", proposal["assumptions"])
        self.assertTrue(any("workload 'worker'" in value for value in proposal["assumptions"]))
        self.assertEqual(proposal["missingInputs"], [])
        missing = propose_goal("dependency_degradation", attach_mode=True)
        self.assertIn("Select or discover the target Service name.", missing["missingInputs"])
        self.assertIn("Confirm the target Service port.", missing["missingInputs"])
        self.assertIn(
            "Choose the dependency and its bounded degradation mechanism.", missing["missingInputs"]
        )
        memory = propose_goal(
            "memory_oom",
            service_name="orders",
            service_port=80,
            telemetry_available=("prometheus",),
        )
        self.assertEqual(len(memory["missingInputs"]), 1)
        with self.assertRaisesRegex(ValueError, "unknown reliability goal"):
            propose_goal("unknown")
        with patch(
            "chamber.control_plane.discovery.subprocess.run",
            return_value=subprocess.CompletedProcess(["kubectl"], 0, "{}", ""),
        ) as run:
            self.assertEqual(SubprocessDiscoveryRunner().run(("kubectl",)).returncode, 0)
            run.assert_called_once_with(("kubectl",), check=False, capture_output=True, text=True)
        runner = MagicMock()
        discovery = KubernetesDiscovery(DiscoverySettings("in-cluster", ("qa",)), runner=runner)
        for payload, message in (
            ("[]", "no item list"),
            ('{"items":{}}', "no item list"),
            ("broken", "invalid JSON"),
        ):
            runner.run.return_value = subprocess.CompletedProcess([], 0, payload, "")
            with self.subTest(payload=payload), self.assertRaisesRegex(DiscoveryError, message):
                discovery.discover(context="", namespace="qa")
        runner.run.return_value = subprocess.CompletedProcess(
            [], 0, '{"items":[1,{"spec":{"ports":[1,{"port":"bad"},{"port":80}]}}]}', ""
        )
        result = discovery.discover(context="", namespace="qa")
        self.assertEqual(result["services"][0]["ports"][0]["port"], 80)

    def test_catalog_symlink_containment_and_alternate_bundled_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled"
            bundled.mkdir()
            catalog = scenarios.ScenarioCatalog(root / "workspace", bundled)
            outside = root / "outside.yaml"
            outside.write_text("{}")
            (catalog.user_dir / "escape.yaml").symlink_to(outside)
            with self.assertRaisesRegex(scenarios.ScenarioCatalogError, "outside the catalog"):
                catalog.read("user", "escape", server._ui_journeys)
            document = scenarios.parse_scenario_document(
                (BUNDLED / "baseline-health.yaml").read_text()
            )
            document["metadata"]["id"] = "logical-id"
            import yaml

            (bundled / "different-filename.yaml").write_text(yaml.safe_dump(document))
            (bundled / "broken.yaml").write_text("bad: [")
            normalized = catalog.read("bundled", "logical-id", server._ui_journeys)
            self.assertEqual(normalized["identity"]["id"], "logical-id")
            with self.assertRaisesRegex(scenarios.ScenarioCatalogError, "saved as a ChamberConfig"):
                catalog.prepare_save(document, replace=False, validate_journeys=server._ui_journeys)
            with (
                patch("chamber.control_plane.scenarios.MAX_SCENARIO_BYTES", 1),
                patch(
                    "chamber.control_plane.scenarios.normalize_document",
                    return_value={"kind": "ChamberConfig", "identity": {"id": "safe"}},
                ),
            ):
                with self.assertRaisesRegex(scenarios.ScenarioCatalogError, "256 KiB"):
                    catalog.prepare_save({}, replace=False, validate_journeys=server._ui_journeys)
        self.assertIsNone(scenarios._first_service_port({}))
        self.assertIsNone(scenarios._first_service_port([{"port": True}]))
        self.assertEqual(
            scenarios._first_service_port([None, {"port": True}, {"port": -1}, {"port": 8080}]),
            8080,
        )
        with self.assertRaisesRegex(scenarios.ScenarioCatalogError, "unsupported values"):
            scenarios._bounded({"bad": object()})
        with self.assertRaisesRegex(scenarios.ScenarioCatalogError, "256 KiB"):
            scenarios._bounded({"large": "x" * scenarios.MAX_SCENARIO_BYTES})
        with self.assertRaises(OSError):
            scenarios._read_document(Path("/missing-ampule-document"))

    def test_safety_projection_rejects_invalid_limits_and_stages(self) -> None:
        for safety, journeys, message in (
            ({"maxVirtualUsers": "0", "maxDuration": "1m"}, [], "positive integer"),
            ({"maxVirtualUsers": "10", "maxDuration": "bad"}, [], "maxDuration"),
            (
                {"maxVirtualUsers": "10", "maxDuration": "1m"},
                [{"stages": [{"duration": "bad"}]}],
                "stage duration",
            ),
            ({"maxVirtualUsers": "1", "maxDuration": "1m"}, [{"vus": 2}], "requires 2 VUs"),
            (
                {"maxVirtualUsers": "10", "maxDuration": "1s"},
                [{"durationSeconds": 2}],
                "duration 2s",
            ),
        ):
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(scenarios.ScenarioCatalogError, message),
            ):
                scenarios._validate_scenario_safety({"safety": safety}, journeys)
        scenarios._validate_scenario_safety(
            {"safety": {"maxVirtualUsers": "10", "maxDuration": "1m"}},
            [{"stages": {}}, {"stages": [None, {"duration": "1s", "targetVus": 1}]}],
        )

    def test_journey_validation_errors_are_specific_and_do_not_mutate_inputs(self) -> None:
        valid: dict[str, Any] = {
            "name": "health",
            "path": "/health",
            "method": "GET",
            "expectedStatus": 200,
            "vus": 1,
        }
        cases: list[tuple[dict[str, Any], str]] = [
            ({"method": ""}, "HTTP method"),
            ({"requestEncoding": "binary"}, "requestEncoding"),
            ({"stages": []}, "non-empty array"),
            ({"stages": [1]}, "JSON object"),
            ({"stages": [{"duration": "", "targetVus": 1}]}, "duration"),
            ({"stages": [{"duration": "1s", "targetVus": True}]}, "non-negative integer"),
            ({"vus": False}, "positive integer"),
            ({"iterations": 0}, "positive integer"),
            ({"durationSeconds": -1}, "positive integer"),
            ({"textBytes": -1}, "non-negative integer"),
            ({"requestEncoding": "multipart", "multipart": {}}, "at least one file"),
            ({"requestEncoding": "multipart", "multipart": {"fields": [], "files": []}}, "fields"),
            ({"requestEncoding": "multipart", "multipart": {"files": [1]}}, "JSON object"),
            (
                {"requestEncoding": "multipart", "multipart": {"files": [{"field": ""}]}},
                "requires field",
            ),
            (
                {
                    "requestEncoding": "multipart",
                    "multipart": {"files": [{"field": "file", "required": "yes"}]},
                },
                "boolean",
            ),
            ({"requestEncoding": "raw", "body": 3}, "string"),
            ({"requestEncoding": "raw", "body": "safe", "contentType": ""}, "contentType"),
            ({"followUps": []}, "non-empty array"),
            ({"followUps": [1]}, "JSON object"),
            ({"followUps": [{"name": ""}]}, "requires name"),
        ]
        for change, message in cases:
            candidate = {**valid, **change}
            original = copy.deepcopy(candidate)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                server._ui_journeys(json.dumps([candidate]))
            self.assertEqual(candidate, original)
        follow = {"name": "verify", "type": "read", "target": "/result", "expected": "ready"}
        result = server._ui_journeys(
            json.dumps(
                [
                    {
                        **valid,
                        "textBytes": 0,
                        "followUps": [follow],
                        "requestEncoding": "multipart",
                        "multipart": {"files": [{"field": "optional", "required": False}]},
                    }
                ]
            )
        )
        self.assertEqual(result[0]["followUps"], [follow])
        duplicate = {
            **valid,
            "requestEncoding": "multipart",
            "multipart": {
                "files": [{"field": "a", "required": False}, {"field": "a", "required": False}]
            },
        }
        with self.assertRaisesRegex(ValueError, "unique"):
            server._ui_journeys(json.dumps([duplicate]))

    def test_browser_upload_reference_validation_cleans_partial_directories(self) -> None:
        def upload(
            filename: str | None = "safe.txt", content_type: str = "text/plain"
        ) -> UploadFile:
            return UploadFile(
                io.BytesIO(b"data"),
                filename=filename,
                headers=Headers({"content-type": content_type}),
            )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def persist(
                entries: list[dict[str, Any]], uploads: list[UploadFile]
            ) -> tuple[str, Path | None]:
                return asyncio.run(
                    server._persist_journey_files(
                        root,
                        json.dumps([{"multipart": {"files": entries}}]),
                        uploads,
                        path_secret=b"test",
                    )
                )

            for entries, uploads, message in (
                ([{"field": "f", "required": "yes"}], [], "true or false"),
                ([{"field": "f", "required": "yes"}], [upload()], "true or false"),
                ([{"field": "f"}], [upload()], "missing its browser"),
                ([{"field": "f", "uploadIndex": True}], [upload()], "browser upload reference"),
                (
                    [{"field": "f", "uploadIndex": 0}, {"field": "g", "uploadIndex": 0}],
                    [upload()],
                    "cannot be reused",
                ),
                ([{"field": "f", "uploadIndex": 0}], [upload(None)], "filename"),
                ([{"field": "f", "uploadIndex": 0}], [upload(content_type="")], "content type"),
                ([], [upload()], "Every browser-uploaded"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    persist(entries, uploads)
                self.assertEqual(list((root / "uploads").glob("*")), [])
            with self.assertRaisesRegex(ValueError, "JSON array"):
                asyncio.run(server._persist_journey_files(root, "{}", []))
            with self.assertRaisesRegex(ValueError, "valid JSON"):
                asyncio.run(server._persist_journey_files(root, "[", []))
            raw, directory = persist(
                [{"field": "optional", "required": False, "filename": "unused"}], []
            )
            self.assertNotIn("filename", json.loads(raw)[0]["multipart"]["files"][0])
            self.assertIsNone(directory)
            self.assertEqual(asyncio.run(server._persist_journey_files(root, " ", [])), (" ", None))
            self.assertEqual(
                asyncio.run(
                    server._persist_journey_files(root, '[1,{}, {"multipart":{"files":{}}}]', [])
                )[1],
                None,
            )
            self.assertIsNone(server._managed_multipart_path(3, root))
            self.assertIsNone(server._redeem_multipart_path("bad", "bad", None, root))


class SupervisorRegressionTests(unittest.TestCase):
    def test_interrupted_supervisor_kills_live_child_and_records_failure(self) -> None:
        class ImmediateThread:
            def __init__(self, *, target: Any, daemon: bool) -> None:
                self.target = target

            def start(self) -> None:
                self.target()

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text("{}")
            manager = AssessmentJobManager(root / "workspace")
            process = MagicMock()
            process.poll.return_value = None

            def fail(job_id: str) -> None:
                manager._jobs[job_id].process = process
                raise RuntimeError("lost supervisor")

            with (
                patch.object(manager, "_execute", side_effect=fail),
                patch("chamber.control_plane.jobs.threading.Thread", ImmediateThread),
                patch("chamber.control_plane.jobs._signal") as kill,
            ):
                result = manager.start(config, mode="kubernetes")
            kill.assert_called_once_with(process, signal.SIGKILL)
            self.assertEqual(result["state"], "failed")
            self.assertIn("lost supervisor", result["error"])
            self.assertTrue(result["cleanup_required"])
            self.assertEqual(manager.get(result["job_id"])["state"], "failed")
            path = manager.directory / f"{result['job_id']}.json"
            path.unlink()
            self.assertEqual(manager.get(result["job_id"])["state"], "failed")
            with self.assertRaises(KeyError):
                manager.get("f" * 32)

    def test_failure_projection_preserves_honest_cleanup_and_command_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AssessmentJobManager(root)
            directory = new_run_directory(root / "runs", "failed")
            initialize_run_record(directory)
            job = AssessmentJob(
                "a" * 32,
                directory / "execution-config.yaml",
                "kubernetes",
                "cluster",
                "http://metrics",
                "now",
                state="failed",
                run_id=directory.name,
                cleanup_required=True,
            )
            write_json_atomic(
                directory / "run-metadata.json",
                {"run_id": directory.name, "stage": "assessed", "cleanup_performed": True},
            )
            write_json_atomic(
                directory / "result.json",
                {"status": "ready", "readiness_score": 100, "conclusive": True},
            )
            manager._record_failure(job)
            self.assertFalse(json.loads((directory / "result.json").read_text())["conclusive"])
            self.assertFalse(
                json.loads((directory / "run-metadata.json").read_text())["rollback"]["verified"]
            )
            command = _command(job)
            self.assertIn("--context", command)
            self.assertIn("--prometheus-url", command)
            self.assertEqual(command[command.index("--run-dir") + 1], str(directory))
            job.run_id = None
            self.assertFalse(manager._preserve_child_result(job))
            manager._record_failure(job)
            process = MagicMock(stdout=None)
            manager._drain(job, process)
            with patch("chamber.control_plane.jobs.os.killpg", side_effect=ProcessLookupError):
                _signal(process, signal.SIGINT)

    def test_cancelled_child_result_does_not_require_cleanup_after_verified_rollback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AssessmentJobManager(root)
            directory = new_run_directory(root / "runs", "cancelled")
            job = AssessmentJob(
                "b" * 32,
                directory / "execution-config.yaml",
                "kubernetes",
                None,
                None,
                "now",
                run_id=directory.name,
            )
            self.assertFalse(manager._preserve_child_result(job))
            for metadata, result, expected in (
                ([], {}, False),
                ({"run_id": "wrong", "stage": "cancelled"}, {}, False),
                ({"run_id": directory.name, "stage": "running"}, {}, False),
                (
                    {
                        "run_id": directory.name,
                        "stage": "cancelled",
                        "runtime_mode": "attach",
                        "rollback": {"verified": True},
                        "error": "operator stopped",
                    },
                    {},
                    True,
                ),
            ):
                write_json_atomic(directory / "run-metadata.json", metadata)
                write_json_atomic(directory / "result.json", result)
                self.assertEqual(manager._preserve_child_result(job), expected)
            self.assertEqual(job.state, "cancelled")
            self.assertFalse(job.cleanup_required)
            self.assertEqual(job.error, "operator stopped")


class ApiFailureRegressionTests(unittest.TestCase):
    def test_missing_resources_and_invalid_start_never_create_jobs(self) -> None:
        with TemporaryDirectory() as tmp:
            app = server.create_app(Path(tmp))
            with TestClient(app) as client:
                for path in (
                    "/api/v1/runs/missing",
                    "/api/v1/runs/missing/report",
                    "/api/v1/runs/missing/evidence/x",
                    "/api/v1/runs/missing/events",
                    "/api/v1/runs/missing/evidence-explorer",
                    "/api/v1/jobs/missing",
                    "/api/v1/jobs/missing/events",
                    "/jobs/missing",
                ):
                    with self.subTest(path=path):
                        self.assertEqual(client.get(path).status_code, 404)
                csrf = client.cookies["ampule_csrf"]
                headers = {"X-CSRF-Token": csrf}
                for path, data in (
                    ("/api/v1/jobs/missing/cancel", {}),
                    ("/api/v1/jobs/missing/cleanup-verified", {"confirmed": True}),
                    ("/api/v1/runs/missing/archive", {"archived": True}),
                    ("/api/v1/runs/missing/tags", {"tags": []}),
                    ("/api/v1/runs/missing/rerun", {}),
                ):
                    with self.subTest(path=path):
                        self.assertEqual(
                            client.post(path, json=data, headers=headers).status_code, 404
                        )
                for path in (
                    "/ui/runs/missing/archive",
                    "/ui/runs/missing/tags",
                    "/ui/runs/missing/fix-and-rerun",
                    "/ui/jobs/missing/cancel",
                ):
                    self.assertEqual(client.post(path, data={"_csrf": csrf}).status_code, 404)
                self.assertEqual(
                    client.post(
                        "/api/v1/runs",
                        json={"mode": "local", "plan_id": "missing"},
                        headers=headers,
                    ).status_code,
                    404,
                )
                for payload in (
                    {"mode": "local"},
                    {"mode": "local", "plan_id": "x", "config_path": "x"},
                ):
                    self.assertEqual(
                        client.post("/api/v1/runs", json=payload, headers=headers).status_code, 400
                    )
                self.assertEqual(
                    client.post(
                        "/api/v1/scenarios/propose", json={"goal": "unsupported"}, headers=headers
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/kubernetes/discovery?context=in-cluster&namespace=undeclared"
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/compare",
                        json={"baseline_run_id": "missing", "candidate_run_id": "missing"},
                        headers=headers,
                    ).status_code,
                    400,
                )
                self.assertEqual(app.state.jobs.list(), ())

    def test_run_error_responses_and_replay_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = server.create_app(root)
            application = app.state.chamber
            directory = new_run_directory(root / "runs", "errors")
            initialize_run_record(directory)
            write_json_atomic(directory / "run.json", {"run_id": directory.name, "state": "failed"})
            with TestClient(app) as client:
                client.get("/readyz")
                csrf = client.cookies["ampule_csrf"]
                headers = {"X-CSRF-Token": csrf}
                for event_id in ("-1", "invalid"):
                    response = client.get(
                        f"/api/v1/runs/{directory.name}/events", headers={"Last-Event-ID": event_id}
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["detail"], "Invalid Last-Event-ID")
                self.assertEqual(
                    client.get(f"/api/v1/runs/{directory.name}/events").status_code, 200
                )
                self.assertEqual(
                    client.get(f"/api/v1/runs/{directory.name}/report?format=unknown").status_code,
                    409,
                )
                with patch.object(application, "report", side_effect=RuntimeError("not assessed")):
                    response = client.get(f"/api/v1/runs/{directory.name}/report")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(
                        response.json()["detail"], "Report is unavailable for this run"
                    )
                with patch.object(
                    application,
                    "plan_rerun",
                    side_effect=ValueError("local run cannot be recovered"),
                ):
                    for path, json_mode in (
                        (f"/api/v1/runs/{directory.name}/rerun", True),
                        (f"/ui/runs/{directory.name}/fix-and-rerun", False),
                    ):
                        response = client.post(
                            path,
                            headers=headers,
                            json={} if json_mode else None,
                            data=None if json_mode else {"_csrf": csrf},
                        )
                        self.assertEqual(response.status_code, 400)
                        self.assertIn("cannot be recovered", response.text)
                with patch.object(application, "set_run_tags", side_effect=ValueError("bad tags")):
                    self.assertEqual(
                        client.post(
                            f"/api/v1/runs/{directory.name}/tags",
                            json={"tags": ["tag"]},
                            headers=headers,
                        ).status_code,
                        400,
                    )
                with patch(
                    "chamber.control_plane.server.registered_evidence",
                    return_value=({"evidence_id": "invalid", "relative_path": None},),
                ):
                    self.assertEqual(
                        client.get(f"/api/v1/runs/{directory.name}/evidence/invalid").status_code,
                        404,
                    )
                config_path = root / "drafts/safe.yaml"
                config_path.parent.mkdir()
                config_path.write_text("{}")
                for key in ("", "x" * 201):
                    self.assertEqual(
                        client.post(
                            "/api/v1/runs",
                            json={"config_path": str(config_path), "mode": "local"},
                            headers={**headers, "Idempotency-Key": key},
                        ).status_code,
                        400,
                    )
                with patch.object(app.state.jobs, "start", side_effect=ValueError("occupied")):
                    response = client.post(
                        "/api/v1/runs",
                        json={"config_path": str(config_path), "mode": "kubernetes"},
                        headers=headers,
                    )
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()["detail"], "occupied")
                self.assertEqual(
                    client.post(
                        "/api/v1/runs",
                        json={"config_path": "/etc/passwd", "mode": "local"},
                        headers=headers,
                    ).status_code,
                    400,
                )

    def test_unauthenticated_browser_preserves_query_and_login_redirects_safely(self) -> None:
        with TemporaryDirectory() as tmp:
            app = server.create_app(Path(tmp), admin_token="op_live_admin_credential_for_testing")
            with TestClient(app) as client:
                response = client.get("/runs?q=orders", follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertIn("q%3Dorders", response.headers["location"])
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        with TemporaryDirectory() as tmp:
            with TestClient(server.create_app(Path(tmp))) as client:
                response = client.get("/login?next=https://evil.example", follow_redirects=False)
                self.assertEqual(response.headers["location"], "/runs")

    def test_bounded_summary_discloses_invalid_numbers_depth_and_omitted_details(self) -> None:
        nested: Any = {"value": "safe"}
        for _ in range(20):
            nested = {"child": nested}
        data = {
            "values": [float("nan"), float("inf")],
            "deep": nested,
            "many": ["x" * 20000 for _ in range(101)],
        }
        data.update({f"field-{index}": ["large" * 5000] for index in range(110)})
        result = server._bounded_run_summary(data)
        self.assertEqual(result["values"], [None, None])
        reasons = {item["reason"] for item in result["summary"]["truncated_fields"]}
        self.assertIn("depth_limit", reasons)
        self.assertIn("invalid_number", reasons)
        self.assertGreater(result["summary"]["omitted_truncation_count"], 0)
        self.assertLess(len(json.dumps(result["many"], separators=(",", ":"))), 65536)


class PlanTransactionRegressionTests(unittest.TestCase):
    def _plan(
        self,
        application: server.ChamberApplication,
        values: dict[str, Any],
        changes: dict[str, Any] | None = None,
    ) -> Path:
        candidate = dict(values)
        candidate.update(changes or {})
        return server._plan_from_values(application, **candidate)

    def _values(self, root: Path) -> dict[str, Any]:
        return dict(
            workspace=root,
            repo=None,
            service_name="orders",
            workload_name="orders",
            workload_kind="Deployment",
            execution_mode="kubernetes",
            runtime_mode="attach",
            kubernetes_context="in-cluster",
            namespace="qa",
            service_port=80,
            journeys_json="",
            journey_type="http",
            traffic_path="/health",
            traffic_profile="smoke",
            request_body="{}",
            events_path="/tasks/{task_id}/events",
            task_id_path="task_id",
            relayna_timeout_seconds=10,
            fault_type="none",
            prometheus_url="",
            agents_mode="offline",
        )

    def test_invalid_intake_does_not_leave_drafts_or_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            application = server.ChamberApplication(root)
            values = self._values(root)
            cases = (
                ({"journey_type": "invalid"}, "http or relayna"),
                ({"journey_type": "relayna", "request_body": "bad"}, "valid JSON"),
                ({"journey_type": "relayna", "request_body": "[]"}, "non-empty JSON object"),
                (
                    {
                        "journey_type": "relayna",
                        "request_body": '{"a":1}',
                        "events_path": "/events",
                    },
                    "task_id",
                ),
                (
                    {
                        "journey_type": "relayna",
                        "request_body": '{"a":1}',
                        "relayna_timeout_seconds": 0,
                    },
                    "positive",
                ),
                ({"agents_exclude_json": "bad"}, "valid JSON"),
                ({"agents_exclude_json": "[1]"}, "non-empty strings"),
                ({"save_scenario": "bad"}, "Save scenario mode"),
                ({"save_scenario": "new"}, "catalog is unavailable"),
                (
                    {
                        "save_scenario": "replace",
                        "catalog": scenarios.ScenarioCatalog(root, BUNDLED),
                    },
                    "explicit confirmation",
                ),
                ({"load_json": "[]"}, "object"),
                ({"load_json": '{"journeyOverrides":[]}'}, "existing journey names"),
                ({"load_json": '{"journeyOverrides":{"absent":{}}}'}, "existing journey names"),
                (
                    {"load_json": '{"journeyOverrides":{"baseline-health":{"unknown":1}}}'},
                    "Unsupported",
                ),
            )
            for change, message in cases:
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                    self._plan(application, values, change)
            self.assertEqual(list(root.glob("drafts/*")), [])
            self.assertEqual(list(root.glob("runs/*")), [])

    def test_save_failure_rolls_back_plan_and_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            application = server.ChamberApplication(root)
            catalog = scenarios.ScenarioCatalog(root, BUNDLED)
            values: dict[str, Any] = {
                **self._values(root),
                "catalog": catalog,
                "save_scenario": "new",
            }
            with patch.object(catalog, "save", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    server._plan_from_values(application, **values)
            self.assertEqual(list(root.glob("runs/*")), [])
            self.assertEqual(list(root.glob("drafts/*")), [])
            self.assertEqual(list(catalog.user_dir.glob("*.yaml")), [])
            with patch.object(application, "plan", side_effect=ValueError("invalid plan")):
                with self.assertRaisesRegex(ValueError, "invalid plan"):
                    server._plan_from_values(application, **self._values(root))
            self.assertEqual(list(root.glob("drafts/*")), [])

    def test_attach_relayna_and_fault_configuration_roundtrip(self) -> None:
        import yaml

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            application = server.ChamberApplication(root)
            values = self._values(root)
            for change, message in (
                ({"service_name": ""}, "service name"),
                ({"workload_name": ""}, "workload name"),
                ({"workload_kind": "Pod"}, "workload kind"),
            ):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                    self._plan(application, values, change)
            for fault in ("pod_kill", "deployment_scale"):
                planned = self._plan(
                    application,
                    values,
                    {
                        "journey_type": "relayna",
                        "request_body": '{"kind":"sample"}',
                        "agents_exclude_json": '["report-writer-agent"]',
                        "fault_type": fault,
                        "prometheus_url": "http://metrics",
                    },
                )
                config = yaml.safe_load((planned / "chamber.yaml").read_text())
                self.assertEqual(config["runtime"]["faults"][0]["type"], fault)
                self.assertEqual(config["traffic"]["journeys"][0]["body"], {"kind": "sample"})
                self.assertEqual(config["agents"]["exclude"], ["report-writer-agent"])
                self.assertEqual(config["runtime"]["prometheusUrl"], "http://metrics")


class StreamingRegressionTests(unittest.TestCase):
    def test_event_stream_waits_for_terminal_state_and_emits_only_changes(self) -> None:
        async def collect(stream: Any) -> list[str]:
            return [event async for event in stream]

        jobs = MagicMock()
        jobs.get.side_effect = [{"state": "running"}, {"state": "running"}, {"state": "completed"}]

        async def no_wait(seconds: float) -> None:
            self.assertEqual(seconds, 0.5)

        with patch("chamber.control_plane.server.asyncio.sleep", no_wait):
            events = asyncio.run(collect(server._job_event_stream(jobs, "id")))
        self.assertEqual(len(events), 2)
        self.assertIn('"state": "completed"', events[-1])
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_json_atomic(directory / "run.json", {"state": "running"})

            async def complete(seconds: float) -> None:
                (directory / "events.jsonl").write_text('{"state":"completed"}\n')
                write_json_atomic(directory / "run.json", {"state": "completed"})

            with patch("chamber.control_plane.server.asyncio.sleep", complete):
                events = asyncio.run(collect(server._run_event_stream(directory)))
            self.assertEqual(len(events), 1)
            self.assertIn("id: 1", events[0])
            self.assertIn('"state":"completed"', events[0])

    def test_managed_upload_reuse_and_optional_file_with_new_upload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "uploads/prior"
            directory.mkdir(parents=True)
            file = directory / "previous.txt"
            file.write_bytes(b"previous")
            secret = b"test"
            existing = {
                "field": "prior",
                "path": str(file),
                "pathToken": server._multipart_path_token(file.resolve(), secret),
            }
            raw = json.dumps(
                [
                    {
                        "multipart": {
                            "files": [
                                existing,
                                {"field": "optional", "required": False},
                                {"field": "new", "uploadIndex": 0},
                            ]
                        }
                    }
                ]
            )
            upload = UploadFile(
                io.BytesIO(b"fresh"),
                filename="fresh.txt",
                headers=Headers({"content-type": "text/plain"}),
            )
            result, destination = asyncio.run(
                server._persist_journey_files(root, raw, [upload], path_secret=secret)
            )
            descriptors = json.loads(result)[0]["multipart"]["files"]
            self.assertEqual(Path(descriptors[0]["path"]).read_bytes(), b"previous")
            self.assertEqual(Path(descriptors[2]["path"]).read_bytes(), b"fresh")
            self.assertIsNotNone(destination)
            with self.assertRaisesRegex(ValueError, "browser upload"):
                asyncio.run(
                    server._persist_journey_files(
                        root, json.dumps([{"multipart": {"files": [{"uploadIndex": 0}]}}]), []
                    )
                )
            optional = {"field": "optional", "pathToken": "untrusted"}
            projection: dict[str, Any] = {
                "journeys": [
                    None,
                    {"multipart": {"files": {}}},
                    {"multipart": {"files": [None, optional]}},
                ]
            }
            server._authorize_multipart_paths(projection, secret, directory)
            self.assertNotIn("pathToken", optional)
