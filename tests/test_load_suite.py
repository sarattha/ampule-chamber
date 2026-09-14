from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from chamber.load.suite import (
    DeadlineResponse,
    Generator,
    assertions,
    execute_iteration,
    execute_suite,
    expand,
    measurements,
    sized_body,
    validate_suite,
)
from tests.test_relayna_traffic import _journey


class Target(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.requests.append(
            {"path": self.path, "body": body.decode(), "auth": self.headers.get("Authorization")}
        )
        status, payload = 200, b'{"id":"one/two","ok":true}'
        if self.path == "/slow":
            time.sleep(0.25)
        if self.path == "/fail":
            status = 503
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/leak")
            self.end_headers()
            return
        if self.path == "/translations":
            status, payload = 202, b'{"task_id":"one"}'
        if self.path.startswith("/events/"):
            payload = (
                b'data: {"status":"queued"}\n\n'
                b'data: {"status":"running"}\n\n'
                b'data: {"status":"completed"}\n\n'
            )
        if self.path.startswith("/events/slow/"):
            self.send_response(200)
            self.end_headers()
            for status in ("queued", "running", "completed"):
                time.sleep(0.1)
                self.wfile.write(f'data: {{"status":"{status}"}}\n\n'.encode())
                self.wfile.flush()
            return
        if self.path == "/large":
            payload = b"x" * (1024 * 1024 + 2)
        if self.path == "/invalid":
            payload = b"not json"
        self.send_response(status)
        self.end_headers()
        try:
            if self.path == "/drip":
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            else:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def traffic(**options: Any) -> dict[str, Any]:
    return {
        "journeys": [{"name": "health", "path": "/", "expectedStatus": 200}],
        "load": {
            "ratePerSecond": 10,
            "durationSeconds": 1,
            "maxInFlight": 8,
            "timeoutSeconds": 1,
            **options,
        },
    }


class LoadSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def run_suite(self, config: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        with TemporaryDirectory() as root:
            output = Path(root) / "summary.json"
            summary = execute_suite(config, base_url=self.url, output=output, **kwargs)
            self.assertEqual(json.loads(output.read_text())["status"], summary["status"])
            self.assertEqual(
                summary["metrics"]["requested"],
                summary["metrics"]["started"] + summary["metrics"]["dropped"],
            )
            return summary

    def iteration(self, journey: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return execute_iteration(
            journey,
            base_url=self.url,
            variables={"item": "one/two"},
            iteration=1,
            timeout=kwargs.pop("timeout", 1),
            workspace=kwargs.pop("workspace", None),
            generator=Generator(),
            **kwargs,
        )

    def test_mixed_arrival_load_and_lifecycle_coverage(self) -> None:
        config = traffic(
            ratePerSecond=10, durationSeconds=3, thresholds={"p99Ms": 900, "minCompletionRate": 1}
        )
        relayna = _journey()
        relayna.update(weight=2, thresholds={"maxQueueWaitMs": 900, "maxWorkerMs": 900})
        config["journeys"].append(relayna)
        result = self.run_suite(config)
        self.assertEqual(result["status"], "pass", json.dumps(result["windows"]))
        self.assertEqual(result["metrics"]["requested"], 30)
        by_name = {j["name"]: j["metrics"] for j in result["windows"][0]["journeys"]}
        self.assertGreater(by_name[relayna["name"]]["started"], by_name["health"]["started"])
        self.assertEqual(
            by_name[relayna["name"]]["queueTimingSamples"], by_name[relayna["name"]]["started"]
        )
        self.assertEqual(by_name["health"]["queueTimingSamples"], 0)
        self.assertGreater(result["generator"]["peakActiveRequests"], 0)
        self.assertTrue(result["generator"]["samples"])

    def test_dropped_arrivals_are_inconclusive_and_bounded(self) -> None:
        config = traffic(ratePerSecond=30, maxInFlight=1)
        config["journeys"][0]["path"] = "/slow"
        result = self.run_suite(config)
        self.assertEqual(result["status"], "inconclusive")
        self.assertGreater(result["metrics"]["dropped"], 0)
        self.assertEqual(result["generator"]["peakActiveRequests"], 1)

    def test_capacity_stops_after_sustained_failures_and_runs_recovery(self) -> None:
        config = traffic(
            model="capacity",
            failureWindows=2,
            recoverySeconds=1,
            stages=[{"ratePerSecond": n, "durationSeconds": 1} for n in (5, 10, 15, 20)],
        )
        config["journeys"][0]["path"] = "/fail"
        result = self.run_suite(config)
        self.assertEqual(result["status"], "fail")
        self.assertEqual([w["phase"] for w in result["windows"]], ["load", "load", "recovery"])
        self.assertIsNotNone(result["capacity"]["stopReason"])
        self.assertIsNone(result["capacity"]["highestPassingRate"])

    def test_soak_windows_warmup_phase_budgets_and_trends(self) -> None:
        config = traffic(
            model="soak",
            durationSeconds=2,
            windowSeconds=1,
            warmupSeconds=1,
            thresholds={"p95Ms": 0},
            phaseThresholds={"load": {"p95Ms": 1000}},
        )
        result = self.run_suite(config)
        self.assertEqual(result["status"], "pass", json.dumps(result["windows"]))
        self.assertEqual([w["state"] for w in result["windows"]], ["fail", "pass", "pass"])
        self.assertEqual(result["soak"]["windowCount"], 2)
        self.assertIsNotNone(result["soak"]["throughputChangePerSecond"])

    def test_error_budgets_and_missing_lifecycle_threshold(self) -> None:
        config = traffic(thresholds={"maxErrorRate": 1})
        config["journeys"][0]["path"] = "/fail"
        self.assertEqual(self.run_suite(config)["status"], "pass")
        config = traffic(thresholds={"maxQueueWaitMs": 100})
        self.assertEqual(self.run_suite(config)["status"], "inconclusive")

    def test_chained_extraction_auth_and_business_assertions(self) -> None:
        journey = {
            "name": "chain",
            "headersFromEnv": {"Authorization": "LOAD_TEST_AUTH"},
            "steps": [
                {"path": "/", "extract": {"identity": "id"}},
                {
                    "path": "/objects/${identity}",
                    "method": "POST",
                    "body": {"id": "${identity}", "item": "${item}"},
                    "assertJson": {"ok": True},
                },
            ],
        }
        with patch.dict(os.environ, {"LOAD_TEST_AUTH": "test-only-secret"}):
            result = self.iteration(journey)
        self.assertTrue(result["success"])
        self.assertEqual(Target.requests[-1]["path"], "/objects/one%2Ftwo")
        self.assertEqual(Target.requests[-1]["auth"], "test-only-secret")
        self.assertNotIn("test-only-secret", json.dumps(result))
        journey["steps"][1]["assertJson"]["ok"] = False
        self.assertFalse(self.iteration(journey)["success"])

    def test_dataset_distribution_is_seeded_and_evidence_omits_payloads(self) -> None:
        config = traffic(
            ratePerSecond=8, datasets={"payloads": [{"text": "small"}, {"text": "x" * 1024}]}
        )
        config["journeys"][0].update(
            method="POST", dataset="payloads", body={"text": "${text}", "n": "${iteration}"}
        )
        start = len(Target.requests)
        first = self.run_suite(deepcopy(config))
        bodies = [r["body"] for r in Target.requests[start:]]
        start = len(Target.requests)
        self.run_suite(deepcopy(config))
        self.assertEqual(bodies, [r["body"] for r in Target.requests[start:]])
        self.assertEqual(len(set(len(json.loads(b)["text"]) for b in bodies)), 2)
        self.assertNotIn("x" * 1024, json.dumps(first))

    def test_encoding_failures_and_deadline_on_continuously_dripping_body(self) -> None:
        self.assertTrue(
            self.iteration({"name": "expected-rejection", "path": "/fail", "expectedStatus": 503})[
                "success"
            ]
        )
        for encoding, extra in [
            ("form", {"form": {"key": "${item}"}}),
            ("raw", {"body": "hello ${item}"}),
        ]:
            with self.subTest(encoding=encoding):
                self.assertTrue(
                    self.iteration(
                        {
                            "name": "encode",
                            "path": "/",
                            "method": "POST",
                            "requestEncoding": encoding,
                            **extra,
                        }
                    )["success"]
                )
        for path, extra in [
            ("/invalid", {"extract": {"v": "id"}}),
            ("/", {"extract": {"v": "missing"}}),
            ("/large", {}),
            ("//other", {}),
            ("/${missing}", {}),
            ("/redirect", {}),
        ]:
            with self.subTest(path=path):
                self.assertFalse(self.iteration({"name": "bad", "path": path, **extra})["success"])
        started = time.monotonic()
        result = self.iteration({"name": "deadline", "path": "/drip"}, timeout=0.2)
        self.assertTrue(result["deadline_missed"])
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertFalse(any(r["path"] == "/leak" for r in Target.requests))
        with patch.dict(os.environ, {"LOAD_TEST_AUTH": "bad\nheader"}):
            self.assertFalse(
                self.iteration(
                    {
                        "name": "bad",
                        "path": "/",
                        "headersFromEnv": {"Authorization": "LOAD_TEST_AUTH"},
                    }
                )["success"]
            )

    def test_scoped_target_metrics_reject_missing_wrong_stale_nonfinite(self) -> None:
        query = {"name": "memoryBytes", "query": "memory", "labels": {"app": "api"}}

        def response(item: Any) -> Any:
            return io.BytesIO(json.dumps(item).encode())

        payload: dict[str, Any] = {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"app": "api", "namespace": "test"}, "value": [time.time(), "100"]}
                ]
            },
        }
        generator = Generator()
        with patch("chamber.load.suite.urlopen", return_value=response(payload)):
            generator.sample_targets([query], "http://prom", "test")
        self.assertEqual(generator.target_samples[-1]["values"], {"memoryBytes": 100})
        for mutation in ("wrong", "stale", "nonfinite", "multiple", "failed"):
            data = deepcopy(payload)
            if mutation == "wrong":
                data["data"]["result"][0]["metric"]["app"] = "other"
            if mutation == "stale":
                data["data"]["result"][0]["value"][0] -= 100
            if mutation == "nonfinite":
                data["data"]["result"][0]["value"][1] = "NaN"
            if mutation == "multiple":
                data["data"]["result"] *= 2
            if mutation == "failed":
                data["status"] = "error"
            with patch("chamber.load.suite.urlopen", return_value=response(data)):
                generator.sample_targets([query], "http://prom", "test")
            self.assertEqual(generator.target_samples[-1]["errors"], ["memoryBytes"])
        generator.sample_targets([query], None, "test")
        self.assertEqual(generator.target_samples[-1]["errors"], ["memoryBytes"])

    def test_soak_target_growth_and_missing_metrics_gate(self) -> None:
        query = {"name": "memoryBytes", "query": "memory", "labels": {"app": "api"}}
        config = traffic(targetMetrics=[query], maxTargetMemoryGrowthMiB=1, maxFinalQueueDepth=0)
        self.assertEqual(self.run_suite(config)["status"], "inconclusive")

        def sample(generator: Generator, *args: Any) -> None:
            generator.target_samples.append(
                {
                    "elapsedSeconds": len(generator.target_samples),
                    "values": {
                        "memoryBytes": len(generator.target_samples) * 2 * 1024**2,
                        "queueDepth": 0,
                    },
                    "errors": [],
                }
            )

        with patch.object(Generator, "sample_targets", sample):
            result = self.run_suite(config)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            [a["state"] for a in result["targetMetrics"]["assertions"]], ["fail", "pass"]
        )

    def test_fast_admission_does_not_hide_slow_worker_and_memory_limit(self) -> None:
        journey = _journey()
        journey["relayna"]["eventsPath"] = "/events/slow/{task_id}"
        row = self.iteration(journey)
        self.assertTrue(row["success"])
        self.assertGreater(row["worker_ms"], 80)
        self.assertLess(row["admission_ms"], row["worker_ms"])
        metrics = measurements([row], 1, 0)
        self.assertEqual(assertions(metrics, {"maxWorkerMs": 50})[0]["state"], "fail")
        with patch.object(Generator, "memory", side_effect=[0] + [2] * 100):
            result = self.run_suite(traffic(maxGeneratorMemoryGrowthMiB=0))
        self.assertEqual(result["status"], "inconclusive")
        with patch.dict(os.environ, {}, clear=True), TemporaryDirectory() as root:
            config = traffic()
            config["journeys"][0]["headersFromEnv"] = {"Authorization": "MISSING_AUTH"}
            with self.assertRaisesRegex(ValueError, "authentication"):
                execute_suite(config, base_url=self.url, output=Path(root) / "load.json")

    def test_validation_rejects_malformed_unbounded_and_foreign_target_inputs(self) -> None:
        for key, value in [
            ("model", "wrong"),
            ("phase", "wrong"),
            ("ratePerSecond", float("nan")),
            ("maxInFlight", 1.5),
            ("maxInFlight", 129),
            ("stages", []),
            ("stages", [1]),
            ("stages", [{"ratePerSecond": 1, "durationSeconds": 3601}]),
            ("durationSeconds", 3600),
            ("ratePerSecond", 1000),
            ("datasets", []),
            ("datasets", {"a": []}),
            ("thresholds", []),
            ("thresholds", {"bad": 1}),
            ("phaseThresholds", {"invalid": {}}),
            ("phaseThresholds", {"load": []}),
            ("targetMetrics", [1]),
            ("targetMetrics", [{"name": "wrong", "query": "a"}]),
            ("targetMetrics", [{"name": "queueDepth", "query": "a", "labels": {}}]),
        ]:
            config = traffic(**{key: value})
            if key == "durationSeconds":
                config["load"]["recoverySeconds"] = 1
            if key == "ratePerSecond" and value == 1000:
                config["load"]["durationSeconds"] = 200
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_suite(config)
        for changes in [
            {"name": ""},
            {"adapter": "unknown"},
            {"weight": 0},
            {"dataset": "missing"},
            {"steps": []},
            {"steps": [1]},
            {"path": "//foreign"},
            {"method": "CONNECT"},
            {"requestEncoding": "bad"},
            {"expectedStatus": True},
            {"extract": []},
            {"extract": {"id": 1}},
            {"headersFromEnv": []},
            {"headersFromEnv": {"X": "bad env"}},
        ]:
            config = traffic()
            config["journeys"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_suite(config)
        self.assertEqual(expand(["${x}", "${x.y}", 3], {"x": {"y": 4}}), [{"y": 4}, 4, 3])
        self.assertEqual(len(sized_body({"text": "ภาษา"}, 13)["text"].encode()), 13)
        with self.assertRaises(ValueError):
            sized_body({}, 3)
        self.assertIsNone(measurements([], 0, 0)["p99Ms"])
        self.assertEqual(assertions(measurements([], 0, 0), {"p99Ms": 5})[0]["state"], "missing")

    def test_deadline_stream_bounds_and_missing_transitions(self) -> None:
        class Fake:
            status = 200
            fp = None

            def __init__(self, content: bytes):
                self.data = io.BytesIO(content)

            def read1(self, n: int) -> bytes:
                return self.data.read(n)

        response = DeadlineResponse(Fake(b"a\nb"), time.monotonic() + 1)
        self.assertEqual(list(response), [b"a\n", b"b"])
        with self.assertRaises(ValueError):
            list(DeadlineResponse(Fake(b"x" * (1024**2 + 1)), time.monotonic() + 1))
        with self.assertRaises(TimeoutError):
            DeadlineResponse(Fake(b"a"), 0).read()
        journey = _journey()
        journey["relayna"]["eventsPath"] = "/invalid/{task_id}"
        result = self.iteration(journey)
        self.assertFalse(result["success"])
        self.assertIsNone(result["worker_ms"])
        self.assertEqual(result["timing_source"], "unavailable")


class LoadIntegrationTests(unittest.TestCase):
    def test_form_mixed_suite_catalog_roundtrip_and_report(self) -> None:
        import yaml
        from fastapi.testclient import TestClient

        from chamber.control_plane.scenarios import normalize_document
        from chamber.control_plane.server import _ui_journeys, create_app
        from tests.test_phase11_12_13_workflow import _fixture_repo

        with TemporaryDirectory() as root:
            workspace, repo = Path(root) / "workspace", Path(root) / "service"
            repo = _fixture_repo(repo)
            client = TestClient(create_app(workspace))
            client.get("/new")
            journeys = [
                {
                    "name": "health",
                    "path": "/",
                    "method": "GET",
                    "expectedStatus": 200,
                    "vus": 1,
                    "iterations": 1,
                },
                {**_journey(), "method": "POST"},
            ]
            data = {
                "_csrf": client.cookies.get("ampule_csrf"),
                "repo": str(repo),
                "service_name": "target-service",
                "execution_mode": "kubernetes",
                "runtime_mode": "deploy",
                "kubernetes_context": "kind-ampule",
                "service_port": "8080",
                "journeys_json": json.dumps(journeys),
                "agents_mode": "offline",
                "load_json": json.dumps(
                    {
                        "ratePerSecond": 20,
                        "durationSeconds": 1,
                        "journeyOverrides": {
                            "health": {
                                "weight": 3,
                                "headersFromEnv": {"Authorization": "LOAD_AUTH"},
                            }
                        },
                    }
                ),
            }
            response = client.post("/ui/plan", data=data, follow_redirects=False)
            self.assertEqual(response.status_code, 303, response.text)
            run_id = response.headers["location"].split("/")[2].split("?")[0]
            run = workspace / "runs" / run_id
            config = yaml.safe_load((run / "chamber.yaml").read_text())
            projection = normalize_document(config, source="user", validate_journeys=_ui_journeys)
            self.assertEqual(projection["load"]["ratePerSecond"], 20)
            self.assertEqual(projection["journeys"][0]["weight"], 3)
            self.assertNotIn("journeyOverrides", config["traffic"]["load"])
            page = client.get(f"/runs/{run_id}")
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn("Load performance", page.text)
            self.assertIn("Untested", page.text)
            for invalid in (
                "[]",
                '{"journeyOverrides":{"unknown":{}}}',
                '{"journeyOverrides":{"health":{"url":"http://other"}}}',
            ):
                data["load_json"] = invalid
                self.assertEqual(client.post("/ui/plan", data=data).status_code, 400)
            data.pop("load_json")
            self.assertEqual(client.post("/ui/plan", data=data).status_code, 400)

    def test_load_evidence_fail_missing_tamper_and_phase_gates(self) -> None:
        from jinja2 import Environment, FileSystemLoader

        from chamber.application.results import build_assessment_result
        from chamber.runs import refresh_evidence_manifest, write_json_atomic
        from tests.test_decision_results import _config, _metadata, _write_required_evidence

        with TemporaryDirectory() as root:
            run = Path(root)
            _write_required_evidence(run)
            config = _config()
            config["traffic"] = traffic()
            # A real loopback load summary is used as the report evidence fixture.
            server = ThreadingHTTPServer(("127.0.0.1", 0), Target)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                summary = execute_suite(
                    config["traffic"],
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    output=run / "evidence/load-summary.json",
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

            def result() -> dict[str, Any]:
                return build_assessment_result(
                    run, config=config, metadata=_metadata("load"), findings=()
                )

            refresh_evidence_manifest(run)
            self.assertEqual(result()["status"], "ready")
            template = Environment(
                loader=FileSystemLoader("chamber/control_plane/templates")
            ).get_template("load_summary.html")
            html = template.render(config=config, result=result(), run_id="load")
            self.assertIn("10 started", html)
            self.assertIn("Generator samples", html)
            from chamber.application.results import _has_evidence_content

            for bad in (
                {"windows": []},
                {"metrics": {"requested": 1}},
                {"windows": [{"journeys": [], "requested": 10, "state": "pass"}]},
            ):
                write_json_atomic(run / "evidence/load-summary.json", {**summary, **bad})
                self.assertFalse(
                    _has_evidence_content(run / "evidence/load-summary.json", "load-summary")
                )
            for state, expected in [("fail", "not_ready"), ("inconclusive", "inconclusive")]:
                summary["status"] = state
                write_json_atomic(run / "evidence/load-summary.json", summary)
                refresh_evidence_manifest(run)
                self.assertEqual(result()["status"], expected)
            (run / "evidence/load-summary.json").write_text("{}")
            self.assertEqual(result()["status"], "inconclusive")
            summary["status"] = "pass"
            write_json_atomic(run / "evidence/load-summary.json", summary)
            config["experiment"] = {"family": "cpu_pressure"}
            refresh_evidence_manifest(run)
            self.assertIn("baseline-load-summary", result()["missing_evidence_ids"])
            write_json_atomic(
                run / "evidence/baseline-load-summary.json", {**summary, "status": "fail"}
            )
            write_json_atomic(
                run / "evidence/recovery-load-summary.json", {**summary, "status": "inconclusive"}
            )
            refresh_evidence_manifest(run)
            self.assertEqual(result()["load"]["status"], "fail")
            self.assertIn("recovery-load-summary", result()["missing_evidence_ids"])

    def test_phase_traffic_keeps_the_original_upload_workspace(self) -> None:
        from chamber.workflow import _execute_kubernetes_traffic

        with TemporaryDirectory() as root:
            workspace = Path(root)
            config = {
                "runtime": {"trafficAccess": {"mode": "endpoint", "url": "http://fixture"}},
                "traffic": traffic(),
            }
            for suffix in ("", "baseline", "recovery"):
                run = workspace / "runs" / "run-1" / suffix
                with patch(
                    "chamber.workflow.execute_suite", return_value={"success": True}
                ) as execute:
                    _execute_kubernetes_traffic(
                        config=config, run_dir=run, context="test", namespace="test", runner=None
                    )
                self.assertEqual(execute.call_args.kwargs["workspace"], workspace)

    def test_chamber_accounts_inflight_and_capacity_drain(self) -> None:
        from chamber.environment.chambers import validate_budget

        config = {
            "traffic": traffic(model="capacity", warmupSeconds=1, recoverySeconds=1),
            "runtime": {},
            "chamber": {"max_vus": 8, "max_duration_seconds": 6},
        }
        validate_budget(config)
        config["chamber"]["max_duration_seconds"] = 5
        with self.assertRaises(ValueError):
            validate_budget(config)
        config["chamber"].update(max_duration_seconds=100, max_vus=2)
        with self.assertRaises(ValueError):
            validate_budget(config)
