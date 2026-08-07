from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

import yaml
from fastapi.testclient import TestClient

from chamber.application import evidence as evidence_projection
from chamber.application.service import ChamberApplication
from chamber.control_plane.server import create_app
from chamber.runs import refresh_evidence_manifest, write_json_atomic


class EvidenceExplorerProjectionTests(unittest.TestCase):
    def test_normalizes_all_supported_sources_without_sensitive_content(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            explorer = ChamberApplication(workspace).get_run(run_dir.name)["evidence_explorer"]

        categories = {event["category"] for event in explorer["events"]}
        self.assertTrue(
            {"traffic", "fault", "kubernetes", "metric", "log", "relayna"}.issubset(categories)
        )
        for event in explorer["events"]:
            self.assertTrue(event["timestamp"])
            self.assertTrue(event["source_identity"]["evidence_id"])
            self.assertTrue(event["source_identity"]["source"])
        rendered = json.dumps(explorer)
        self.assertNotIn("private document text", rendered)
        self.assertNotIn("customer request body", rendered)
        self.assertNotIn("raw OCR response", rendered)
        self.assertEqual(
            {"task-a", "task-b"},
            {event["task_id"] for event in explorer["events"] if event.get("task_id")},
        )
        self.assertTrue({"api-a", "worker-b"}.issubset(set(explorer["facets"]["pod"])))
        self.assertTrue(
            {"exact", "run_window", "inferred"}.issubset(
                {event["correlation"] for event in explorer["events"]}
            )
        )

    def test_filters_preserve_time_finding_context_and_paginate(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            application = ChamberApplication(workspace)
            query = {
                "finding": "finding-restarts",
                "task_id": "task-a",
                "start": "2026-08-08T00:00:00+00:00",
                "end": "2026-08-08T00:10:00+00:00",
                "page_size": "1",
            }
            explorer = application.get_run(run_dir.name, evidence_query=query)["evidence_explorer"]

        self.assertEqual(explorer["filters"]["finding"], "finding-restarts")
        self.assertEqual(explorer["filters"]["task_id"], "task-a")
        self.assertEqual(explorer["window"]["start"], query["start"])
        self.assertEqual(explorer["window"]["end"], query["end"])
        self.assertEqual(len(explorer["events"]), 1)
        self.assertGreater(explorer["pagination"]["total_pages"], 1)
        next_query = parse_qs(urlparse(explorer["pagination"]["next_url"]).query)
        self.assertEqual(next_query["finding"], ["finding-restarts"])
        self.assertEqual(next_query["task_id"], ["task-a"])
        self.assertEqual(next_query["start"], [query["start"]])
        self.assertEqual(next_query["end"], [query["end"]])

    def test_finding_deep_link_opens_cited_range_and_highlights_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            application = ChamberApplication(workspace)
            run = application.get_run(run_dir.name)
            deep_link = run["findings"][0]["investigation_url"]
            query = {key: values[0] for key, values in parse_qs(urlparse(deep_link).query).items()}
            explorer = application.get_run(run_dir.name, evidence_query=query)["evidence_explorer"]

            with TestClient(create_app(workspace)) as client:
                response = client.get(deep_link)

        self.assertEqual(explorer["finding_context"]["finding_id"], "finding-restarts")
        self.assertGreater(explorer["finding_context"]["highlighted_event_count"], 0)
        self.assertEqual(explorer["window"]["start"], "2026-08-08T00:00:10+00:00")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Investigating finding-restarts", response.text)
        self.assertIn('class="explorer-event category-', response.text)
        self.assertIn("highlighted", response.text)
        self.assertIn('data-correlation="exact"', response.text)
        self.assertIn("correlation correlation-inferred", response.text)
        self.assertIn("Expert raw artifacts and verified downloads", response.text)
        self.assertNotIn("private document text", response.text)

    def test_task_pod_signal_and_severity_filters_are_independent(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            application = ChamberApplication(workspace)
            for key, value in (
                ("task_id", "task-b"),
                ("pod", "worker-b"),
                ("signal", "restarts"),
                ("severity", "warning"),
            ):
                with self.subTest(filter=key):
                    explorer = application.get_run(
                        run_dir.name,
                        evidence_query={key: value},
                    )["evidence_explorer"]
                    self.assertTrue(explorer["events"])
                    self.assertTrue(
                        all(str(event.get(key) or "") == value for event in explorer["events"])
                    )


class EvidenceExplorerApiAndIntegrityTests(unittest.TestCase):
    def test_api_pagination_and_digest_verified_download(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            with TestClient(create_app(workspace)) as client:
                page = client.get(
                    f"/api/v1/runs/{run_dir.name}/evidence-explorer",
                    params={"page_size": 2},
                )
                download = client.get(f"/api/v1/runs/{run_dir.name}/evidence/prometheus-memory")
                (run_dir / "evidence/prometheus-memory.json").write_text(
                    "tampered", encoding="utf-8"
                )
                rejected = client.get(f"/api/v1/runs/{run_dir.name}/evidence/prometheus-memory")
                after_tamper = client.get(
                    f"/api/v1/runs/{run_dir.name}/evidence-explorer",
                    params={"signal": "cpu"},
                )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.json()["events"]), 2)
        self.assertGreater(page.json()["pagination"]["total_pages"], 1)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(after_tamper.json()["events"], [])

    def test_large_sources_are_bounded_and_pages_remain_small(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace, run_dir = _run_fixture(Path(tmp))
            tasks = [
                {
                    "task_id": f"bulk-task-{index}",
                    "journey": "document-lifecycle",
                    "total_duration_ms": 1000,
                    "events": [
                        {"timestamp": "2026-08-08T00:00:20Z", "status": f"stage-{stage}"}
                        for stage in range(8)
                    ],
                }
                for index in range(200)
            ]
            write_json_atomic(run_dir / "evidence/relayna-summary.json", {"tasks": tasks})
            commands = []
            for batch in range(8):
                events = [
                    {
                        "metadata": {"creationTimestamp": "2026-08-08T00:00:10Z"},
                        "eventTime": "2026-08-08T00:00:10Z",
                        "type": "Normal",
                        "reason": f"Batch{batch}Event{index}",
                        "involvedObject": {"kind": "Pod", "name": "api-a"},
                    }
                    for index in range(300)
                ]
                commands.append(
                    {
                        "command": ["kubectl", "get", "events"],
                        "exit_status": 0,
                        "stdout": json.dumps({"items": events}),
                        "stderr": "",
                    }
                )
            write_json_atomic(run_dir / "evidence/kubernetes-commands.json", {"commands": commands})
            refresh_evidence_manifest(run_dir)
            explorer = ChamberApplication(workspace).get_run(
                run_dir.name,
                evidence_query={"page_size": "25"},
            )["evidence_explorer"]

        self.assertTrue(explorer["truncated"])
        self.assertEqual(explorer["projected_event_count"], 1000)
        self.assertEqual(len(explorer["events"]), 25)
        self.assertEqual(explorer["pagination"]["total_items"], 1000)
        self.assertGreater(explorer["pagination"]["total_pages"], 1)


class EvidenceExplorerFallbackTests(unittest.TestCase):
    def test_legacy_summaries_and_malformed_records_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            source = {"relative_path": "artifact.json", "collected_at": "invalid"}
            artifact = run_dir / "artifact.json"

            write_json_atomic(
                artifact,
                {
                    "summaries": [
                        "invalid",
                        {
                            "pod_name": "legacy-pod",
                            "role": "api",
                            "peak_memory_bytes": 1024,
                            "peak_cpu_cores": 0.5,
                            "max_restarts": 2,
                            "task_ids": ["legacy-task"],
                        },
                    ]
                },
            )
            prometheus = evidence_projection._prometheus_events(run_dir, source)
            self.assertEqual(
                {event["signal"] for event in prometheus}, {"memory", "cpu", "restarts"}
            )
            self.assertTrue(any(event["severity"] == "warning" for event in prometheus))

            write_json_atomic(
                artifact,
                {
                    "tasks": [
                        "invalid",
                        {
                            "task_id": "failed-task",
                            "events": ["invalid", {"status": "failed"}],
                        },
                    ]
                },
            )
            relayna = evidence_projection._relayna_events(run_dir, source)
            self.assertTrue(any(event["severity"] == "error" for event in relayna))
            self.assertTrue(any(event["correlation"] == "inferred" for event in relayna))

            write_json_atomic(artifact, {"workers": ["invalid", {}]})
            workers = evidence_projection._worker_events(run_dir, source)
            self.assertEqual(workers[0]["pod"], "worker")
            self.assertEqual(workers[0]["correlation"], "run_window")

            write_json_atomic(
                artifact,
                {
                    "verified": False,
                    "actions": [
                        "invalid",
                        {"type": "deployment_scale", "deployment": "api", "restored": False},
                    ],
                },
            )
            rollback = evidence_projection._rollback_events(run_dir, source)
            self.assertEqual(rollback[-1]["severity"], "error")
            self.assertEqual(rollback[-1]["workload"], "api")

            write_json_atomic(
                artifact,
                {
                    "commands": [
                        "invalid",
                        {
                            "command": ["kubectl", "get", "events"],
                            "stdout": json.dumps(
                                {
                                    "items": [
                                        {
                                            "type": "Normal",
                                            "reason": "Scheduled",
                                            "involvedObject": {"kind": "Deployment", "name": "api"},
                                        }
                                    ]
                                }
                            ),
                        },
                        {
                            "command": ["kubectl", "get", "pod"],
                            "stdout": json.dumps(
                                {"metadata": {"name": "legacy-pod"}, "status": {}}
                            ),
                        },
                        {
                            "command": ["kubectl", "logs", "deployment/api"],
                            "stdout": "2026-08-08T00:00:00Z CRITICAL worker.crashed detail",
                        },
                    ]
                },
            )
            kubernetes = evidence_projection._kubernetes_events(run_dir, source)
            self.assertTrue(any(event["correlation"] == "inferred" for event in kubernetes))
            self.assertTrue(any(event["severity"] == "error" for event in kubernetes))
            self.assertIsNone(
                next(event for event in kubernetes if event["category"] == "log")["pod"]
            )

            write_json_atomic(artifact, {"metrics": "invalid"})
            self.assertEqual(evidence_projection._k6_events(run_dir, source, config={}), [])

    def test_parser_and_query_fallbacks_are_deterministic(self) -> None:
        self.assertIsNone(evidence_projection._datetime(None))
        self.assertIsNone(evidence_projection._datetime("not-a-time"))
        self.assertEqual(
            evidence_projection._iso("2026-08-08T00:00:00"),
            "2026-08-08T00:00:00+00:00",
        )
        self.assertIsNone(evidence_projection._number("not-a-number"))
        self.assertIsNone(evidence_projection._number(float("nan")))
        self.assertEqual(evidence_projection._bounded_int("bad", 7, 1, 10), 7)
        self.assertEqual(evidence_projection._evenly_bounded([1, 2, 3], 1), [1])
        self.assertEqual(evidence_projection._items([]), [])
        self.assertIsNone(evidence_projection._owner_name({}))
        self.assertEqual(evidence_projection._json(Path("/does/not/exist")), {})
        self.assertEqual(evidence_projection._json_text("not json"), {})
        self.assertIsNone(evidence_projection._metric_value("invalid", "value"))
        self.assertIn(
            "bounded traffic outcome",
            evidence_projection._metric_summary(
                requests=None,
                error_rate=None,
                latency_ms=None,
            ),
        )
        self.assertEqual(evidence_projection._finding_evidence_ids(None), set())
        self.assertFalse(evidence_projection._finding_signal_matches(None, "restarts"))

        projection = {
            "events": [
                evidence_projection._event(
                    event_id="event",
                    timestamp="2026-08-08T00:05:00+00:00",
                    source="test",
                    source_id="test",
                    signal="test",
                    category="test",
                    severity="unexpected",
                    correlation="inferred",
                    title="Test",
                    summary="Test",
                )
            ]
        }
        queried = evidence_projection.query_evidence_explorer(
            projection,
            run_id="run",
            findings=[],
            query={
                "start": "2026-08-08T00:10:00Z",
                "end": "2026-08-08T00:00:00Z",
            },
        )
        self.assertEqual(queried["window"]["start"], "2026-08-08T00:00:00+00:00")
        self.assertEqual(queried["events"][0]["severity"], "info")


def _run_fixture(root: Path) -> tuple[Path, Path]:
    workspace = root / ".chamber"
    run_dir = workspace / "runs" / "explorer-run"
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True)
    write_json_atomic(
        run_dir / "run.json",
        {"run_id": run_dir.name, "state": "completed", "service_name": "ocr-api"},
    )
    write_json_atomic(run_dir / "run-metadata.json", {"service_name": "ocr-api"})
    (run_dir / "chamber.yaml").write_text(
        yaml.safe_dump(
            {
                "service": {"name": "ocr-api"},
                "traffic": {
                    "journeys": [
                        {"name": "health", "adapter": "http"},
                        {"name": "document-lifecycle", "adapter": "relayna"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    write_json_atomic(
        run_dir / "findings.json",
        [
            {
                "finding_id": "finding-restarts",
                "signal_type": "restart_loop",
                "severity": "high",
                "confidence": "high",
                "evidence_ids": ["kubernetes-command-1", "prometheus-memory"],
            }
        ],
    )
    write_json_atomic(
        evidence / "k6-summary.json",
        {
            "metrics": {
                "http_reqs": {"values": {"count": 20}},
                "http_req_failed": {"values": {"rate": 0.05}},
                "http_req_duration": {"values": {"p(95)": 240}},
            },
            "request": "customer request body",
        },
    )
    write_json_atomic(
        evidence / "relayna-summary.json",
        {
            "tasks": [
                _task("task-a", "2026-08-08T00:00:05Z", "2026-08-08T00:00:25Z"),
                _task("task-b", "2026-08-08T00:00:06Z", "2026-08-08T00:00:26Z"),
            ],
            "document": "private document text",
        },
    )
    write_json_atomic(
        evidence / "relayna-workers.json",
        {
            "workers": [
                {
                    "name": "worker-a",
                    "start_time": "2026-08-08T00:00:08Z",
                    "task_ids": ["task-a"],
                    "correlation": "task_label",
                },
                {
                    "name": "worker-b",
                    "start_time": "2026-08-08T00:00:09Z",
                    "task_ids": [],
                    "correlation": "run_window_and_service_labels",
                },
            ]
        },
    )
    pod_items = [
        _pod("api-a", "api-owner", "2026-08-08T00:00:02Z"),
        _pod("worker-b", "worker-owner", "2026-08-08T00:00:03Z"),
    ]
    kubernetes_event = {
        "metadata": {"creationTimestamp": "2026-08-08T00:00:10Z"},
        "eventTime": "2026-08-08T00:00:10Z",
        "type": "Warning",
        "reason": "BackOff",
        "message": "raw OCR response",
        "involvedObject": {"kind": "Pod", "name": "api-a"},
    }
    write_json_atomic(
        evidence / "kubernetes-commands.json",
        {
            "commands": [
                {
                    "command": ["kubectl", "get", "pods"],
                    "exit_status": 0,
                    "stdout": json.dumps({"items": pod_items}),
                    "stderr": "",
                },
                {
                    "command": ["kubectl", "get", "events"],
                    "exit_status": 0,
                    "stdout": json.dumps({"items": [kubernetes_event]}),
                    "stderr": "",
                },
                {
                    "command": ["kubectl", "logs", "pod/api-a"],
                    "exit_status": 0,
                    "stdout": (
                        "2026-08-08T00:00:11Z ERROR worker.failed private document text\n"
                        "untimestamped raw OCR response\n"
                    ),
                    "stderr": "",
                },
            ]
        },
    )
    write_json_atomic(
        evidence / "rollback.json",
        {
            "verified": True,
            "actions": [{"type": "pod_kill", "pod": "api-a", "restored": True}],
        },
    )
    write_json_atomic(evidence / "prometheus-memory.json", _prometheus())
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "event_id": "run:1",
                "event_type": "state_changed",
                "state": "running",
                "observed_at": "2026-08-08T00:00:01Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    refresh_evidence_manifest(run_dir)
    return workspace, run_dir


def _task(task_id: str, started: str, ended: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "journey": "document-lifecycle",
        "total_duration_ms": 20_000,
        "events": [
            {"timestamp": started, "status": "processing", "message": "private document text"},
            {"timestamp": ended, "status": "completed", "body": "customer request body"},
        ],
    }


def _pod(name: str, owner: str, started: str) -> dict[str, object]:
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": started,
            "ownerReferences": [{"name": owner}],
        },
        "status": {"phase": "Running", "startTime": started},
    }


def _prometheus() -> dict[str, object]:
    workloads = [
        {
            "pod_name": "api-a",
            "role": "api",
            "correlation": "selected_target",
            "task_ids": [],
        },
        {
            "pod_name": "worker-b",
            "role": "relayna_worker",
            "correlation": "run_window_and_service_labels",
            "task_ids": ["task-b"],
        },
    ]
    return {
        "workloads": workloads,
        "range_queries": {
            "container_memory_working_set_bytes": {
                "series": [
                    _series("api-a", 10, 64 * 1024 * 1024),
                    _series("worker-b", 10, 114 * 1024 * 1024),
                ]
            },
            "container_cpu_usage_cores": {
                "series": [_series("api-a", 11, 0.025), _series("worker-b", 11, 0.089)]
            },
            "kube_pod_container_status_restarts_total": {
                "series": [_series("api-a", 12, 1), _series("worker-b", 12, 0)]
            },
        },
    }


def _series(pod: str, second: int, value: float) -> dict[str, object]:
    return {
        "metric": {"pod": pod, "container": "app"},
        "values": [[1_786_147_200 + second, str(value)]],
    }


if __name__ == "__main__":
    unittest.main()
