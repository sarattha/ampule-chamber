from __future__ import annotations

import json
import socket
import unittest
from datetime import UTC, datetime
from email.message import Message
from html import unescape
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

import chamber.workflow as workflow
from chamber.application.results import build_assessment_result
from chamber.application.service import ChamberApplication
from chamber.control_plane.server import create_app
from chamber.report import (
    ReportInput,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)
from chamber.runs import refresh_evidence_manifest

REQUIRED_QUERIES = (
    "container_memory_working_set_bytes",
    "container_cpu_usage_seconds_total",
    "kube_pod_container_status_restarts_total",
)


class PrometheusQueryTests(unittest.TestCase):
    def test_kubernetes_pod_names_build_exact_promql_regex(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch(
                "chamber.workflow._prometheus_query",
                return_value=_successful_query(),
            ) as query:
                workflow._collect_prometheus_memory_evidence(
                    Path(tmp),
                    prometheus_url="http://prometheus.example",
                    namespace="chamber-test",
                    pod_names=("example-service-abc123", "worker.v2-7d9f"),
                )

        queries = [str(call.args[1]) for call in query.call_args_list]
        self.assertEqual(len(queries), 3)
        for promql in queries:
            self.assertIn('namespace="chamber-test"', promql)
            self.assertIn(
                'pod=~"example-service-abc123|worker\\\\.v2-7d9f"',
                promql,
            )
            self.assertNotIn(r"example\-service", promql)

    def test_promql_layers_escape_regex_and_string_metacharacters_separately(self) -> None:
        self.assertEqual(
            workflow._promql_pod_regex(("pod-a", "pod.b", "pod[1]")),
            r"pod-a|pod\\.b|pod\\[1\\]",
        )
        self.assertEqual(workflow._promql_string('value"with\\slashes'), r"value\"with\\slashes")

    def test_transport_and_decode_failures_remain_visible(self) -> None:
        failures = (
            HTTPError("http://prometheus", 400, "Bad Request", Message(), None),
            socket.gaierror(-2, "Name or service not known"),
            TimeoutError("query timed out"),
            json.JSONDecodeError("invalid JSON", "<html>", 0),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("chamber.workflow._read_prometheus_payload", side_effect=failure):
                    result = workflow._prometheus_query(
                        "http://prometheus.example", 'up{job="target"}'
                    )
                self.assertFalse(result["ok"])
                self.assertIsNone(result["series_count"])
                self.assertTrue(result["error"])
                self.assertEqual(result["series"], [])

    def test_prometheus_failure_response_preserves_backend_error(self) -> None:
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={
                "status": "error",
                "errorType": "bad_data",
                "error": "invalid parameter query",
            },
        ):
            result = workflow._prometheus_query("http://prometheus.example", "bad query")

        self.assertFalse(result["ok"])
        self.assertIsNone(result["series_count"])
        self.assertEqual(result["error"], "bad_data: invalid parameter query")

    def test_http_error_response_preserves_prometheus_backend_error(self) -> None:
        response_body = BytesIO(
            json.dumps(
                {
                    "status": "error",
                    "errorType": "bad_data",
                    "error": "invalid parameter query",
                }
            ).encode("utf-8")
        )
        error = HTTPError(
            "http://prometheus.example/api/v1/query",
            422,
            "Unprocessable Entity",
            Message(),
            response_body,
        )
        with patch("chamber.workflow.urlopen", side_effect=error):
            result = workflow._prometheus_query("http://prometheus.example", "bad query")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_data: invalid parameter query")
        self.assertTrue(response_body.closed)

    def test_http_error_with_unusable_body_preserves_http_diagnostic(self) -> None:
        unusable_bodies = (
            (b"", "error response body was empty"),
            (b"<html>temporarily unavailable</html>", "error response was not valid JSON"),
        )
        for body, expected_detail in unusable_bodies:
            with self.subTest(body=body):
                response_body = BytesIO(body)
                error = HTTPError(
                    "http://prometheus.example/api/v1/query",
                    503,
                    "Service Unavailable",
                    Message(),
                    response_body,
                )
                with patch("chamber.workflow.urlopen", side_effect=error):
                    result = workflow._prometheus_query("http://prometheus.example", "up")

                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"],
                    f"Prometheus returned HTTP 503 Service Unavailable; {expected_detail}",
                )
                self.assertTrue(response_body.closed)

    def test_success_response_records_explicit_count_and_no_error(self) -> None:
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={"status": "success", "data": {"result": [_series()]}},
        ):
            result = workflow._prometheus_query("http://prometheus.example", "up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["series_count"], 1)
        self.assertIsNone(result["error"])
        self.assertEqual(result["observed_pod_names"], ["example-service-abc123"])

    def test_success_response_preserves_all_observed_pods_when_series_are_truncated(self) -> None:
        series = [_series("example-service-abc123") for _ in range(20)]
        series.append(_series("example-service-def456"))
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={"status": "success", "data": {"result": series}},
        ):
            result = workflow._prometheus_query("http://prometheus.example", "up")

        self.assertEqual(result["series_count"], 21)
        self.assertEqual(len(result["series"]), 20)
        self.assertEqual(
            result["observed_pod_names"],
            ["example-service-abc123", "example-service-def456"],
        )

    def test_range_query_and_summary_preserve_worker_peaks(self) -> None:
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"pod": "ocr-worker-abc", "container": "worker"},
                            "values": [[1, "40"], [2, "70"]],
                        },
                        {
                            "metric": {"pod": "ocr-worker-abc", "container": "sidecar"},
                            "values": [[1, "2"], [2, "3"]],
                        },
                    ]
                },
            },
        ):
            result = workflow._prometheus_query_range(
                "http://prometheus.example",
                "memory query",
                start=datetime(2026, 7, 13, tzinfo=UTC),
                end=datetime(2026, 7, 13, 0, 1, tzinfo=UTC),
                step_seconds=15,
            )
        summaries = workflow._prometheus_range_summaries(
            {
                "container_memory_working_set_bytes": result,
                "container_cpu_usage_cores": {
                    **{key: value for key, value in result.items() if key != "pod_summaries"},
                    "series": [
                        {
                            "metric": {"pod": "ocr-worker-abc", "container": "worker"},
                            "values": [[1, "0.02"], [2, "0.089"]],
                        }
                    ],
                },
                "kube_pod_container_status_restarts_total": {
                    **{key: value for key, value in result.items() if key != "pod_summaries"},
                    "series": [
                        {
                            "metric": {"pod": "ocr-worker-abc", "container": "worker"},
                            "values": [[1, "0"], [2, "0"]],
                        }
                    ],
                },
            },
            workloads=[
                {
                    "pod_name": "ocr-worker-abc",
                    "role": "relayna_worker",
                    "task_ids": [],
                }
            ],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["observed_pod_names"], ["ocr-worker-abc"])
        self.assertEqual(summaries[0]["peak_memory_bytes"], 73)
        self.assertEqual(summaries[0]["peak_cpu_cores"], 0.089)
        self.assertEqual(summaries[0]["max_restarts"], 0)
        self.assertEqual(summaries[0]["sample_count"], 2)

    def test_range_summaries_use_all_series_beyond_persisted_preview(self) -> None:
        series = [
            {
                "metric": {"pod": f"ocr-worker-{index:03d}", "container": "worker"},
                "values": [[1, str(index)], [2, str(index + 1)]],
            }
            for index in range(101)
        ]
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={"status": "success", "data": {"result": series}},
        ):
            result = workflow._prometheus_query_range(
                "http://prometheus.example",
                "memory query",
                start=datetime(2026, 7, 13, tzinfo=UTC),
                end=datetime(2026, 7, 13, 0, 1, tzinfo=UTC),
                step_seconds=15,
            )
        summaries = workflow._prometheus_range_summaries(
            {name: result for name in REQUIRED_QUERIES},
            workloads=[],
        )
        last_worker = next(item for item in summaries if item["pod_name"] == "ocr-worker-100")

        self.assertEqual(result["series_count"], 101)
        self.assertEqual(len(result["series"]), 100)
        self.assertEqual(result["pod_summaries"]["ocr-worker-100"]["peak"], 101)
        self.assertEqual(last_worker["peak_memory_bytes"], 101)
        self.assertEqual(last_worker["max_restarts"], 101)

    def test_collector_writes_bounded_run_window_for_api_and_worker(self) -> None:
        def range_query(
            prometheus_url: str,
            query: str,
            *,
            start: datetime,
            end: datetime,
            step_seconds: int,
        ) -> dict[str, Any]:
            del prometheus_url, start, end
            self.assertEqual(step_seconds, 15)
            value = "104857600" if "memory" in query else "0.05" if "rate(" in query else "0"
            return {
                "ok": True,
                "query": query,
                "series_count": 2,
                "error": None,
                "observed_pod_names": ["ocr-api-123", "ocr-worker-abc"],
                "series": [
                    {
                        "metric": {"pod": pod_name, "container": "app"},
                        "values": [[1, value], [2, value]],
                    }
                    for pod_name in ("ocr-api-123", "ocr-worker-abc")
                ],
            }

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with (
                patch("chamber.workflow._prometheus_query", return_value=_successful_query()),
                patch("chamber.workflow._prometheus_query_range", side_effect=range_query),
            ):
                workflow._collect_prometheus_memory_evidence(
                    run_dir,
                    prometheus_url="http://prometheus.example",
                    namespace="common",
                    pod_names=("ocr-api-123",),
                    observed_pods=(
                        {"name": "ocr-api-123", "role": "api", "phase": "Running"},
                        {
                            "name": "ocr-worker-abc",
                            "role": "relayna_worker",
                            "phase": "Succeeded",
                            "correlation": "run_window_and_service_labels",
                            "task_ids": [],
                        },
                    ),
                    range_start=datetime(2026, 7, 13, tzinfo=UTC),
                    range_end=datetime(2026, 7, 13, 0, 1, tzinfo=UTC),
                )
            artifact = json.loads(
                (run_dir / "evidence/prometheus-memory.json").read_text(encoding="utf-8")
            )

        self.assertEqual(artifact["schema_version"], 2)
        self.assertEqual(artifact["window"]["step_seconds"], 15)
        self.assertEqual(
            [item["pod_name"] for item in artifact["summaries"]],
            ["ocr-api-123", "ocr-worker-abc"],
        )
        self.assertEqual(artifact["summaries"][1]["peak_memory_bytes"], 104857600)
        self.assertEqual(artifact["summaries"][1]["peak_cpu_cores"], 0.05)

    def test_relayna_worker_discovery_distinguishes_exact_and_run_window_correlation(self) -> None:
        pod_payload = {
            "items": [
                _worker_pod("ocr-worker-exact", task_id="task-1"),
                _worker_pod("ocr-worker-window"),
                _worker_pod("another-worker", service="another-service"),
            ]
        }
        with patch("chamber.workflow._kubectl_json", return_value=pod_payload):
            workers = workflow._discover_relayna_worker_pods(
                context="aks",
                namespace="common",
                service_name="ocr-service-api",
                selected_pod_names=("ocr-service-api-123",),
                task_ids=("task-1", "task-2"),
                traffic_started_at=datetime(2026, 7, 13, tzinfo=UTC),
                runner=cast(Any, object()),
                commands=[],
            )

        self.assertEqual(
            [item["name"] for item in workers], ["ocr-worker-exact", "ocr-worker-window"]
        )
        self.assertEqual(workers[0]["task_ids"], ["task-1"])
        self.assertEqual(workers[0]["correlation"], "task_label")
        self.assertEqual(workers[1]["task_ids"], [])
        self.assertEqual(workers[1]["correlation"], "run_window_and_service_labels")


