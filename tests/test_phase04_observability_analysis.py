from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import Mock, patch
from urllib.error import URLError

from chamber.analysis import analyze_evidence
from chamber.chaos import plan_faults
from chamber.contracts.scenario import load_scenario
from chamber.environment import EnvironmentMetadata, plan_environment
from chamber.load import plan_traffic
from chamber.observability import (
    EvidenceArtifact,
    ObservabilityCollectionError,
    PrometheusQuery,
    collect_kubernetes_evidence,
    collect_prometheus_evidence,
)
from chamber.orchestrator import build_experiment_timeline

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"
PHASE03_ARTIFACT_DIR = ROOT / "docs/internal/phases/phase-03-traffic-and-chaos/artifacts"


class Phase04CollectionTests(unittest.TestCase):
    def test_kubernetes_collector_scopes_commands_and_parses_json(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        pods = {
            "items": [
                {
                    "metadata": {"name": "sample-service-abc"},
                    "status": {"containerStatuses": []},
                }
            ]
        }
        events: dict[str, Any] = {"items": []}
        completed = [
            subprocess.CompletedProcess(("kubectl",), 0, json.dumps(pods), ""),
            subprocess.CompletedProcess(("kubectl",), 0, json.dumps(events), ""),
            subprocess.CompletedProcess(("kubectl",), 0, "startup log", ""),
        ]

        with patch("chamber.observability.collection.subprocess.run", side_effect=completed) as run:
            evidence = collect_kubernetes_evidence(environment)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            [item.signal_type for item in evidence],
            ["pod_status", "kubernetes_events", "logs"],
        )
        self.assertIn("-l", commands[0])
        self.assertIn("chamber.ampule.dev/run-id=phase04-test", commands[0][6])
        self.assertEqual(commands[2][:4], ("kubectl", "-n", environment.namespace, "logs"))
        self.assertEqual(evidence[2].resource, "sample-service-abc")

    def test_prometheus_collection_requires_endpoint_and_collects_queries(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        query = PrometheusQuery("memory_usage", "target-pods", "up")

        with patch.dict("chamber.observability.collection.os.environ", {}, clear=True):
            with self.assertRaisesRegex(ObservabilityCollectionError, "PROMETHEUS_URL"):
                collect_prometheus_evidence(environment, queries=(query,))

        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = json.dumps(
            {"status": "success", "data": {"result": [{"value": [1, "95"]}]}}
        ).encode("utf-8")

        with patch("chamber.observability.collection.urlopen", return_value=response) as urlopen:
            evidence = collect_prometheus_evidence(
                environment,
                prometheus_url="http://prometheus.local",
                queries=(query,),
            )

        self.assertEqual(evidence[0].source, "prometheus")
        self.assertEqual(evidence[0].signal_type, "memory_usage")
        self.assertEqual(evidence[0].payload["query"], "up")
        self.assertIn("/api/v1/query", urlopen.call_args.args[0])

    def test_kubernetes_collector_reports_command_and_json_failures(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata

        failed = subprocess.CompletedProcess(("kubectl",), 1, "", "forbidden")
        with patch("chamber.observability.collection.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(ObservabilityCollectionError, "forbidden"):
                collect_kubernetes_evidence(environment)

        invalid_json = subprocess.CompletedProcess(("kubectl",), 0, "not-json", "")
        with patch("chamber.observability.collection.subprocess.run", return_value=invalid_json):
            with self.assertRaisesRegex(ObservabilityCollectionError, "invalid JSON"):
                collect_kubernetes_evidence(environment)

        non_object = subprocess.CompletedProcess(("kubectl",), 0, "[]", "")
        with patch("chamber.observability.collection.subprocess.run", return_value=non_object):
            with self.assertRaisesRegex(ObservabilityCollectionError, "must be an object"):
                collect_kubernetes_evidence(environment)

    def test_prometheus_collection_reports_backend_failures(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        query = PrometheusQuery("memory_usage", "target-pods", "up")

        with patch(
            "chamber.observability.collection.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaisesRegex(ObservabilityCollectionError, "unreachable"):
                collect_prometheus_evidence(
                    environment,
                    prometheus_url="http://prometheus.local",
                    queries=(query,),
                )

        response = _http_response("not-json")
        with patch("chamber.observability.collection.urlopen", return_value=response):
            with self.assertRaisesRegex(ObservabilityCollectionError, "invalid JSON"):
                collect_prometheus_evidence(
                    environment,
                    prometheus_url="http://prometheus.local",
                    queries=(query,),
                )

        response = _http_response("[]")
        with patch("chamber.observability.collection.urlopen", return_value=response):
            with self.assertRaisesRegex(ObservabilityCollectionError, "must be an object"):
                collect_prometheus_evidence(
                    environment,
                    prometheus_url="http://prometheus.local",
                    queries=(query,),
                )

        response = _http_response(json.dumps({"status": "error", "data": {}}))
        with patch("chamber.observability.collection.urlopen", return_value=response):
            with self.assertRaisesRegex(ObservabilityCollectionError, "did not succeed"):
                collect_prometheus_evidence(
                    environment,
                    prometheus_url="http://prometheus.local",
                    queries=(query,),
                )


class Phase04AnalysisTests(unittest.TestCase):
    def test_detects_oom_restart_loop_prometheus_and_k6_findings(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "oom-stress.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        evidence = (
            _evidence(
                environment,
                "kubernetes",
                "pod_status",
                environment.namespace,
                {
                    "items": [
                        {
                            "metadata": {"name": "sample-service-abc"},
                            "status": {
                                "containerStatuses": [
                                    {
                                        "name": "sample-service",
                                        "restartCount": 4,
                                        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                        "lastState": {"terminated": {"reason": "OOMKilled"}},
                                    }
                                ]
                            },
                        }
                    ]
                },
            ),
            _prometheus_evidence(environment, "memory_usage", "target-pods", 94),
            _prometheus_evidence(environment, "cpu_throttling", "target-pods", 22),
        )
        k6_summary = {
            "metrics": {
                "http_req_duration": {"p(95)": 1500},
                "http_req_failed": {"value": 0.08},
            }
        }

        with TemporaryDirectory() as artifact_dir:
            summary_path = Path(artifact_dir) / "summary.json"
            summary_path.write_text(json.dumps(k6_summary), encoding="utf-8")
            result = analyze_evidence(evidence, scenario=scenario, k6_summary_path=summary_path)

        signal_types = {finding.signal_type for finding in result.findings}
        self.assertIn("oom_killed", signal_types)
        self.assertIn("restart_loop", signal_types)
        self.assertIn("memory_usage", signal_types)
        self.assertIn("cpu_throttling", signal_types)
        self.assertIn("request_latency", signal_types)
        self.assertIn("error_rate", signal_types)
        self.assertTrue(all(finding.evidence_ids for finding in result.findings))
        self.assertTrue(any(finding.severity == "critical" for finding in result.findings))

    def test_correlates_error_rate_to_traffic_and_fault_windows(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        traffic_plan = plan_traffic(
            scenario,
            environment=environment,
            artifact_dir=PHASE03_ARTIFACT_DIR,
        )
        with TemporaryDirectory() as artifact_dir:
            fault_plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)
        timeline = build_experiment_timeline(
            scenario,
            traffic_plan=traffic_plan,
            fault_plan=fault_plan,
        )
        evidence = (_prometheus_evidence(environment, "error_rate", "target-service", 47),)

        result = analyze_evidence(evidence, scenario=scenario, timeline=timeline)

        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertEqual(finding.signal_type, "error_rate")
        self.assertIn("traffic-start", finding.related_timeline_ids)
        self.assertIn("downstream-api-unavailable-start", finding.related_timeline_ids)
        self.assertIn("downstream-api-unavailable-removed", finding.related_timeline_ids)

    def test_k6_dependency_fault_summary_from_phase03_produces_error_finding(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "dependency-failure.yaml")
        environment = plan_environment(scenario, run_id="phase04-live-fixture").metadata
        evidence = (
            _evidence(environment, "fixture", "logs", "sample-service", {"text": "fixture"}),
        )
        summary_path = PHASE03_ARTIFACT_DIR / "live-dependency-fault-k6-summary.json"

        result = analyze_evidence(evidence, scenario=scenario, k6_summary_path=summary_path)

        error_findings = [
            finding for finding in result.findings if finding.signal_type == "error_rate"
        ]
        self.assertEqual(len(error_findings), 1)
        self.assertIn("47.9853%", error_findings[0].observed_facts[0])

    def test_detects_kubernetes_oom_event_and_restart_count_only_loop(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "oom-stress.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        evidence = (
            _evidence(
                environment,
                "kubernetes",
                "pod_status",
                environment.namespace,
                {
                    "items": [
                        {
                            "status": {
                                "containerStatuses": [{"name": "sample-service", "restartCount": 3}]
                            }
                        },
                        {"status": {"containerStatuses": "not-a-list"}},
                        {"status": {"containerStatuses": ["not-a-dict"]}},
                    ]
                },
            ),
            _evidence(
                environment,
                "kubernetes",
                "kubernetes_events",
                environment.namespace,
                {
                    "items": [
                        {
                            "reason": "OOMKilling",
                            "message": "container was oom killed",
                            "involvedObject": {"name": "sample-service-abc"},
                        }
                    ]
                },
            ),
        )

        result = analyze_evidence(evidence, scenario=scenario)

        signal_types = [finding.signal_type for finding in result.findings]
        self.assertEqual(signal_types.count("restart_loop"), 1)
        self.assertEqual(signal_types.count("oom_killed"), 1)
        restart = next(
            finding for finding in result.findings if finding.signal_type == "restart_loop"
        )
        self.assertEqual(restart.confidence, "medium")

    def test_prometheus_thresholds_cover_cpu_latency_and_no_finding_paths(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        evidence = (
            _prometheus_evidence(environment, "cpu_usage", "target-pods", 95),
            _prometheus_evidence(environment, "request_latency", "target-service", 1200),
            _prometheus_evidence(environment, "memory_usage", "target-pods", 50),
            _prometheus_evidence(environment, "unknown_metric", "target", 999),
            _evidence(
                environment,
                "prometheus",
                "error_rate",
                "target-service",
                {"query": "bad-shape"},
            ),
        )

        result = analyze_evidence(evidence, scenario=scenario)

        signal_types = {finding.signal_type for finding in result.findings}
        self.assertEqual(signal_types, {"cpu_usage", "request_latency"})

    def test_empty_evidence_and_invalid_k6_summary_are_handled_explicitly(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")

        result = analyze_evidence((), scenario=scenario)

        self.assertEqual(result.run_id, "")
        self.assertEqual(result.scenario_id, "baseline-health-001")
        self.assertEqual(result.findings, ())

        with TemporaryDirectory() as artifact_dir:
            summary_path = Path(artifact_dir) / "bad-summary.json"
            summary_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "k6 summary"):
                analyze_evidence((), scenario=scenario, k6_summary_path=summary_path)

    def test_k6_summary_without_relevant_metrics_produces_no_findings(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        evidence = (_evidence(environment, "fixture", "logs", "sample-service", {"text": "ok"}),)

        with TemporaryDirectory() as artifact_dir:
            summary_path = Path(artifact_dir) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "metrics": {
                            "http_reqs": {"count": 10},
                            "http_req_failed": {"value": 0.01},
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = analyze_evidence(evidence, scenario=scenario, k6_summary_path=summary_path)

        self.assertEqual(result.findings, ())

        with TemporaryDirectory() as artifact_dir:
            summary_path = Path(artifact_dir) / "summary.json"
            summary_path.write_text(json.dumps({"metrics": []}), encoding="utf-8")
            result = analyze_evidence(evidence, scenario=scenario, k6_summary_path=summary_path)

        self.assertEqual(result.findings, ())

    def test_custom_threshold_and_range_metrics_are_detected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "baseline-health.yaml")
        scenario.document["failureConditions"].append(
            {
                "type": "cpu_throttling_above_percent",
                "description": "CPU throttling above 10 percent is risky.",
                "threshold": 10,
            }
        )
        environment = plan_environment(scenario, run_id="phase04-test").metadata
        evidence = (
            _evidence(
                environment,
                "prometheus",
                "cpu_throttling",
                "target-pods",
                {
                    "query": "cpu_throttling",
                    "result": {
                        "status": "success",
                        "data": {
                            "result": [
                                "not-a-series",
                                {"values": [[1781280000, "bad"], [1781280060, "15"]]},
                            ]
                        },
                    },
                },
            ),
        )
        traffic_plan = plan_traffic(
            scenario,
            environment=environment,
            artifact_dir=PHASE03_ARTIFACT_DIR,
        )
        with TemporaryDirectory() as artifact_dir:
            fault_plan = plan_faults(scenario, environment=environment, artifact_dir=artifact_dir)
        timeline = build_experiment_timeline(
            scenario,
            traffic_plan=traffic_plan,
            fault_plan=fault_plan,
        )

        result = analyze_evidence(evidence, scenario=scenario, timeline=timeline)

        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].signal_type, "cpu_throttling")
        self.assertIn("traffic-start", result.findings[0].related_timeline_ids)


def _http_response(text: str) -> Mock:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.read.return_value = text.encode("utf-8")
    return response


def _evidence(
    environment: EnvironmentMetadata,
    source: str,
    signal_type: str,
    resource: str,
    payload: dict[str, object],
) -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=f"{environment.run_id}:{source}:{signal_type}:{resource}",
        run_id=environment.run_id,
        scenario_id=environment.scenario_id,
        source=source,
        signal_type=signal_type,
        resource=resource,
        collected_at="2026-06-12T00:00:00Z",
        start_time=None,
        end_time=None,
        payload=payload,
    )


def _prometheus_evidence(
    environment: EnvironmentMetadata,
    signal_type: str,
    resource: str,
    value: float,
) -> EvidenceArtifact:
    return _evidence(
        environment,
        "prometheus",
        signal_type,
        resource,
        {
            "query": signal_type,
            "result": {
                "status": "success",
                "data": {"result": [{"value": [1781280000, str(value)]}]},
            },
        },
    )


if __name__ == "__main__":
    unittest.main()
