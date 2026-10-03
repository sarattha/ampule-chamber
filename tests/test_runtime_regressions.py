"""Failure-path regressions for evidence, run storage and external runtime boundaries."""

from __future__ import annotations

import copy
import io
import json
import runpy
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chamber.agents.contracts import AgentValidationError, EvidenceCitation, _collect_evidence_ids
from chamber.agents.sdk import OpenAIAgentsSdkRunner
from chamber.application import results, service
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.load import planning
from chamber.orchestrator import live
from chamber.report import cli, rendering
from chamber.runs import (
    initialize_run_record,
    new_run_directory,
    registered_evidence,
    store,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parents[1]


class RuntimeRegressionTests(unittest.TestCase):
    def test_invalid_traffic_contracts_have_precise_validation_failures(self) -> None:
        scenario = load_scenario(ROOT / "scenarios/baseline-health.yaml")
        cases = [
            ("traffic", "method", "DELETE", "traffic.method"),
            ("traffic", "expectedStatus", "200", "traffic.expectedStatus"),
            ("traffic", "stages", [], "traffic.stages"),
            ("traffic", "stages", [None], "expected mapping"),
            ("traffic", "stages", [{"duration": "5s", "targetVus": -1}], "targetVus"),
            ("safety", "maxVirtualUsers", [], "safety.maxVirtualUsers"),
        ]
        for section, field, value, reason in cases:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(scenario)
                changed.document[section][field] = value
                self.assertTrue(
                    any(reason in text for text in planning.validate_traffic_contract(changed))
                )
        for ports, reason in (([], "at least one service port"), ([None], "integer port")):
            changed = copy.deepcopy(scenario)
            changed.document["target"]["service"]["ports"] = ports
            self.assertTrue(
                any(reason in text for text in planning.validate_traffic_contract(changed))
            )
        changed = copy.deepcopy(scenario)
        changed.document["traffic"].update(method="POST", body={})
        self.assertTrue(
            any("JSON object" in text for text in planning.validate_traffic_contract(changed))
        )
        changed.document["traffic"] = None
        changed.document["safety"] = None
        self.assertTrue(
            any("expected mapping" in text for text in planning.validate_traffic_contract(changed))
        )
        failures: list[str] = []
        self.assertEqual(planning._max_virtual_users({"maxVirtualUsers": 2}, failures), 2)
        self.assertIsNone(planning._max_virtual_users(None, failures))
        for value in (None, "", "0s", "1.5s", "1d"):
            with self.assertRaises(planning.TrafficPlanningError):
                planning.parse_duration_seconds(value)

    def test_port_forward_probe_fails_closed_and_kills_unresponsive_process(self) -> None:
        scenario = load_scenario(ROOT / "scenarios/baseline-health.yaml")
        environment = plan_environment(scenario, run_id="probe").metadata
        plan = planning.plan_traffic(scenario, environment=environment, artifact_dir="/tmp")
        with patch.object(planning.shutil, "which", return_value=None):
            with self.assertRaisesRegex(planning.TrafficPlanningError, "kubectl executable"):
                planning._start_port_forward(plan)
        with self.assertRaisesRegex(planning.TrafficPlanningError, "HTTP"):
            planning._wait_for_target("file:///secret", Mock())
        process = Mock()
        process.poll.return_value = 1
        process.communicate.return_value = ("", "port occupied")
        with self.assertRaisesRegex(planning.TrafficPlanningError, "port occupied"):
            planning._wait_for_target("http://127.0.0.1:2000", process)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("kubectl", 5), 0]
        with patch.object(planning.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaisesRegex(planning.TrafficPlanningError, "timed out"):
                planning._wait_for_target("http://127.0.0.1:2000", process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_run_directory_collisions_and_unregistered_evidence_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(store.uuid, "uuid4", return_value=SimpleNamespace(hex="fixed")),
                patch.object(store, "datetime") as clock,
            ):
                clock.now.return_value.strftime.return_value = "20261003"
                new_run_directory(root, "a--b")
                with self.assertRaisesRegex(RuntimeError, "unique"):
                    new_run_directory(root, "a--b")
            run = root / "run"
            initialize_run_record(run)
            manifest = run / "evidence/manifest.json"
            for entries in (
                {},
                [None],
                [{"run_id": run.name, "relative_path": None}],
                [{"run_id": run.name, "relative_path": "../private"}],
            ):
                write_json_atomic(manifest, {"run_id": run.name, "entries": entries})
                self.assertEqual(registered_evidence(run), ())
            (root / "unrelated-file").write_text("ignore")
            store.RunIndex(root.parent).rebuild()
            self.assertEqual(store._dns_fragment("A--B ! C"), "a-b-c")
            for config, expected in (
                ([], "unknown"),
                ({}, "unknown"),
                ({"service": {"name": "orders"}}, "orders"),
            ):
                (run / "chamber.yaml").write_text(json.dumps(config))
                self.assertEqual(store._service_name(run), expected)
            with patch.object(Path, "read_text", side_effect=OSError("unreadable")):
                self.assertEqual(store._service_name(run), "unknown")
            write_json_atomic(run / "result.json", {"status": "failed"})
            self.assertEqual(store._result_status(run), "failed")
            write_json_atomic(run / "result.json", [])
            self.assertIsNone(store._result_status(run))
            (run / "run.json").unlink()
            write_json_atomic(run / "run-metadata.json", [])
            self.assertIsNone(store._run_summary(run))

    def test_agent_output_type_and_nested_citations_are_enforced(self) -> None:
        runner = OpenAIAgentsSdkRunner()
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic"}),
            patch("chamber.agents.sdk.Runner.run_sync") as execute,
        ):
            execute.return_value.final_output = {"unsafe": True}
            with self.assertRaisesRegex(AgentValidationError, "expected str"):
                runner.run_structured(
                    name="analysis",
                    instructions="cite evidence",
                    input_text="fixture",
                    output_type=str,
                )
            execute.return_value.final_output = "safe typed output"
            self.assertEqual(
                runner.run_structured(
                    name="analysis",
                    instructions="cite evidence",
                    input_text="fixture",
                    output_type=str,
                ),
                "safe typed output",
            )

        @dataclass
        class Citations:
            evidence_ids: tuple[str, ...]
            nested: object

        output = Citations(
            ("artifact-a",), {"nested": [EvidenceCitation("artifact-b", "fact"), None, 4]}
        )
        self.assertEqual(_collect_evidence_ids(output), {"artifact-a", "artifact-b"})
        self.assertEqual(_collect_evidence_ids("unstructured claims"), set())

    def test_invalid_prometheus_responses_and_live_errors_are_explicit(self) -> None:
        for response, reason in (
            (b"invalid", "invalid JSON"),
            (b"[]", "success"),
            (b'{"status":"error"}', "success"),
        ):
            with patch.object(live, "urlopen", return_value=io.BytesIO(response)):
                with self.assertRaisesRegex(live.LiveRunError, reason):
                    live._require_prometheus("http://metrics")
        with patch.object(live, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(live.LiveRunError, "not reachable"):
                live._require_prometheus("http://metrics")
        with self.assertRaisesRegex(live.LiveRunError, "HTTP"):
            live._require_http_url("file:///metrics")
        with self.assertRaisesRegex(live.LiveRunError, "no nodes"):
            live._verify_kind_image(
                "kind-cluster",
                "image",
                Mock(run=Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))),
            )
        with self.assertRaisesRegex(live.LiveRunError, "failed to inspect"):
            live._recordless_success(subprocess.CompletedProcess([], 1, "stdout", ""), "inspect")
        for signal, text in (
            ("memory_usage", "memory limits"),
            ("cpu_usage", "resource limits"),
            ("unknown", "recorded evidence"),
        ):
            self.assertIn(text, " ".join(live._recommendations(Mock(signal_type=signal))))
        scenario = load_scenario(ROOT / "scenarios/baseline-health.yaml")
        environment = plan_environment(scenario, run_id="only-target").metadata
        self.assertEqual(live._dependency_graph_lines(environment), ())
        scenario.document["faults"] = [{"type": "dependency_errors"}, {"type": "memory_pressure"}]
        scenario.document["environment"]["dependencies"] = []
        self.assertEqual(len(live._scenario_limitations(scenario)), 2)
        self.assertTrue(live._run_id("orders").startswith("chamber-orders-"))

    def test_report_fixture_fields_validate_types_and_cli_creates_output_directory(self) -> None:
        cases = [
            (rendering._mapping, None),
            (rendering._dict_list, [1]),
            (rendering._string_list, [1]),
            (rendering._list, None),
            (rendering._string, ""),
            (rendering._integer, "1"),
            (rendering._optional_boolean, "true"),
        ]
        for function, value in cases:
            with self.subTest(function=function.__name__), self.assertRaises(ValueError):
                function({"field": value}, "field")
        self.assertEqual(rendering._optional_string_list({}, "field"), [])
        self.assertIsNone(rendering._optional_string({}, "field"))
        self.assertIsNone(rendering._optional_integer({}, "field"))
        with (
            TemporaryDirectory() as tmp,
            patch.object(cli, "load_report_input", return_value=Mock()),
            patch.object(cli, "render_markdown_report", return_value="# Fixture\n"),
        ):
            output = Path(tmp) / "nested/report.md"
            with patch(
                "sys.argv", ["report", "--fixture", "fixture.json", "--output", str(output)]
            ):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(output.read_text(), "# Fixture\n")
        with (
            patch("sys.argv", ["report", "--fixture", "fixture.json"]),
            patch("chamber.report.load_report_input", return_value=Mock()),
            patch("chamber.report.render_markdown_report", return_value="# Fixture\n"),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            with self.assertRaises(SystemExit) as exit:
                runpy.run_module("chamber.report.cli", run_name="__main__")
            self.assertEqual(exit.exception.code, 0)
            self.assertIn("# Fixture", output.getvalue())

    def test_load_evidence_requires_consistent_delivery_and_assertions(self) -> None:
        metric = {
            "requested": 1,
            "started": 1,
            "completed": 1,
            "successful": 1,
            "dropped": 0,
            "p95Ms": 1,
            "p99Ms": 1,
            "errorRate": 0,
            "completionRate": 1,
            "deadlineMissRate": 0,
        }
        good = {
            "schema_version": "chamber.ampule.dev/load-suite/v1",
            "status": "pass",
            "metrics": metric,
            "windows": [
                {
                    "phase": "baseline",
                    "state": "pass",
                    "requested": 1,
                    "journeys": [
                        {"metrics": copy.deepcopy(metric), "assertions": [{"state": "pass"}]}
                    ],
                }
            ],
        }
        self.assertTrue(results._load_evidence_complete(good))
        mutations = [
            lambda d: d.update(schema_version="unknown"),
            lambda d: d["metrics"].update(completed=0),
            lambda d: d["metrics"].update(p95Ms=float("nan")),
            lambda d: d["metrics"].update(requested=2, started=2, completed=2, successful=2),
            lambda d: d["windows"][0].update(state="missing"),
            lambda d: d["windows"][0].update(requested=0),
            lambda d: d["windows"][0]["journeys"][0].update(assertions=[]),
            lambda d: d["windows"][0]["journeys"][0].update(assertions=[{"state": "fail"}]),
            lambda d: d["windows"][0].update(state="fail"),
        ]
        for mutate in mutations:
            changed = copy.deepcopy(good)
            mutate(changed)
            self.assertFalse(results._load_evidence_complete(changed), changed)
        self.assertEqual(results._confidence_label(True, 100, ({"confidence": "low"},)), "moderate")

    def test_runtime_signal_artifacts_require_successful_well_formed_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            path = run / "evidence/kubernetes-commands.json"
            for signal, command, stdout in (
                ("logs", ["logs"], "known safe log"),
                ("pod_status", ["get", "pods"], '{"items":[{}]}'),
                ("kubernetes_events", ["get", "events"], '{"items":[]}'),
            ):
                write_json_atomic(
                    path,
                    {
                        "commands": [
                            {"exit_status": 1, "command": command, "stdout": stdout},
                            {"exit_status": 0, "command": command, "stdout": stdout},
                        ]
                    },
                )
                self.assertTrue(results._runtime_signal_present(run, signal))
            write_json_atomic(
                path,
                {
                    "commands": [
                        {"exit_status": 0, "command": ["get", "pods"], "stdout": "not json"}
                    ]
                },
            )
            self.assertFalse(results._runtime_signal_present(run, "pod_status"))
            write_json_atomic(
                path,
                {
                    "commands": [
                        None,
                        {"exit_status": 0, "command": None, "stdout": ""},
                        {
                            "exit_status": 0,
                            "command": ["get", "pod", "pod/orders", "json"],
                            "stdout": '{"metadata":{"name":"orders"}}',
                        },
                    ]
                },
            )
            artifacts = results._command_evidence(run, run_id="run", scenario_id="scenario")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].resource, "pod/orders")
            self.assertIn("items", artifacts[0].payload)
            path.write_text("invalid")
            self.assertEqual(
                results._command_evidence(run, run_id="run", scenario_id="scenario"), ()
            )
            text = run / "logs.txt"
            text.write_text("safe log")
            self.assertTrue(results._has_evidence_content(text, "logs"))

    def test_workspace_filters_do_not_accept_mismatched_or_unassigned_runs(self) -> None:
        row = {
            "service_name": "orders",
            "state": "completed",
            "status": "ready",
            "environment": "dev",
            "evidence_coverage_percent": 50,
            "fault_types": ["pod_kill"],
            "created_at": "2026-10-03",
            "owner": "unassigned",
        }
        defaults = dict(
            search="",
            state="",
            outcome="",
            environment="",
            coverage="",
            fault="",
            date_from="",
            date_to="",
            view="",
        )
        self.assertTrue(service._run_matches(row, **defaults))
        mismatches = [
            ("state", "running"),
            ("outcome", "failed"),
            ("environment", "prod"),
            ("coverage", "complete"),
            ("coverage", "missing"),
            ("fault", "cpu_pressure"),
            ("date_from", "2026-10-04"),
            ("date_to", "2026-10-02"),
            ("view", "needs_attention"),
            ("view", "my_services"),
        ]
        for key, value in mismatches:
            self.assertFalse(service._run_matches(row, **{**defaults, key: value}))
        row["evidence_coverage_percent"] = 100
        self.assertFalse(service._run_matches(row, **{**defaults, "coverage": "partial"}))
        for invalid in (float("nan"), float("inf"), "invalid"):
            self.assertIsNone(service._number(invalid))
        self.assertEqual(
            service._service_name({"config": {"service": {"name": "orders"}}}), "orders"
        )
        self.assertEqual(service._finding_ids(None), set())
        self.assertEqual(service._finding_views("run", None, ()), [])
        self.assertEqual(service._finding_views("run", [None], ()), [])