class PrometheusUiProjectionTests(unittest.TestCase):
    def test_assessment_ui_shows_api_worker_metrics_and_task_keyed_feeds(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            run_dir = workspace / "runs/relayna-ui"
            evidence = run_dir / "evidence"
            evidence.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps({"run_id": run_dir.name, "state": "completed"}),
                encoding="utf-8",
            )
            (run_dir / "run-metadata.json").write_text(
                json.dumps({"service_name": "ocr-service-api"}), encoding="utf-8"
            )
            (evidence / "prometheus-memory.json").write_text(
                json.dumps(
                    {
                        "namespace": "common",
                        "window": {"start": "start", "end": "end", "step_seconds": 15},
                        "summaries": [
                            _metric_summary("ocr-service-api-123", "api", 64, 0.025),
                            _metric_summary("ocr-worker-abc", "relayna_worker", 114, 0.089),
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (evidence / "relayna-summary.json").write_text(
                json.dumps(
                    {
                        "success": True,
                        "task_count": 2,
                        "tasks": [
                            _relayna_task("task-a", "processing", "completed"),
                            _relayna_task("task-b", "queued", "completed"),
                        ],
                    }
                ),
                encoding="utf-8",
            )

            projection = ChamberApplication(workspace).get_run(run_dir.name)
            with TestClient(create_app(workspace)) as client:
                response = client.get(f"/runs/{run_dir.name}?tab=evidence")

        self.assertEqual(projection["prometheus"]["workloads"][1]["role"], "Relayna worker")
        self.assertEqual(
            [event["task_id"] for event in projection["relayna"]["tasks"][0]["events"]],
            ["task-a", "task-a"],
        )
        self.assertEqual(
            [event["task_id"] for event in projection["relayna"]["tasks"][1]["events"]],
            ["task-b", "task-b"],
        )
        self.assertEqual(response.status_code, 200)
        for text in (
            "Runtime metrics",
            "ocr-service-api-123",
            "ocr-worker-abc",
            "114.0 MiB",
            "89.0m",
            "Relayna task feed",
            "task-a",
            "task-b",
        ):
            self.assertIn(text, response.text)


class PrometheusEvidenceGateTests(unittest.TestCase):
    def test_failed_required_query_is_diagnostic_but_not_satisfied_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(Path(tmp), failed_query=REQUIRED_QUERIES[1])
            result = _assessment_result(run_dir)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["conclusive"])
        self.assertIsNone(result["readiness_score"])
        self.assertEqual(result["evidence_coverage_percent"], 75)
        self.assertIn("prometheus-memory", result["available_evidence_ids"])
        self.assertIn("prometheus-memory", result["missing_evidence_ids"])
        self.assertTrue(
            any("failed: connection refused" in item for item in result["evidence_limitations"])
        )

    def test_report_regeneration_recomputes_changed_prometheus_evidence(self) -> None:
        changed_queries = (
            (REQUIRED_QUERIES[1], None),
            (None, REQUIRED_QUERIES[0]),
        )
        for failed_query, zero_query in changed_queries:
            with (
                self.subTest(
                    failed_query=failed_query,
                    zero_query=zero_query,
                ),
                TemporaryDirectory() as tmp,
            ):
                run_dir = _reportable_result_run(Path(tmp))
                config = workflow.load_config(run_dir / "chamber.yaml", require_repo=False)
                metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
                workflow._finalize_guided_result(run_dir, config=config, metadata=metadata)
                analysis_event_count = _event_count(run_dir, "analysis_completed")
                self.assertEqual(analysis_event_count, 1)

                workflow.render_report_from_run(run_dir)
                ready = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(ready["status"], "ready")
                self.assertTrue(ready["conclusive"])
                self.assertEqual(ready["evidence_coverage_percent"], 100)
                self.assertEqual(
                    _event_count(run_dir, "analysis_completed"),
                    analysis_event_count,
                )

                _write_prometheus_artifact(
                    run_dir,
                    failed_query=failed_query,
                    zero_query=zero_query,
                )
                workflow.render_report_from_run(run_dir)
                recomputed = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

                self.assertEqual(recomputed["status"], "inconclusive")
                self.assertFalse(recomputed["conclusive"])
                self.assertIsNone(recomputed["readiness_score"])
                self.assertEqual(recomputed["evidence_coverage_percent"], 75)
                self.assertIn("prometheus-memory", recomputed["missing_evidence_ids"])
                self.assertEqual(
                    _event_count(run_dir, "analysis_completed"),
                    analysis_event_count,
                )

    def test_cli_selected_prometheus_is_required_from_run_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(Path(tmp), failed_query=REQUIRED_QUERIES[1])
            result = _assessment_result(run_dir, prometheus_in_config=False)

        self.assertEqual(result["status"], "inconclusive")
        self.assertIn("prometheus-memory", result["required_evidence_ids"])
        self.assertEqual(result["evidence_coverage_percent"], 75)

    def test_successful_zero_series_is_distinct_and_inconclusive(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(Path(tmp), zero_query=REQUIRED_QUERIES[0])
            result = _assessment_result(run_dir)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["conclusive"])
        self.assertEqual(result["evidence_coverage_percent"], 75)
        self.assertTrue(
            any("succeeded but returned zero" in item for item in result["evidence_limitations"])
        )
        self.assertFalse(any("failed" in item for item in result["evidence_limitations"]))

    def test_all_required_queries_with_series_allow_conclusive_ready(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(Path(tmp))
            result = _assessment_result(run_dir)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["conclusive"])
        self.assertEqual(result["readiness_score"], 100)
        self.assertEqual(result["evidence_coverage_percent"], 100)
        self.assertEqual(result["missing_evidence_ids"], [])
        self.assertEqual(result["evidence_limitations"], [])

    def test_partial_selected_pod_coverage_is_inconclusive(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(
                Path(tmp),
                pod_names=("example-service-abc123", "example-service-def456"),
                partial_query=REQUIRED_QUERIES[1],
            )
            result = _assessment_result(run_dir)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["conclusive"])
        self.assertEqual(result["evidence_coverage_percent"], 75)
        self.assertIn("prometheus-memory", result["missing_evidence_ids"])
        self.assertEqual(
            result["evidence_limitations"],
            [
                "Prometheus query container_cpu_usage_seconds_total returned no series "
                "for selected pods: example-service-def456."
            ],
        )

    def test_every_required_query_covering_all_selected_pods_is_conclusive(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _result_run(
                Path(tmp),
                pod_names=("example-service-abc123", "example-service-def456"),
            )
            result = _assessment_result(run_dir)

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["conclusive"])
        self.assertEqual(result["evidence_coverage_percent"], 100)
        self.assertEqual(result["evidence_limitations"], [])


class PrometheusReportTests(unittest.TestCase):
    def test_control_plane_formats_recompute_stale_prometheus_results(self) -> None:
        for report_format in ("json", "html", "markdown"):
            with self.subTest(report_format=report_format), TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                run_dir = _reportable_result_run(workspace / "runs")
                config = workflow.load_config(run_dir / "chamber.yaml", require_repo=False)
                metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
                ready = workflow._finalize_guided_result(
                    run_dir,
                    config=config,
                    metadata=metadata,
                )
                analysis_event_count = _event_count(run_dir, "analysis_completed")
                self.assertEqual(ready["status"], "ready")
                self.assertEqual(analysis_event_count, 1)
                _write_prometheus_artifact(run_dir, failed_query=REQUIRED_QUERIES[1])

                suffix = "" if report_format == "markdown" else f"?format={report_format}"
                with TestClient(create_app(workspace)) as client:
                    response = client.get(f"/api/v1/runs/{run_dir.name}/report{suffix}")

                persisted = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(persisted["status"], "inconclusive")
                self.assertEqual(persisted["evidence_coverage_percent"], 75)
                self.assertEqual(
                    _event_count(run_dir, "analysis_completed"),
                    analysis_event_count,
                )
                self.assertEqual(_event_count(run_dir, "report_generated"), 1)
                if report_format == "json":
                    self.assertEqual(response.json()["result"]["status"], "inconclusive")
                    self.assertEqual(
                        response.json()["result"]["evidence_coverage_percent"],
                        75,
                    )
                else:
                    self.assertIn("inconclusive", response.text.lower())
                    self.assertIn("75%", response.text)

    def test_report_cli_tolerates_unreadable_prometheus_artifacts(self) -> None:
        malformed_artifacts = (
            ("{not-json", "Prometheus evidence is unreadable:"),
            ("[]", "Prometheus evidence does not contain required queries."),
        )
        for artifact_text, expected_limitation in malformed_artifacts:
            with self.subTest(artifact_text=artifact_text), TemporaryDirectory() as tmp:
                run_dir = _reportable_result_run(Path(tmp))
                artifact = run_dir / "evidence/prometheus-memory.json"
                artifact.write_text(artifact_text, encoding="utf-8")

                with patch("builtins.print"):
                    exit_code = workflow.main(["report", "--run", str(run_dir)])

                result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
                report = (run_dir / "report.md").read_text(encoding="utf-8")

                self.assertEqual(exit_code, 0)
                self.assertEqual(result["status"], "inconclusive")
                self.assertFalse(result["conclusive"])
                self.assertIn("prometheus-memory", result["missing_evidence_ids"])
                self.assertTrue(
                    any(
                        expected_limitation in limitation
                        for limitation in result["evidence_limitations"]
                    )
                )
                self.assertIn("Prometheus Metrics", report)
                self.assertIn("Prometheus evidence unavailable:", report)
                self.assertIn(expected_limitation, report)

    def test_offline_markdown_and_html_show_query_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs/prometheus-failed"
            _write_prometheus_artifact(run_dir, failed_query=REQUIRED_QUERIES[1])
            markdown = _render_report(run_dir)
            html = _render_html(Path(tmp), run_dir, markdown)

        for output in (markdown, html):
            self.assertIn("Prometheus Metrics", output)
            self.assertIn("ok=false", output)
            self.assertIn("series_count=unavailable", output)
            self.assertIn("error=connection refused", output)
            self.assertIn("Kubernetes logs are separate", output)
        self.assertIn(_stored_promql(REQUIRED_QUERIES[1]), markdown)
        self.assertIn(_stored_promql(REQUIRED_QUERIES[1]), unescape(html))

    def test_successful_metrics_show_labels_value_timestamp_and_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs/prometheus-success"
            _write_prometheus_artifact(run_dir)
            markdown = _render_report(run_dir)
            html = _render_html(Path(tmp), run_dir, markdown)

        for output in (markdown, html):
            self.assertIn("container_memory_working_set_bytes: ok=true", output)
            self.assertIn("series_count=1", output)
            self.assertIn("pod=example-service-abc123", output)
            self.assertIn("container=app", output)
            self.assertIn("sample_value=42", output)
            self.assertIn("sample_timestamp=1710000000", output)
            self.assertIn("evidence/prometheus-memory.json", output)
        self.assertIn(_stored_promql(REQUIRED_QUERIES[0]), markdown)
        self.assertIn(_stored_promql(REQUIRED_QUERIES[0]), unescape(html))

    def test_partial_selected_pod_coverage_is_visible(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs/prometheus-partial"
            _write_prometheus_artifact(
                run_dir,
                pod_names=("example-service-abc123", "example-service-def456"),
                partial_query=REQUIRED_QUERIES[1],
            )
            markdown = _render_report(run_dir)

        self.assertIn("selected_pod_coverage=1/2", markdown)
        self.assertIn("missing_selected_pods=example-service-def456", markdown)

    def test_agent_summaries_and_details_preserve_error_without_zero_default(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_prometheus_artifact(run_dir, failed_query=REQUIRED_QUERIES[1])
            summaries = workflow._agent_evidence_summaries(run_dir, ("prometheus-memory",))
            details = workflow._agent_evidence_details(run_dir, ("prometheus-memory",))

        summary = "\n".join(summaries)
        detail = "\n".join(details)
        self.assertIn("ok=false series_count=unavailable error=connection refused", summary)
        self.assertIn('"series_count": null', detail)
        self.assertIn('"error": "connection refused"', detail)


def _result_run(
    root: Path,
    *,
    failed_query: str | None = None,
    zero_query: str | None = None,
    pod_names: tuple[str, ...] = (),
    partial_query: str | None = None,
) -> Path:
    run_dir = root / "live-run"
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True)
    for name in ("preflight", "kubernetes-commands", "k6-summary"):
        (evidence / f"{name}.json").write_text("{}", encoding="utf-8")
    _write_prometheus_artifact(
        run_dir,
        failed_query=failed_query,
        zero_query=zero_query,
        pod_names=pod_names,
        partial_query=partial_query,
    )
    refresh_evidence_manifest(run_dir)
    return run_dir


def _reportable_result_run(root: Path) -> Path:
    run_dir = _result_run(root)
    config = {
        "apiVersion": "chamber.ampule.dev/v1alpha1",
        "kind": "ChamberConfig",
        "service": {"name": "example-service", "repo": "."},
        "deployment": {
            "manifests": ["kubernetes.yaml"],
            "workloads": [{"name": "example-service", "kind": "Deployment", "role": "target"}],
        },
        "traffic": {
            "entrypoint": "example-service",
            "journeys": [
                {
                    "name": "health",
                    "method": "GET",
                    "path": "/healthz",
                    "expectedStatus": 200,
                    "stages": [{"duration": "1s", "targetVus": 1}],
                }
            ],
        },
        "dependencies": {"internal": [], "external": []},
        "runtime": {
            "provider": "kubernetes",
            "mode": "deploy",
            "kubernetesContext": "kind-issue24-test",
            "prometheusUrl": "http://prometheus.example",
            "cleanup": True,
            "trafficAccess": {
                "mode": "port-forward",
                "service": "example-service",
                "servicePort": 8080,
            },
        },
        "agents": {"mode": "off"},
    }
    workflow.save_config(config, run_dir / "chamber.yaml")
    (run_dir / "plan.json").write_text(
        json.dumps(
            {
                "namespace": "chamber-test",
                "runtime": {"provider": "kubernetes"},
                "workloads": [],
                "limitations": [],
                "redacted_config": [],
                "external_dependencies": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "run-metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "stage": "assessed",
                "mode": "kubernetes",
                "provider": "kubernetes",
                "namespace": "chamber-test",
                "success": True,
                "traffic_result": {"success": True},
                "cleanup_performed": True,
                "cleanup_notes": ["Cleanup verified."],
                "rollback": {"verified": True},
                "runtime": {"prometheus_url": "http://prometheus.example"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _assessment_result(
    run_dir: Path,
    *,
    prometheus_in_config: bool = True,
) -> dict[str, Any]:
    runtime = {"provider": "kubernetes", "mode": "deploy"}
    if prometheus_in_config:
        runtime["prometheusUrl"] = "http://prometheus.example"
    return build_assessment_result(
        run_dir,
        config={"runtime": runtime},
        metadata={
            "run_id": run_dir.name,
            "stage": "assessed",
            "mode": "kubernetes",
            "success": True,
            "traffic_result": {"success": True},
            "cleanup_performed": True,
            "rollback": {"verified": True},
            "runtime": {"prometheus_url": "http://prometheus.example"},
        },
        findings=(),
    )


def _write_prometheus_artifact(
    run_dir: Path,
    *,
    failed_query: str | None = None,
    zero_query: str | None = None,
    pod_names: tuple[str, ...] = (),
    partial_query: str | None = None,
) -> None:
    queries = {}
    returned_pod_names = pod_names or ("example-service-abc123",)
    for name in REQUIRED_QUERIES:
        if name == failed_query:
            queries[name] = {
                "ok": False,
                "query": _stored_promql(name),
                "series_count": None,
                "error": "connection refused",
                "series": [],
            }
        elif name == zero_query:
            queries[name] = {
                "ok": True,
                "query": _stored_promql(name),
                "series_count": 0,
                "error": None,
                "series": [],
            }
        else:
            query_pod_names = (
                returned_pod_names[:1] if name == partial_query else returned_pod_names
            )
            queries[name] = _successful_query(
                _stored_promql(name),
                pod_names=query_pod_names,
            )
    path = run_dir / "evidence/prometheus-memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pod_names": list(pod_names), "queries": queries}) + "\n",
        encoding="utf-8",
    )


def _successful_query(
    query: str = "up",
    *,
    pod_names: tuple[str, ...] = ("example-service-abc123",),
) -> dict[str, object]:
    return {
        "ok": True,
        "query": query,
        "series_count": len(pod_names),
        "error": None,
        "observed_pod_names": list(pod_names),
        "series": [_series(pod_name) for pod_name in pod_names],
    }


def _stored_promql(name: str) -> str:
    return (
        f'{name}{{namespace="chamber-test",pod=~"example-service-abc123|example-service-def456"}}'
    )


def _event_count(run_dir: Path, event_type: str) -> int:
    events_path = run_dir / "events.jsonl"
    return sum(
        json.loads(line)["event_type"] == event_type
        for line in events_path.read_text(encoding="utf-8").splitlines()
    )


def _series(pod_name: str = "example-service-abc123") -> dict[str, object]:
    return {
        "metric": {"pod": pod_name, "container": "app"},
        "value": [1710000000, "42"],
    }


def _worker_pod(
    name: str,
    *,
    service: str = "ocr-service",
    task_id: str | None = None,
) -> dict[str, Any]:
    labels = {
        "app": f"{service.removesuffix('-service')}-worker",
        "service": service,
        "scaledjob.keda.sh/name": f"{service.removesuffix('-service')}-worker",
    }
    if task_id:
        labels["relayna.ampule.dev/task-id"] = task_id
    return {
        "metadata": {
            "name": name,
            "labels": labels,
            "ownerReferences": [{"kind": "Job", "name": f"{name}-job"}],
        },
        "status": {
            "phase": "Succeeded",
            "startTime": "2026-07-13T00:00:05Z",
            "containerStatuses": [
                {"state": {"terminated": {"finishedAt": "2026-07-13T00:00:40Z"}}}
            ],
        },
    }


def _metric_summary(pod_name: str, role: str, memory_mib: int, cpu_cores: float) -> dict[str, Any]:
    return {
        "pod_name": pod_name,
        "role": role,
        "phase": "Succeeded" if role == "relayna_worker" else "Running",
        "peak_memory_bytes": memory_mib * 1024 * 1024,
        "peak_cpu_cores": cpu_cores,
        "max_restarts": 0,
        "sample_count": 8,
        "task_ids": [],
        "correlation": "run_window_and_service_labels",
        "errors": [],
    }


def _relayna_task(task_id: str, *statuses: str) -> dict[str, Any]:
    return {
        "iteration": 1,
        "journey": "document-lifecycle",
        "task_id": task_id,
        "terminal_status": statuses[-1],
        "success": True,
        "total_duration_ms": 1250,
        "event_count": len(statuses),
        "events": [
            {"sequence": index, "task_id": "untrusted-payload-id", "status": status}
            for index, status in enumerate(statuses, 1)
        ],
    }


def _render_report(run_dir: Path) -> str:
    sections = workflow._prometheus_report_sections(run_dir)
    report = ReportInput(
        title="Ampule Chamber Reliability Report",
        service=ServiceMetadata("service", "owner", ".", "commit"),
        run=RunMetadata("run", "2026-07-13", 1, "namespace", "kind", "assessed"),
        scenario=TestedScenario("scenario", "baseline", "scenario.yaml", "k6", 1, "none"),
        findings=(),
        evidence=(),
        reproduction=ReproductionDetails((), ()),
        retest_plan=(),
        cleanup_notes=(),
        limitations=(),
        agent_sections=sections,
    )
    return render_markdown_report(report)


def _render_html(workspace: Path, run_dir: Path, markdown: str) -> str:
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    with patch.object(ChamberApplication, "report", return_value=run_dir / "report.md"):
        with TestClient(create_app(workspace)) as client:
            response = client.get(f"/api/v1/runs/{run_dir.name}/report?format=html")
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.text


if __name__ == "__main__":
    unittest.main()
