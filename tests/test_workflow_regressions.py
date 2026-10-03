"""Failure-path and evidence fidelity regressions for the guided workflow."""

from __future__ import annotations

import io
import subprocess
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import chamber.workflow as workflow


def config_fixture(repo: Path) -> dict[str, Any]:
    return {
        "kind": "ChamberConfig",
        "service": {"name": "checkout-api", "repo": str(repo)},
        "deployment": {
            "manifests": ["app.yaml"],
            "workloads": [{"name": "checkout-api", "kind": "Deployment"}],
        },
        "traffic": {"journeys": [{"name": "health", "path": "/health"}]},
        "runtime": {},
        "agents": {"mode": "off"},
    }


class WorkflowValidationRegressions(unittest.TestCase):
    def test_config_validation_rejects_incomplete_or_inconsistent_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            valid = config_fixture(Path(tmp))
            cases = [
                (
                    {"service": {"name": "checkout", "repo": str(Path(tmp) / "missing")}},
                    "does not exist",
                ),
                (
                    {"deployment": {"manifests": [], "workloads": [{}]}},
                    "manifests must not be empty",
                ),
                (
                    {"deployment": {"manifests": ["app.yaml"], "workloads": []}},
                    "workloads must not be empty",
                ),
                ({"traffic": {"journeys": []}}, "journeys must not be empty"),
                ({"agents": {"mode": "invented"}}, "agents.mode must be one of"),
                ({"traffic": {"journeys": [{"adapter": "relayna"}]}}, "journeys\\[0\\]"),
                (
                    {
                        "scenarioId": "one",
                        "scenario": {
                            "id": "two",
                            "name": "Two",
                            "source": "user",
                            "revision": "abc",
                        },
                    },
                    "must match scenarioId",
                ),
            ]
            for changes, message in cases:
                with self.subTest(changes=changes):
                    candidate = {**deepcopy(valid), **changes}
                    with self.assertRaisesRegex(workflow.WorkflowError, message):
                        workflow.validate_config(candidate)
            scenario = {
                "id": "one",
                "name": "One",
                "source": "user",
                "revision": "abc",
                "origin": {"source": "bundled", "revision": "def"},
            }
            workflow.validate_config({**valid, "scenarioId": "one", "scenario": scenario})

    def test_attach_validation_checks_service_names_and_ports(self) -> None:
        with TemporaryDirectory() as tmp:
            config = config_fixture(Path(tmp))
            config["runtime"] = {
                "provider": "kubernetes",
                "mode": "attach",
                "namespace": "sandbox",
                "kubernetesContext": "dev",
                "trafficAccess": {"mode": "endpoint", "url": "http://target"},
            }
            for services, message in (
                ([], "must not be empty in attach mode"),
                ([{"name": "checkout", "port": 0}], "positive integer"),
                ([{"name": "checkout", "port": "80"}], "positive integer"),
            ):
                with self.subTest(services=services):
                    config["deployment"]["services"] = services
                    with self.assertRaisesRegex(workflow.WorkflowError, message):
                        workflow.validate_config(config)
            config["deployment"]["services"] = [{"name": "checkout", "port": 80}]
            workflow.validate_config(config)

    def test_runtime_validation_rejects_unsafe_settings(self) -> None:
        cases = [
            ({"provider": "unknown"}, "provider must be one of"),
            ({"mode": "unknown"}, "mode must be one of"),
            ({"accessToken": "secret"}, "looks secret-like"),
            ({"config": {"database-password": "secret"}}, "looks secret-like"),
            ({"provider": "kubernetes", "kubernetesContext": "dev"}, "trafficAccess is required"),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "trafficAccess": {"mode": "tunnel"},
                },
                "mode must be one of",
            ),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "mode": "attach",
                    "namespace": "test",
                    "cleanup": True,
                },
                "cleanup must be false",
            ),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "mode": "attach",
                    "namespace": "test",
                    "faults": [{"type": "invented"}],
                },
                "type must be one of",
            ),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "mode": "attach",
                    "namespace": "test",
                    "faults": [{"type": "deployment_scale", "replicas": -1}],
                },
                "non-negative integer",
            ),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "trafficAccess": {
                        "mode": "port-forward",
                        "service": "checkout",
                        "servicePort": 0,
                    },
                },
                "positive integer",
            ),
            (
                {
                    "provider": "kubernetes",
                    "kubernetesContext": "dev",
                    "trafficAccess": {"mode": "endpoint", "url": "file:///tmp/target"},
                },
                "HTTP\\(S\\)",
            ),
        ]
        for runtime, message in cases:
            with (
                self.subTest(runtime=runtime),
                self.assertRaisesRegex(workflow.WorkflowError, message),
            ):
                workflow._validate_runtime(runtime, source="runtime")
        workflow._validate_runtime(
            {
                "provider": "kubernetes",
                "kubernetesContext": "dev",
                "trafficAccess": {"mode": "endpoint", "url": "https://target"},
                "prometheusUrl": "https://metrics",
            },
            source="runtime",
        )

    def test_missing_and_scalar_config_errors_are_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            with self.assertRaisesRegex(workflow.WorkflowError, "cannot read config"):
                workflow.load_config(path)
            path.write_text("- not-a-config\n", encoding="utf-8")
            with self.assertRaisesRegex(workflow.WorkflowError, "YAML mapping"):
                workflow.load_config(path)
            self.assertEqual(
                workflow._config_workspace(Path(tmp) / "runs/run/chamber.yaml"), Path(tmp).resolve()
            )
            self.assertEqual(
                workflow._config_workspace(Path(tmp) / "drafts/chamber.yaml"), Path(tmp).resolve()
            )

    def test_assessment_dispatch_rejects_ambiguous_runtime_input(self) -> None:
        for options, message in (
            ({"mode": "kubernetes"}, "requires --config only"),
            ({"mode": "remote"}, "only --mode local"),
            ({}, "requires --repo"),
        ):
            with (
                self.subTest(options=options),
                self.assertRaisesRegex(workflow.WorkflowError, message),
            ):
                workflow.assess(**dict[str, Any](options))
        with (
            TemporaryDirectory() as tmp,
            self.assertRaisesRegex(workflow.WorkflowError, "not a resumable"),
        ):
            workflow.assess(resume=Path(tmp))

    def test_reserved_run_directory_accepts_only_supervisor_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "run"
            directory.mkdir()
            for filename in ("run.json", "events.jsonl", "execution-config.yaml"):
                (directory / filename).touch()
            self.assertEqual(workflow._assessment_directory("test", directory), directory)
            (directory / "result.json").touch()
            with self.assertRaisesRegex(
                workflow.WorkflowError, "already contains execution artifacts"
            ):
                workflow._assessment_directory("test", directory)

    def test_agent_overrides_reject_bad_roles_and_preserve_order(self) -> None:
        with self.assertRaisesRegex(workflow.WorkflowError, "agents must be a mapping"):
            workflow._apply_agent_exclude_override({"agents": []}, ("report",))
        with self.assertRaisesRegex(workflow.WorkflowError, "unknown agent role"):
            workflow._agent_exclusions({"agents": {"exclude": ["invented"]}}, ())
        names = tuple(workflow.AGENT_NAMES[:2])
        config = {"agents": {"exclude": [names[0]]}}
        workflow._apply_agent_exclude_override(config, names)
        self.assertEqual(config["agents"]["exclude"], list(names))
        self.assertEqual(workflow._normalized_agent_names((" a, b ", "a", "")), ("a", "b"))

    def test_context_failure_is_not_reported_as_a_selected_cluster(self) -> None:
        with patch.object(workflow.shutil, "which", return_value=None):
            self.assertIsNone(workflow._current_kube_context())
        with (
            patch.object(workflow.shutil, "which", return_value="kubectl"),
            patch.object(
                workflow.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "", "unauthorized"),
            ),
        ):
            self.assertIsNone(workflow._current_kube_context())
        with (
            patch.object(workflow, "_current_kube_context", return_value="customer-production"),
            self.assertRaisesRegex(workflow.WorkflowError, "refusing unsafe"),
        ):
            workflow._validate_assessment_safety()


