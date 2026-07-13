from __future__ import annotations

import json
import socket
import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
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

    def test_success_response_records_explicit_count_and_no_error(self) -> None:
        with patch(
            "chamber.workflow._read_prometheus_payload",
            return_value={"status": "success", "data": {"result": [_series()]}},
        ):
            result = workflow._prometheus_query("http://prometheus.example", "up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["series_count"], 1)
        self.assertIsNone(result["error"])


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


class PrometheusReportTests(unittest.TestCase):
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
    )
    refresh_evidence_manifest(run_dir)
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
) -> None:
    queries = {}
    for name in REQUIRED_QUERIES:
        if name == failed_query:
            queries[name] = {
                "ok": False,
                "query": name,
                "series_count": None,
                "error": "connection refused",
                "series": [],
            }
        elif name == zero_query:
            queries[name] = {
                "ok": True,
                "query": name,
                "series_count": 0,
                "error": None,
                "series": [],
            }
        else:
            queries[name] = _successful_query(name)
    path = run_dir / "evidence/prometheus-memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"queries": queries}) + "\n", encoding="utf-8")


def _successful_query(query: str = "up") -> dict[str, object]:
    return {
        "ok": True,
        "query": query,
        "series_count": 1,
        "error": None,
        "series": [_series()],
    }


def _series() -> dict[str, object]:
    return {
        "metric": {"pod": "example-service-abc123", "container": "app"},
        "value": [1710000000, "42"],
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
