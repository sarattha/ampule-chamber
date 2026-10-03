"""Regression tests for corrupted evidence and decision projection contracts."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from chamber.application import evidence, results, service
from chamber.application.service import ChamberApplication
from chamber.runs import write_json_atomic
from tests.test_decision_results import _config, _metadata, _write_required_evidence


class EvidenceRegressionTests(unittest.TestCase):
    def test_malformed_artifacts_do_not_become_valid_runtime_events(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            source = {"relative_path": "bad.json"}
            write_json_atomic(run / "bad.json", [])
            for projection in (
                evidence._relayna_events,
                evidence._worker_events,
                evidence._kubernetes_events,
                evidence._rollback_events,
                evidence._prometheus_events,
            ):
                self.assertEqual(projection(run, source), [])
            write_json_atomic(run / "bad.json", {})
            self.assertEqual(evidence._prometheus_events(run, source), [])
            self.assertEqual(evidence._owner_name({"ownerReferences": [None, {}]}), None)
            self.assertEqual(evidence._metric_value({"values": {"p95": "NaN"}}, "p95"), None)
            self.assertIsNone(evidence._datetime(float("inf")))
            epoch = evidence._datetime(0)
            assert epoch is not None
            self.assertEqual(epoch.year, 1970)
            self.assertEqual(evidence._bounded_categories([], 10), [])
            self.assertEqual(evidence._bounded_categories([[{}]], 0), [])
            self.assertEqual(
                evidence._finding_evidence_ids({"evidence_ids": ["run:k6-summary:1"]}),
                {"k6-summary"},
            )

    def test_prometheus_projection_skips_invalid_samples_and_preserves_valid_memory(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            source = {"relative_path": "metrics.json"}
            series = [
                None,
                {"metric": {}},
                {"metric": {"pod": "orders"}, "values": [[], None, [0, "NaN"], [1, "12"]]},
            ]
            write_json_atomic(
                run / "metrics.json",
                {"range_queries": {"container_memory_working_set_bytes": {"series": series}}},
            )
            events = evidence._prometheus_events(run, source)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["pod"], "orders")
            self.assertEqual(events[0]["value"], 12)
            write_json_atomic(
                run / "metrics.json",
                {
                    "summaries": [
                        {"pod_name": "orders", "peak_memory_bytes": None, "peak_cpu_cores": 0.5}
                    ]
                },
            )
            self.assertEqual(
                [item["signal"] for item in evidence._prometheus_events(run, source)], ["cpu"]
            )
            query = {
                "series": [
                    None,
                    {},
                    {"metric": {"pod": "orders"}, "value": [1, "NaN"]},
                    {"metric": {"pod": "orders"}, "value": [2, "12"]},
                ]
            }
            self.assertEqual(service._instant_query_totals(query), {"orders": 12})
            views = service._prometheus_query_views({"queries": {"bad": None, "memory": query}})
            self.assertEqual(len(views), 1)
            self.assertEqual(len(views[0]["samples"]), 3)

    def test_safe_structured_log_projection_never_expands_beyond_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            lines = ["2026-10-03T01:00:00Z INFO safe_event task_id=task-1"] * 150
            write_json_atomic(
                run / "logs.json",
                {
                    "commands": [
                        {"command": ["kubectl", "logs", "pod/orders"], "stdout": "\n".join(lines)}
                    ]
                },
            )
            events = evidence._kubernetes_events(run, {"relative_path": "logs.json"})
            self.assertEqual(len(events), evidence.MAX_SAFE_LOG_EVENTS)
            self.assertTrue(all(item["signal"] == "log_event" for item in events))

    def test_corrupt_yaml_and_jsonl_are_isolated_from_run_views(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "absent.yaml"
            self.assertEqual(service._yaml_or_default(missing), {})
            bad = root / "bad.yaml"
            bad.write_text("[unterminated")
            self.assertEqual(service._yaml_or_default(bad), {})
            events = root / "events.jsonl"
            events.write_text('bad JSON\n[]\n{"state":"completed"}\n')
            self.assertEqual(service._events(events), ({"state": "completed"},))
            tasks = root / "tasks.json"
            write_json_atomic(
                tasks, {"tasks": [None, {"task_id": "exact", "statuses": ["queued", "completed"]}]}
            )
            view = service._relayna_view(tasks)
            self.assertEqual(len(view["tasks"]), 1)
            self.assertEqual(len(view["tasks"][0]["events"]), 2)

    def test_application_assessment_forwards_explicit_execution_options(self) -> None:
        with TemporaryDirectory() as tmp:
            app = ChamberApplication(Path(tmp))
            output = Path(tmp) / "run"
            with patch("chamber.workflow.assess", return_value=output) as execute:
                self.assertEqual(
                    app.assess(
                        config=Path("chamber.yaml"),
                        run_dir=output,
                        mode="kubernetes",
                        agents_mode="offline",
                        agents_exclude=("analyst",),
                        context="dev",
                        prometheus_url="http://metrics",
                    ),
                    output,
                )
                self.assertEqual(execute.call_args.kwargs["context"], "dev")
                self.assertEqual(execute.call_args.kwargs["agents_exclude"], ("analyst",))
            with patch("chamber.workflow.plan_config", return_value=output) as planning:
                bad = Path(tmp) / "invalid.yaml"
                bad.write_text("[invalid")
                app.plan(bad)
                self.assertEqual(planning.call_args.args[0], bad)
            with self.assertRaisesRegex(ValueError, "explicit Kubernetes target"):
                with patch.object(
                    app,
                    "get_run",
                    return_value={
                        "result": {"status": "failed"},
                        "config": {"runtime": {"provider": "local"}},
                    },
                ):
                    app.plan_rerun("source")

    def test_finding_comparison_distinguishes_added_resolved_and_severity_changes(self) -> None:
        baseline = {
            "findings": [
                {"finding_id": "resolved", "severity": "high"},
                {"finding_id": "same", "severity": "low"},
                {"finding_id": "regression", "severity": "low"},
            ]
        }
        candidate = {
            "findings": [
                {"finding_id": "added", "severity": "high"},
                {"finding_id": "same", "severity": "low"},
                {"finding_id": "regression", "severity": "high"},
            ]
        }
        changes = {
            item["finding_id"]: item["change"]
            for item in service._finding_changes(baseline, candidate)
        }
        self.assertEqual(
            changes, {"added": "added", "resolved": "resolved", "regression": "worsened"}
        )
        self.assertEqual(
            service._public_evidence_id("run:k6-summary:1", {"k6-summary": {}}), "k6-summary"
        )

    def test_complete_evidence_scores_critical_and_high_findings_honestly(self) -> None:
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            _write_required_evidence(run)
            metadata = _metadata(run.name)
            for severity, expected in (("critical", "not_ready"), ("high", "conditional")):
                findings = (
                    {
                        "finding_id": "fault",
                        "severity": severity,
                        "confidence": "high",
                        "signal_type": "error_rate",
                    },
                )
                result = results.build_assessment_result(
                    run, config=_config(), metadata=metadata, findings=findings
                )
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["conclusive"])
            metadata["traffic_result"]["success"] = False
            failed = results.build_assessment_result(
                run, config=_config(), metadata=metadata, findings=()
            )
            self.assertEqual(failed["status"], "failed")
            self.assertFalse(failed["conclusive"])