class WorkflowPrometheusRegressions(unittest.TestCase):
    def test_http_error_bodies_preserve_valid_prometheus_errors(self) -> None:
        error = HTTPError(
            "http://metrics",
            422,
            "invalid",
            Message(),
            io.BytesIO(b'{"status":"error","error":"bad query"}'),
        )
        with patch.object(workflow, "urlopen", side_effect=error):
            self.assertEqual(
                workflow._read_prometheus_payload("http://metrics")["error"], "bad query"
            )

    def test_http_error_bodies_are_checked_for_type_encoding_and_readability(self) -> None:
        for body, message in (
            (b"", "body was empty"),
            (b"oops", "not valid JSON"),
            (b"\xff", "not valid JSON"),
            (b"[]", "not a JSON object"),
        ):
            with self.subTest(body=body):
                error = HTTPError("http://metrics", 500, "failed", Message(), io.BytesIO(body))
                with (
                    patch.object(workflow, "urlopen", side_effect=error),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    workflow._read_prometheus_payload("http://metrics")
        error = HTTPError("http://metrics", 500, "", Message(), io.BytesIO())
        with (
            patch.object(error, "read", side_effect=OSError("unavailable")),
            patch.object(workflow, "urlopen", side_effect=error),
            self.assertRaisesRegex(RuntimeError, "body could not be read"),
        ):
            workflow._read_prometheus_payload("http://metrics")

    def test_query_failures_are_explicit_in_instant_and_range_results(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for payload, message in (
            ([], "non-object"),
            (
                {"status": "error", "errorType": "bad_data", "error": "bad query"},
                "bad_data: bad query",
            ),
            ({"status": "error", "error": "failed"}, "failed"),
            ({"status": "unknown"}, "unknown"),
            ({"status": "success", "data": {}}, "data.result"),
        ):
            with (
                self.subTest(payload=payload),
                patch.object(workflow, "_read_prometheus_payload", return_value=payload),
            ):
                instant = workflow._prometheus_query("http://metrics", "up")
                ranged = workflow._prometheus_query_range(
                    "http://metrics", "up", start=start, end=start, step_seconds=15
                )
                for result in (instant, ranged):
                    self.assertFalse(result["ok"])
                    self.assertIn(message, result["error"])
                    self.assertEqual(result["series"], [])
        for url in ("file:///etc/passwd", "http:"):
            with self.subTest(url=url):
                self.assertFalse(workflow._prometheus_query(url, "up")["ok"])
                self.assertFalse(
                    workflow._prometheus_query_range(
                        url, "up", start=start, end=start, step_seconds=1
                    )["ok"]
                )

    def test_pod_peaks_sum_container_samples_and_ignore_nonfinite_values(self) -> None:
        malformed = [
            None,
            {"metric": None},
            {"metric": {"pod": "p"}, "values": None},
            {"metric": {"pod": "p"}, "values": [None, [], [1, "bad"], [2, "nan"], [3, "inf"]]},
        ]
        series = malformed + [
            {"metric": {"pod": "p"}, "values": [[1, "2"], [2, "3"]]},
            {"metric": {"pod": "p"}, "values": [[1, "5"]]},
        ]
        self.assertEqual(workflow._prometheus_pod_peaks(series), ({"p": 7.0}, {"p": 2}))
        self.assertEqual(workflow._prometheus_pod_peaks(None), ({}, {}))

    def test_range_summaries_disclose_failed_queries_and_observed_workers(self) -> None:
        queries = {
            "container_memory_working_set_bytes": {
                "ok": True,
                "series": [{"metric": {"pod": "worker"}, "values": [[1, 12]]}],
            },
            "container_cpu_usage_cores": None,
            "kube_pod_container_status_restarts_total": {"ok": False, "error": "denied"},
        }
        summaries = workflow._prometheus_range_summaries(
            queries, workloads=[{}, {"pod_name": "api", "role": "api"}]
        )
        self.assertEqual(
            summaries[0]["errors"], ["kube_pod_container_status_restarts_total: denied"]
        )
        self.assertEqual(summaries[1]["pod_name"], "worker")
        self.assertEqual(summaries[1]["peak_memory_bytes"], 12)
        self.assertEqual(summaries[1]["sample_count"], 1)
        summaries = workflow._prometheus_range_summaries(
            {
                "container_memory_working_set_bytes": {
                    "ok": True,
                    "pod_summaries": {"api": {"peak": 3, "sample_count": 2}, "invalid": None},
                }
            },
            workloads=[{"pod_name": "api"}],
        )
        self.assertEqual(summaries[0]["peak_memory_bytes"], 3)

    def test_report_explains_corrupt_absent_and_empty_metric_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            evidence = run / "evidence"
            evidence.mkdir()
            path = evidence / "prometheus-memory.json"
            for raw, expected in (
                ("invalid", "unavailable"),
                ("[]", "JSON object"),
                ("{}", "queries are missing"),
            ):
                path.write_text(raw, encoding="utf-8")
                self.assertIn(
                    expected, " ".join(workflow._prometheus_report_sections(run)[0].lines)
                )
            workflow._write_json(
                path,
                {
                    "pod_names": ["api", "worker"],
                    "queries": {
                        "skip": None,
                        "failed": {"ok": False},
                        "empty": {"ok": True, "series_count": 0},
                        "partial": {
                            "ok": True,
                            "series_count": 1,
                            "series": [{"metric": {"pod": "api"}, "value": [1, "12"]}],
                        },
                    },
                },
            )
            rendered = " ".join(workflow._prometheus_report_sections(run)[0].lines)
            self.assertIn("unknown Prometheus query failure", rendered)
            self.assertIn("no matching target series", rendered)
            self.assertIn("missing_selected_pods=worker", rendered)
            self.assertEqual(workflow._prometheus_series_sample(None), "sample unavailable")
            self.assertEqual(
                workflow._prometheus_series_sample([{"metric": {"pod": "api"}}]),
                "pod=api; sample unavailable",
            )


class WorkflowProjectionRegressions(unittest.TestCase):
    def test_agent_metric_projections_bound_details_and_ignore_invalid_metrics(self) -> None:
        self.assertEqual(workflow._k6_agent_details({}), [])
        self.assertEqual(workflow._prometheus_memory_agent_details({}), [])
        details = workflow._prometheus_memory_agent_details(
            {
                "queries": {
                    "invalid": [],
                    "memory": {"ok": False, "error": "missing", "series": list(range(10))},
                }
            }
        )
        self.assertIn('"series": [0, 1, 2, 3, 4]', details[0])
        self.assertNotIn("invalid", details[0])
        self.assertEqual(workflow._metric_number({"count": True}, "count"), None)
        self.assertEqual(workflow._metric_number([], "count"), None)
        self.assertEqual(workflow._metric_number({"count": 12}, "count"), 12)
        detail = workflow._agent_detail("logs", {"data": "x" * (workflow.AGENT_DETAIL_LIMIT + 100)})
        self.assertTrue(detail.endswith("...<truncated>"))
        self.assertLessEqual(len(detail), workflow.AGENT_DETAIL_LIMIT + 6)
        self.assertEqual(
            workflow._bounded_agent_details(["a", "x" * workflow.AGENT_DETAIL_TOTAL_LIMIT, "b"]),
            ("a",),
        )

    def test_attach_and_command_projections_keep_operational_fields_only(self) -> None:
        self.assertEqual(workflow._kubernetes_command_agent_details({}), [])
        details = workflow._kubernetes_command_agent_details(
            {
                "commands": [
                    None,
                    {"command": "invalid"},
                    {
                        "command": ["kubectl", "logs", "api"],
                        "exit_status": 1,
                        "stdout": "x" * 1000,
                        "stderr": "error",
                    },
                ]
            }
        )
        self.assertIn('"failed_command_count": 1', details[0])
        self.assertNotIn("x" * 701, details[0])
        details = workflow._attach_discovery_agent_details(
            {
                "namespace": "sandbox",
                "workloads": [None, {"name": "api", "secret": "hidden"}],
                "services": [None, {"name": "api"}],
                "pods": [None, {"name": "api-pod"}],
            }
        )
        self.assertIn("api-pod", details[0])
        self.assertNotIn("hidden", details[0])
        self.assertIn('"checks": []', workflow._preflight_agent_details({"checks": None})[0])
        self.assertEqual(workflow._agent_payload_lines({}), ("No agent details recorded.",))
        self.assertEqual(workflow._unique_strings("invalid"), ())

    def test_evidence_link_aliases_do_not_invent_unregistered_artifacts(self) -> None:
        available = {"kubernetes-commands": "commands.json", "k6-summary": "k6.json"}
        self.assertEqual(
            workflow._report_evidence_id("kubernetes-command-12", available), "kubernetes-commands"
        )
        self.assertEqual(
            workflow._report_evidence_id("source:k6-summary:checks", available), "k6-summary"
        )
        self.assertIsNone(workflow._report_evidence_id("unknown", available))
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            workflow._write_json(run / "findings.json", {})
            self.assertEqual(workflow._finding_ids(run), ())
            with self.assertRaisesRegex(workflow.WorkflowError, "JSON list"):
                workflow._persisted_report_findings(run)

    def test_upload_report_skips_malformed_metadata_and_keeps_safe_file_facts(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "evidence").mkdir()
            path = run / "evidence/relayna-summary.json"
            for payload in (
                {},
                {"inputs": [None, {"files": None}]},
                {"inputs": [{"files": [None]}]},
            ):
                workflow._write_json(path, payload)
                self.assertEqual(workflow._relayna_input_sections(run), ())
            workflow._write_json(
                path,
                {
                    "inputs": [
                        {
                            "journey": "ocr",
                            "files": [
                                {
                                    "field": "image",
                                    "filename": "example.png",
                                    "size_bytes": 12,
                                    "sha256": "digest",
                                }
                            ],
                        }
                    ]
                },
            )
            rendered = workflow._relayna_input_sections(run)[0].lines[0]
            self.assertIn("example.png", rendered)
            self.assertIn("12 bytes", rendered)
            self.assertIn("sha256=digest", rendered)

    def test_load_report_preserves_baseline_fault_recovery_and_target_assertions(self) -> None:
        metrics = {
            "started": 3,
            "requested": 4,
            "dropped": 1,
            "successful": 2,
            "p95Ms": 100,
            "p99Ms": 120,
            "errorRate": 0.25,
            "completionRate": 0.5,
            "queueWaitP95Ms": 5,
            "workerP95Ms": 20,
            "queueTimingSamples": 3,
        }

        def window(phase: str) -> dict[str, Any]:
            return {
                "phase": phase,
                "step": 1,
                "state": "pass",
                "ratePerSecond": 2,
                "achievedStartsPerSecond": 1.5,
                "journeys": [
                    {
                        "name": "checkout",
                        "metrics": metrics,
                        "assertions": [
                            {"name": "latency", "state": "pass", "expected": 150, "observed": 100}
                        ],
                    }
                ],
            }

        summary = {
            "status": "complete",
            "metrics": metrics,
            "windows": [window("fault")],
            "phases": [
                {"phase": "recovery", "windows": [window("recovery")]},
                {"phase": "baseline", "windows": [window("baseline")]},
            ],
            "generator": {"limited": False, "peakActiveRequests": 3, "rssHighWaterGrowthMiB": 1},
            "targetMetrics": {
                "assertions": [{"name": "memory", "state": "pass", "observed": 12, "expected": 100}]
            },
            "timingNote": "end-to-end timing",
        }
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            workflow._write_json(
                run / "result.json",
                {
                    "load": summary,
                    "experiment": {
                        "family": "pod_kill",
                        "assertions": [
                            {
                                "name": "rollback",
                                "state": "pass",
                                "expected": True,
                                "observed": True,
                            }
                        ],
                    },
                },
            )
            lines = workflow._load_report_sections(run)[0].lines
            joined = "\n".join(lines)
            self.assertLess(joined.index("baseline step"), joined.index("fault step"))
            self.assertLess(joined.index("fault step"), joined.index("recovery step"))
            self.assertIn("queue/worker p95 5/20 ms", joined)
            self.assertIn("memory: pass", joined)
            self.assertIn(
                "rollback: pass", " ".join(workflow._experiment_report_sections(run)[0].lines)
            )

    def test_manifest_image_inference_handles_nonworkloads_and_alternate_templates(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.yaml").write_text(
                "not: kubernetes\n---\napiVersion: apps/v1\nkind: Deployment\nmetadata: {}\n",
                encoding="utf-8",
            )
            self.assertEqual(workflow._workloads_from_manifests(repo, ["app.yaml"]), [])
            self.assertIsNone(
                workflow._first_container_image(repo, "missing", "Deployment", ["app.yaml"])
            )
            self.assertEqual(workflow._yaml_documents(repo / "missing"), ())
            template = {"spec": {"containers": [{"image": "worker:v1"}]}}
            self.assertEqual(
                workflow._pod_template({"kind": "Job", "spec": {"template": template}}), template
            )
            self.assertEqual(
                workflow._pod_template(
                    {"kind": "CronJob", "spec": {"jobTemplate": {"spec": {"template": template}}}}
                ),
                template,
            )
            self.assertEqual(
                workflow._pod_template(
                    {"kind": "ScaledJob", "spec": {"jobTargetRef": {"template": template}}}
                ),
                template,
            )
            (repo / "Dockerfile").touch()
            self.assertEqual(
                workflow._image_config(repo, "api", [])["api"]["image"], "ampule/api:local"
            )
            with patch.object(workflow.subprocess, "run", side_effect=OSError("missing git")):
                self.assertEqual(workflow._git_commit(repo), "unknown")
        builds, replacements = workflow._image_specs({"plain": "api:v1", "invalid": 42})
        self.assertEqual(builds[0].image, "api:v1")
        self.assertEqual(replacements, ())
        self.assertEqual(workflow._dns_fragment("--"), "service")

    def test_read_json_and_structural_helpers_reject_incorrect_types(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            with self.assertRaisesRegex(workflow.WorkflowError, "cannot read"):
                workflow._read_json(path)
            workflow._write_json(path, [])
            with self.assertRaisesRegex(workflow.WorkflowError, "JSON object"):
                workflow._read_json(path)
        for helper in (workflow._mapping, workflow._list, workflow._non_empty):
            with self.subTest(helper=helper), self.assertRaises(workflow.WorkflowError):
                helper(None, "field")

    def test_kubernetes_json_errors_include_operation_description(self) -> None:
        runner = Mock()
        command = ("kubectl", "get", "pods")
        for stdout, message in (
            ("{", "failed to parse read pods JSON"),
            ("[]", "read pods JSON must be an object"),
        ):
            runner.run.return_value = subprocess.CompletedProcess(command, 0, stdout, "")
            with (
                self.subTest(stdout=stdout),
                self.assertRaisesRegex(workflow.WorkflowError, message),
            ):
                workflow._kubectl_json(runner, command, [], "read pods")
        self.assertEqual(
            workflow._match_labels_selector({"spec": {"selector": {"matchLabels": []}}}), {}
        )
        self.assertIsNone(workflow._kubernetes_datetime("not-a-time"))
        self.assertIsNone(workflow._kubernetes_datetime(12))
        self.assertIsNone(workflow._pod_finished_at({"containerStatuses": [None, {"state": []}]}))

    def test_required_http_upload_without_path_fails_before_script_execution(self) -> None:
        journey = {
            "name": "upload",
            "requestEncoding": "multipart",
            "multipart": {"files": [None, {"field": "image", "required": True}]},
        }
        with self.assertRaisesRegex(workflow.WorkflowError, "readable path"):
            workflow._k6_script_for_journeys((journey,), base_url="http://target")
        self.assertEqual(workflow._duration_seconds("invalid"), 1)
        self.assertEqual(workflow._k6_function_name("123 upload", index=1), "journey_123_upload_1")
        self.assertEqual(workflow._k6_function_name("?!", index=2), "journey_2")


class RecordingRunner:
    """Keep all external execution synthetic and inspectable."""

    def __init__(self, *, exit_status: int = 0, stdout: str = "{}") -> None:
        self.commands: list[tuple[str, ...]] = []
        self.exit_status = exit_status
        self.stdout = stdout

    def run(
        self, command: tuple[str, ...], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return subprocess.CompletedProcess(
            command, self.exit_status, self.stdout, "synthetic failure" if self.exit_status else ""
        )


class WorkflowAttachSafetyRegressions(unittest.TestCase):
    def test_restore_rejects_corrupt_ledger_and_exposes_manual_remediation(self) -> None:
        for ledger in (
            {},
            {"pending_restore": [None]},
            {"pending_restore": [{"type": "pod_kill"}]},
        ):
            with self.subTest(ledger=ledger):
                runner = RecordingRunner()
                restored = workflow._restore_attach_faults(
                    dict[str, object](ledger),
                    context="dev",
                    namespace="sandbox",
                    runner=runner,
                    commands=[],
                )
                self.assertFalse(restored["verified"])
                self.assertEqual(runner.commands, [])
        action = {"type": "deployment_scale", "deployment": "api", "original_replicas": "3"}
        ledger = {"pending_restore": [action], "actions": [action]}
        runner = RecordingRunner(exit_status=1)
        restored = workflow._restore_attach_faults(
            dict[str, object](ledger),
            context="dev",
            namespace="sandbox",
            runner=runner,
            commands=[],
        )
        self.assertFalse(restored["verified"])
        self.assertIn("--replicas=3", action["manual_remediation"])
        self.assertEqual(restored["pending_restore"], [])

    def test_pod_fault_rejects_missing_targets_and_unverified_restoration(self) -> None:
        for discovery, fault, message in (
            ({}, {}, "at least one discovered pod"),
            ({"pods": [{"name": "api"}]}, {"pod": "missing"}, "not discovered"),
            (
                {"pods": [{"name": "api"}], "workloads": [{"kind": "Job", "name": "worker"}]},
                {"pod": "api"},
                "rollback could not verify",
            ),
        ):
            with (
                self.subTest(discovery=discovery),
                self.assertRaisesRegex(workflow.WorkflowError, message),
            ):
                workflow._run_attach_pod_kill(
                    fault,
                    discovery=discovery,
                    context="dev",
                    namespace="sandbox",
                    runner=RecordingRunner(),
                    commands=[],
                    actions=[],
                )

    def test_scale_fault_defaults_original_replica_count_and_registers_rollback_before_error(
        self,
    ) -> None:
        with self.assertRaisesRegex(workflow.WorkflowError, "requires a discovered Deployment"):
            workflow._run_attach_deployment_scale(
                {"replicas": 0},
                discovery={},
                context="dev",
                namespace="sandbox",
                runner=RecordingRunner(),
                commands=[],
                actions=[],
                pending=[],
            )
        actions: list[dict[str, object]] = []
        pending: list[dict[str, object]] = []
        with self.assertRaisesRegex(workflow.WorkflowError, "scale deployment/api"):
            workflow._run_attach_deployment_scale(
                {"replicas": 0},
                discovery={"workloads": [{"kind": "Deployment", "name": "api"}]},
                context="dev",
                namespace="sandbox",
                runner=RecordingRunner(exit_status=1),
                commands=[],
                actions=actions,
                pending=pending,
            )
        self.assertEqual(actions, pending)
        self.assertEqual(actions[0]["original_replicas"], 1)
        self.assertFalse(actions[0]["restored"])
        self.assertTrue(
            workflow._attach_faults_allowed(
                {"workloads": [{"labels": {"chamber.ampule.dev/allow-faults": "true"}}]}
            )
        )

    def test_attach_preflight_failure_persists_result_without_target_mutation(self) -> None:
        from chamber.environment.preflight import KubernetesPreflightResult

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = config_fixture(root)
            config["deployment"]["manifests"] = []
            config["deployment"]["services"] = [{"name": "api", "port": 80}]
            config["runtime"] = {
                "provider": "kubernetes",
                "mode": "attach",
                "namespace": "sandbox",
                "kubernetesContext": "dev",
                "trafficAccess": {"mode": "endpoint", "url": "http://api"},
            }
            runner = RecordingRunner()
            run = root / "failed-run"
            workflow.save_config(config, root / "chamber.yaml")
            preflight = KubernetesPreflightResult(
                "dev", "sandbox", False, ("pod access denied",), ()
            )
            with (
                patch.object(workflow, "init_workspace"),
                patch.object(workflow, "run_kubernetes_attach_preflight", return_value=preflight),
                self.assertRaisesRegex(
                    workflow.WorkflowError, "attach preflight failed: pod access denied"
                ),
            ):
                workflow._assess_kubernetes_config(
                    root / "chamber.yaml",
                    agents_mode="off",
                    context=None,
                    prometheus_url=None,
                    runner=runner,
                    run_dir=run,
                )
            metadata = workflow._read_json(run / "run-metadata.json")
            self.assertEqual(metadata["stage"], "preflight_failed")
            self.assertFalse(metadata["success"])
            self.assertFalse(metadata["cleanup_performed"])
            self.assertEqual(runner.commands, [])
            self.assertTrue((run / "result.json").exists())
            self.assertEqual(metadata["preflight"]["blockers"], ["pod access denied"])
            self.assertIn("Status: preflight_failed", (run / "report.md").read_text())

    def test_attach_rollback_failure_persists_failed_outcome_before_raise(self) -> None:
        from chamber.environment.preflight import KubernetesPreflightResult

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = config_fixture(root)
            config["deployment"]["services"] = [{"name": "api", "port": 80}]
            config["runtime"] = {
                "provider": "kubernetes",
                "mode": "attach",
                "namespace": "sandbox",
                "kubernetesContext": "dev",
                "trafficAccess": {"mode": "endpoint", "url": "http://api"},
                "faults": [{"type": "deployment_scale", "replicas": 0}],
            }
            discovery = {
                "namespace": {"labels": {"chamber.ampule.dev/allow-faults": "true"}},
                "workloads": [{"kind": "Deployment", "name": "api", "replicas": 2}],
                "pods": [],
            }
            run = root / "failed-rollback"
            with (
                patch.object(workflow, "init_workspace"),
                patch.object(
                    workflow,
                    "run_kubernetes_attach_preflight",
                    return_value=KubernetesPreflightResult("dev", "sandbox", True, (), ()),
                ),
                patch.object(workflow, "_discover_attach_target", return_value=discovery),
                patch.object(
                    workflow, "_execute_kubernetes_traffic", return_value={"success": True}
                ),
                self.assertRaisesRegex(workflow.WorkflowError, "scale deployment/api"),
            ):
                workflow._assess_kubernetes_attach_config(
                    config,
                    config_path=root / "chamber.yaml",
                    agents_mode="off",
                    agents_exclude=(),
                    context=None,
                    prometheus_url=None,
                    runner=RecordingRunner(exit_status=1),
                    run_dir=run,
                )
            metadata = workflow._read_json(run / "run-metadata.json")
            self.assertFalse(metadata["success"])
            self.assertFalse(metadata["rollback"]["verified"])
            self.assertEqual(metadata["error"], "attach fault rollback could not be verified")
            self.assertTrue((run / "report.md").exists())

    def test_worker_discovery_rejects_malformed_and_unrelated_pods(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        items: list[Any] = [None, {"metadata": []}, {"metadata": {}, "status": []}]
        for name, labels, start, owners in (
            ("api", {}, now.isoformat(), []),
            ("checkout-worker-old", {}, "2025-01-01T00:00:00Z", [{"kind": "Job"}]),
            ("other-worker", {}, now.isoformat(), [{"kind": "Job"}]),
            ("checkout-worker-no-owner", {}, now.isoformat(), []),
            ("checkout-worker-invalid-start", {}, "invalid", [{"kind": "Job"}]),
        ):
            items.append(
                {
                    "metadata": {"name": name, "labels": labels, "ownerReferences": owners},
                    "status": {"startTime": start},
                }
            )
        items.append(
            {
                "metadata": {
                    "name": "checkout-worker-exact",
                    "labels": {
                        "scaledjob.keda.sh/name": "checkout-worker",
                        "task_id": "task-1",
                        "unsafe": "hidden",
                    },
                    "annotations": None,
                    "ownerReferences": None,
                },
                "status": {"startTime": now.isoformat(), "phase": "Succeeded"},
            }
        )
        with patch.object(workflow, "_kubectl_json", return_value={"items": items}):
            workers = workflow._discover_relayna_worker_pods(
                context="dev",
                namespace="sandbox",
                service_name="checkout-api",
                selected_pod_names=("api",),
                task_ids=("task-1",),
                traffic_started_at=now,
                runner=RecordingRunner(),
                commands=[],
            )
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]["correlation"], "task_label")
        self.assertEqual(workers[0]["task_ids"], ["task-1"])
        self.assertNotIn("unsafe", workers[0]["labels"])

    def test_pod_selector_discovery_skips_empty_selectors_and_bad_pod_rows(self) -> None:
        with patch.object(
            workflow,
            "_kubectl_json",
            return_value={
                "items": [
                    None,
                    {"metadata": {}},
                    {"metadata": {"name": "api"}, "status": {"phase": "Running"}},
                ]
            },
        ) as query:
            pods = workflow._discover_attach_pods(
                context="dev",
                namespace="sandbox",
                selectors=({}, {"app": "api"}),
                runner=RecordingRunner(),
                commands=[],
            )
        self.assertEqual(query.call_count, 1)
        self.assertEqual(pods[0]["name"], "api")
        self.assertEqual(pods[0]["phase"], "Running")

    def test_named_context_override_is_rejected_before_any_mutation(self) -> None:
        config = config_fixture(Path("/tmp"))
        config["runtime"] = {"kubernetesContext": "bound", "namespace": "sandbox"}
        config["chamber"] = {"context": "bound"}
        with self.assertRaisesRegex(workflow.WorkflowError, "cannot change a named chamber target"):
            workflow._assess_kubernetes_attach_config(
                config,
                config_path=Path("config.yaml"),
                agents_mode=None,
                agents_exclude=(),
                context="other",
                prometheus_url=None,
                runner=RecordingRunner(),
            )


class WorkflowEvidenceContextRegressions(unittest.TestCase):
    def test_agent_context_contains_each_available_runtime_source_and_discloses_failures(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "evidence").mkdir()
            workflow._write_json(
                run / "run-metadata.json",
                {
                    "stage": "failed",
                    "success": False,
                    "mode": "kubernetes",
                    "runtime_mode": "attach",
                    "cleanup_performed": False,
                    "traffic_result": {"success": False, "exit_status": 1},
                    "rollback": {"faults_requested": True, "verified": False},
                },
            )
            sources: dict[str, dict[str, Any]] = {
                "preflight": {"ready": False, "blockers": ["denied"], "checks": []},
                "kubernetes-commands": {
                    "commands": [
                        None,
                        {"exit_status": 1, "command": ["kubectl", "logs"], "stderr": "failed"},
                    ]
                },
                "attach-discovery": {"pods": [{"name": "api", "phase": "Running"}]},
                "rollback": {"verified": False},
                "relayna-summary": {"task_count": 3, "successful_count": 1, "failed_count": 2},
                "relayna-workers": {"workers": [None, {"correlation": "task_label"}]},
                "prometheus-memory": {
                    "queries": {
                        "invalid": None,
                        "memory": {"ok": False, "error": "unavailable", "series_count": None},
                    }
                },
                "local-assessment": {"observed_facts": ["local manifests read", ""]},
                "k6-summary": {
                    "metrics": {"http_reqs": {"count": 4}, "http_req_failed": {"value": 0.25}}
                },
            }
            for name, payload in sources.items():
                workflow._write_json(run / "evidence" / f"{name}.json", payload)
            workflow._write_json(
                run / "plan.json",
                {"workloads": [None], "readiness_checks": [None], "external_dependencies": [None]},
            )
            ids = ("plan", *sources)
            summaries = "\n".join(workflow._agent_evidence_summaries(run, ids))
            for expected in (
                "preflight blockers: 1",
                "runtime mode: attach",
                "rollback verified: False",
                "Relayna tasks: successful=1 total=3",
                "Relayna task failures: 2",
                "observed=2 exact_task_labels=1",
                "error=unavailable",
                "local manifests read",
                "derived failed http requests: 1.0",
            ):
                self.assertIn(expected, summaries)
            details = "\n".join(workflow._agent_evidence_details(run, ids))
            for source in (
                "plan",
                "preflight",
                "attach-discovery",
                "rollback",
                "relayna-summary",
                "relayna-workers",
                "local-assessment",
            ):
                self.assertIn(source + ":", details)
            self.assertEqual(workflow._agent_evidence_details(run, ("unavailable",)), ())
            self.assertEqual(
                workflow._agent_missing_signals(stage="plan", evidence_ids=()),
                ("live Kubernetes execution",),
            )
            workflow._write_json(run / "evidence/pre-test-state.json", {})
            ids = workflow._kubernetes_assess_evidence_ids({"summary_path": "k6.json"}, run_dir=run)
            self.assertIn("pre-test-state", ids)
            self.assertIn("rollback", ids)
            self.assertIn("k6-summary", ids)

    def test_missing_or_wrongly_typed_runtime_files_do_not_create_evidence(self) -> None:
        sources = (
            "plan",
            "preflight",
            "kubernetes-commands",
            "attach-discovery",
            "rollback",
            "k6-summary",
            "relayna-summary",
            "relayna-workers",
            "prometheus-memory",
            "local-assessment",
        )
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertEqual(workflow._agent_evidence_summaries(run, sources), ())
            self.assertEqual(workflow._agent_evidence_details(run, sources), ())
            (run / "evidence").mkdir()
            for source in sources:
                if source != "plan":
                    workflow._write_json(run / "evidence" / f"{source}.json", {})
            self.assertNotIn(
                "prometheus", " ".join(workflow._agent_evidence_summaries(run, sources))
            )
            workflow._write_json(
                run / "run-metadata.json", {"traffic_result": {}, "rollback": {}, "mode": "local"}
            )
            self.assertEqual(
                workflow._agent_evidence_summaries(run, ()),
                ("Live cleanup: not applicable to local inspection.",),
            )
            workflow._write_json(
                run / "evidence/k6-summary.json", {"metrics": {"checks": {"passes": 3, "fails": 1}}}
            )
            self.assertIn(
                "k6 checks: passes=3 fails=1",
                workflow._agent_evidence_summaries(run, ("k6-summary",)),
            )

    def test_metadata_retains_failure_cleanup_requirements_and_handles_corrupt_config(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            workflow._write_json(
                run / "execution-failure.json",
                {"cleanup_required": True, "error": "restoration failed"},
            )
            (run / "chamber.yaml").write_text("{invalid", encoding="utf-8")
            workflow._write_metadata(run, {"stage": "failed", "cleanup_performed": True})
            metadata = workflow._read_json(run / "run-metadata.json")
            self.assertFalse(metadata["cleanup_performed"])
            self.assertFalse(metadata["rollback"]["verified"])
            self.assertEqual(metadata["error"], "restoration failed")
            (run / "execution-failure.json").unlink()
            workflow.save_config(
                {"service": {"name": "api", "repo": str(run)}, "scenarioId": "legacy"},
                run / "chamber.yaml",
            )
            workflow._write_metadata(run, {"stage": "planned"})
            self.assertEqual(
                workflow._read_json(run / "run-metadata.json")["scenario"],
                {"id": "legacy", "source": "legacy", "revision": "unrecorded"},
            )

    def test_findings_join_only_registered_evidence_and_skip_malformed_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            workflow._write_json(
                run / "findings.json",
                [
                    None,
                    {
                        "finding_id": "f-1",
                        "evidence_ids": ["kubernetes-command-2", "unknown"],
                        "observed_facts": ["pod failed"],
                    },
                ],
            )
            with patch.object(
                workflow,
                "registered_evidence",
                return_value=[
                    {},
                    {
                        "evidence_id": "kubernetes-commands",
                        "relative_path": "evidence/kubernetes-commands.json",
                    },
                ],
            ):
                findings = workflow._persisted_report_findings(run)
            self.assertEqual(len(findings), 1)
            self.assertEqual(
                findings[0].evidence_links,
                (("kubernetes-command-2", "evidence/kubernetes-commands.json"),),
            )
            self.assertEqual(workflow._finding_ids(run), ("f-1",))

    def test_range_collection_handles_no_pods_and_corrects_inverted_window(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "evidence").mkdir()
            with (
                patch.object(workflow, "_prometheus_query", return_value={"ok": False}),
                patch.object(
                    workflow,
                    "_prometheus_query_range",
                    return_value={"ok": False, "error": "missing"},
                ) as query,
            ):
                workflow._collect_prometheus_memory_evidence(
                    run,
                    prometheus_url="http://metrics",
                    namespace="sandbox",
                    range_start=start,
                    range_end=start,
                )
            payload = workflow._read_json(run / "evidence/prometheus-memory.json")
            self.assertEqual(payload["window"]["end"], "2026-01-01T00:00:01+00:00")
            self.assertEqual(payload["summaries"], [])
            self.assertNotIn("pod=~", query.call_args.args[1])
            self.assertEqual(workflow._prometheus_workloads((), ({},)), [])

    def test_report_writer_live_call_preserves_structured_output_contract(self) -> None:
        from chamber.agents import ChamberAgentContext, ReportNarrative

        context = ChamberAgentContext(
            scenario_id="s",
            scenario_path="s.yaml",
            service_name="api",
            run_id="run",
            evidence_ids=("plan",),
            finding_ids=(),
            artifact_paths=(),
        )
        runner = Mock()
        runner.run_structured.return_value = ReportNarrative(
            summary="Supplied plan reviewed.",
            recommendations=(),
            limitations=("No live evidence",),
            evidence_ids=("plan",),
        )
        with patch.object(workflow, "OpenAIAgentsSdkRunner", return_value=runner):
            output = workflow._report_agent_output(context, "live")
        self.assertEqual(output.summary, "Supplied plan reviewed.")
        self.assertEqual(runner.run_structured.call_args.kwargs["output_type"], ReportNarrative)
        self.assertIn('"plan"', runner.run_structured.call_args.kwargs["input_text"])


class WorkflowExperimentPhaseRegressions(unittest.TestCase):
    def run_experiment(
        self, root: Path, *, recovery_fails: bool = False
    ) -> tuple[list[dict[str, Any]], Mock, Path]:
        from chamber.environment.preflight import KubernetesPreflightResult

        config = config_fixture(root)
        config["deployment"]["services"] = [{"name": "api", "port": 80}]
        config["runtime"] = {
            "provider": "kubernetes",
            "mode": "attach",
            "namespace": "sandbox",
            "kubernetesContext": "dev",
            "trafficAccess": {"mode": "endpoint", "url": "http://api"},
        }
        config["traffic"]["load"] = {"ratePerSecond": 2, "durationSeconds": 1, "recoveryRate": 1}
        config["experiment"] = {
            "family": "dependency_delay",
            "pod": "api-pod",
            "dependencyPod": "cache-pod",
            "container": "app",
            "durationSeconds": 10,
            "recoverySeconds": 5,
        }
        original = deepcopy(config)
        session = Mock(spec=workflow.ExperimentSession)
        session.spec = config["experiment"]
        session.evidence = {"restored": True}
        observed: list[dict[str, Any]] = []

        def traffic(**options: Any) -> dict[str, object]:
            execution_config = options["config"]
            observed.append(deepcopy(execution_config))
            directory = options["run_dir"]
            if execution_config["traffic"]["load"]["phase"] == "recovery" and recovery_fails:
                raise RuntimeError("recovery target unavailable")
            workflow._write_json(directory / "evidence/load-summary.json", {})
            return {"success": True, "evidence_id": "load-summary"}

        run = root / "experiment-run"
        with (
            patch.object(workflow, "init_workspace"),
            patch.object(
                workflow,
                "run_kubernetes_attach_preflight",
                return_value=KubernetesPreflightResult("dev", "sandbox", True, (), ()),
            ),
            patch.object(workflow, "_discover_attach_target", return_value={"pods": []}),
            patch.object(workflow, "_execute_kubernetes_traffic", side_effect=traffic),
            patch.object(workflow, "ExperimentSession", return_value=session),
        ):
            workflow._assess_kubernetes_attach_config(
                config,
                config_path=root / "chamber.yaml",
                agents_mode="off",
                agents_exclude=(),
                context=None,
                prometheus_url=None,
                runner=RecordingRunner(),
                run_dir=run,
            )
        self.assertEqual(config, original)
        return observed, session, run

    def test_dependency_exercise_preserves_baseline_fault_and_recovery_load_boundaries(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            observed, session, run = self.run_experiment(Path(tmp))
            loads = [config["traffic"]["load"] for config in observed]
            self.assertEqual([load["phase"] for load in loads], ["baseline", "fault", "recovery"])
            self.assertEqual(loads[0]["stages"], [{"ratePerSecond": 1, "durationSeconds": 5}])
            self.assertEqual(loads[2]["stages"], loads[0]["stages"])
            self.assertEqual(loads[2]["warmupSeconds"], 0)
            self.assertEqual(loads[2]["recoverySeconds"], 0)
            self.assertEqual(
                observed[2]["traffic"]["journeys"][0]["stages"],
                [{"duration": "5s", "targetVus": 1}],
            )
            session.start.assert_called_once()
            session.finish.assert_called_once()
            session.record_load.assert_called_once_with(True)
            session.assertion.assert_called_once_with(
                "Traffic succeeds after restoration", "pass", True, True
            )
            self.assertTrue((run / "evidence/baseline-load-summary.json").exists())
            self.assertTrue((run / "evidence/recovery-load-summary.json").exists())
            self.assertTrue(workflow._read_json(run / "run-metadata.json")["rollback"]["verified"])

    def test_recovery_traffic_failure_is_missing_evidence_after_verified_restoration(self) -> None:
        with TemporaryDirectory() as tmp:
            observed, session, run = self.run_experiment(Path(tmp), recovery_fails=True)
            self.assertEqual(len(observed), 3)
            session.assertion.assert_called_once_with(
                "Traffic succeeds after restoration", "missing", True, "recovery target unavailable"
            )
            self.assertTrue(workflow._read_json(run / "run-metadata.json")["rollback"]["verified"])
            self.assertFalse((run / "evidence/recovery-load-summary.json").exists())
            self.assertTrue((run / "report.md").exists())
