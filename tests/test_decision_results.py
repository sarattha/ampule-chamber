from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from chamber.application.results import build_assessment_result
from chamber.application.service import ChamberApplication
from chamber.control_plane.server import create_app
from chamber.report import (
    ReportFinding,
    ReportInput,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)
from chamber.runs import initialize_run_record, refresh_evidence_manifest, write_json_atomic
from tests.evidence_fixtures import evidence_payload

STATE_FIXTURES = (
    ("ready", {"stage": "assessed", "mode": "kubernetes", "success": True}, "ready"),
    (
        "inconclusive",
        {"stage": "assessed", "mode": "kubernetes", "success": True},
        "inconclusive",
    ),
    ("failed", {"stage": "failed", "mode": "kubernetes", "error": "traffic failed"}, "failed"),
    ("cancelled", {"stage": "cancelled", "mode": "kubernetes"}, "cancelled"),
    ("local", {"stage": "assessed", "mode": "local", "success": True}, "local_only"),
    (
        "preflight",
        {"stage": "preflight_failed", "mode": "kubernetes", "error": "access denied"},
        "preflight_failed",
    ),
)


class DecisionResultProjectionTests(unittest.TestCase):
    def test_ready_inconclusive_failed_cancelled_local_and_preflight_fixtures(self) -> None:
        with TemporaryDirectory() as tmp:
            headlines = set()
            first_actions = set()
            for fixture_name, metadata_values, expected in STATE_FIXTURES:
                with self.subTest(fixture=fixture_name):
                    run_dir = Path(tmp) / fixture_name
                    run_dir.mkdir()
                    if fixture_name in {"ready", "failed", "cancelled"}:
                        _write_required_evidence(run_dir)
                    metadata = {
                        "run_id": fixture_name,
                        "traffic_result": {"success": True},
                        "cleanup_performed": True,
                        "rollback": {"verified": True},
                        **metadata_values,
                    }
                    result = build_assessment_result(
                        run_dir,
                        config=_config(),
                        metadata=metadata,
                        findings=(),
                    )
                    self.assertEqual(result["status"], expected)
                    headlines.add(result["verdict"]["headline"])
                    first_actions.add(result["next_actions"][0]["title"])
                    if expected == "ready":
                        self.assertEqual(result["readiness_score"], 100)
                        self.assertTrue(result["conclusive"])
                    else:
                        self.assertIsNone(result["readiness_score"])
                        self.assertFalse(result["conclusive"])
            self.assertEqual(len(headlines), len(STATE_FIXTURES))
            self.assertGreaterEqual(len(first_actions), 5)

    def test_required_present_and_missing_evidence_explain_and_link_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "prometheus-gap"
            _write_required_evidence(run_dir)
            prometheus = run_dir / "evidence/prometheus-memory.json"
            prometheus.write_text(
                json.dumps(
                    {
                        "queries": {
                            "container_memory_working_set_bytes": {
                                "ok": False,
                                "error": "connection refused",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            refresh_evidence_manifest(run_dir)
            config = _config()
            config["runtime"]["prometheusUrl"] = "http://prometheus.invalid:9090"
            result = build_assessment_result(
                run_dir,
                config=config,
                metadata=_metadata(run_dir.name),
                findings=(),
            )

            requirements = result["evidence_requirements"]
            self.assertEqual(len(requirements["required"]), 4)
            self.assertEqual(len(requirements["present"]), 3)
            self.assertEqual(len(requirements["missing"]), 1)
            gap = requirements["missing"][0]
            self.assertEqual(gap["name"], "Prometheus workload telemetry")
            self.assertTrue(gap["impact"])
            self.assertTrue(gap["likely_cause"])
            self.assertTrue(gap["resolution"])
            self.assertEqual(gap["configuration_context"]["path"], "runtime.prometheusUrl")
            self.assertIn("tab=configuration", gap["configuration_context"]["url"])
            self.assertIn("/evidence/prometheus-memory", gap["evidence_url"])
            self.assertIn("connection refused", " ".join(gap["limitations"]))
            self.assertIsNone(result["readiness_score"])

    def test_goal_required_cpu_or_memory_signals_gate_on_prometheus_evidence(self) -> None:
        for signal in ("cpu_usage", "memory_usage"):
            with self.subTest(signal=signal), TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / signal
                _write_required_evidence(run_dir)
                config = _config()
                config["scenario"]["requiredSignals"] = [signal]

                result = build_assessment_result(
                    run_dir,
                    config=config,
                    metadata=_metadata(run_dir.name),
                    findings=(),
                )

                self.assertEqual(result["status"], "inconclusive")
                self.assertIn("prometheus-memory", result["required_evidence_ids"])
                self.assertIn("prometheus-memory", result["missing_evidence_ids"])
                self.assertIsNone(result["readiness_score"])

    def test_findings_link_to_registered_supporting_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            run_dir = workspace / "runs" / "linked-finding"
            evidence = run_dir / "evidence"
            evidence.mkdir(parents=True)
            (evidence / "kubernetes-commands.json").write_text(
                json.dumps(evidence_payload("kubernetes-commands")), encoding="utf-8"
            )
            refresh_evidence_manifest(run_dir)
            write_json_atomic(
                run_dir / "findings.json",
                [
                    {
                        "finding_id": "finding-1",
                        "signal_type": "restart_loop",
                        "evidence_ids": ["kubernetes-command-1"],
                    }
                ],
            )
            write_json_atomic(run_dir / "run.json", {"run_id": run_dir.name, "state": "completed"})

            finding = ChamberApplication(workspace).get_run(run_dir.name)["findings"][0]
            self.assertEqual(
                finding["evidence_links"][0]["registered_evidence_id"], "kubernetes-commands"
            )
            self.assertEqual(
                finding["evidence_links"][0]["url"],
                f"/api/v1/runs/{run_dir.name}/evidence/kubernetes-commands",
            )


class DecisionRerunTests(unittest.TestCase):
    def test_fix_setup_preserves_target_revision_journeys_safety_and_faults(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            source = workspace / "runs" / "source"
            source.mkdir(parents=True)
            config = _config()
            config["runtime"]["faults"] = [{"type": "pod_kill", "recoverySeconds": 30}]
            config["runtime"]["prometheusUrl"] = "http://prometheus.old:9090"
            config["safety"] = {"maxVirtualUsers": 4, "maxDurationSeconds": 60}
            (source / "chamber.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            write_json_atomic(source / "result.json", {"status": "inconclusive"})
            write_json_atomic(source / "run.json", {"run_id": source.name, "state": "completed"})
            planned = workspace / "runs" / "planned-rerun"

            def fake_plan(path: Path, *, run_dir: Path | None = None) -> Path:
                del run_dir
                planned.mkdir(parents=True)
                (planned / "chamber.yaml").write_text(
                    path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                initialize_run_record(planned)
                return planned

            application = ChamberApplication(workspace)
            with patch.object(application, "plan", side_effect=fake_plan):
                result = application.plan_rerun(
                    source.name,
                    kubernetes_context="kind-recovered",
                    prometheus_url="http://prometheus.monitoring:9090",
                )

            rerun = yaml.safe_load((result / "chamber.yaml").read_text(encoding="utf-8"))
            for key in ("service", "scenario", "traffic", "safety"):
                self.assertEqual(rerun[key], config[key])
            self.assertEqual(rerun["runtime"]["faults"], config["runtime"]["faults"])
            self.assertEqual(rerun["runtime"]["kubernetesContext"], "kind-recovered")
            self.assertEqual(rerun["runtime"]["prometheusUrl"], "http://prometheus.monitoring:9090")
            record = json.loads((result / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(record["parent_run_id"], source.name)
            self.assertEqual(record["rerun"]["kind"], "fix_setup")

            preserved = workspace / "runs" / "preserved-rerun"

            def preserve_plan(path: Path, *, run_dir: Path | None = None) -> Path:
                del run_dir
                preserved.mkdir(parents=True)
                (preserved / "chamber.yaml").write_text(
                    path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                initialize_run_record(preserved)
                return preserved

            with patch.object(application, "plan", side_effect=preserve_plan):
                application.plan_rerun(source.name, prometheus_url="")
            preserved_config = yaml.safe_load(
                (preserved / "chamber.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(
                preserved_config["runtime"]["prometheusUrl"],
                config["runtime"]["prometheusUrl"],
            )

    def test_fix_setup_refuses_local_only_auto_promotion(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            source = workspace / "runs" / "local"
            source.mkdir(parents=True)
            config = _config()
            config["runtime"]["provider"] = "local"
            (source / "chamber.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
            write_json_atomic(source / "result.json", {"status": "local_only"})
            write_json_atomic(source / "run.json", {"run_id": source.name, "state": "completed"})
            with self.assertRaisesRegex(ValueError, "terminal Kubernetes"):
                ChamberApplication(workspace).plan_rerun(source.name)


class DecisionSurfaceConsistencyTests(unittest.TestCase):
    def test_overview_api_and_json_report_share_the_result_projection(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            run_dir = workspace / "runs" / "surface"
            run_dir.mkdir(parents=True)
            (run_dir / "chamber.yaml").write_text(yaml.safe_dump(_config()), encoding="utf-8")
            write_json_atomic(run_dir / "run.json", {"run_id": run_dir.name, "state": "completed"})
            write_json_atomic(run_dir / "run-metadata.json", _metadata(run_dir.name))
            result = build_assessment_result(
                run_dir,
                config=_config(),
                metadata=_metadata(run_dir.name),
                findings=(),
            )
            write_json_atomic(run_dir / "result.json", result)
            write_json_atomic(run_dir / "findings.json", [])
            report_path = run_dir / "report.md"
            report_path.write_text("# Report\n", encoding="utf-8")
            app = create_app(workspace)
            client = TestClient(app)

            with patch.object(app.state.chamber, "report", return_value=report_path):
                api_result = client.get(f"/api/v1/runs/{run_dir.name}").json()["result"]
                export_result = client.get(
                    f"/api/v1/runs/{run_dir.name}/report?format=json"
                ).json()["result"]
            overview = client.get(f"/runs/{run_dir.name}")
            self.assertEqual(api_result, export_result)
            self.assertIn(result["verdict"]["headline"], overview.text)
            self.assertIn("Operational verdict", overview.text)
            self.assertIn("Fix setup and rerun", overview.text)
            self.assertIn("Open runtime.kubernetesContext", overview.text)

            csrf = overview.cookies.get("ampule_csrf") or client.cookies.get("ampule_csrf")
            planned = workspace / "runs" / "api-rerun"
            planned.mkdir()
            with patch.object(app.state.chamber, "plan_rerun", return_value=planned):
                api_rerun = client.post(
                    f"/api/v1/runs/{run_dir.name}/rerun",
                    json={"kubernetes_context": "kind-fixed", "prometheus_url": ""},
                    headers={"X-CSRF-Token": str(csrf)},
                )
                ui_rerun = client.post(
                    f"/ui/runs/{run_dir.name}/fix-and-rerun",
                    data={
                        "_csrf": csrf,
                        "kubernetes_context": "kind-fixed",
                        "prometheus_url": "",
                    },
                    follow_redirects=False,
                )
            self.assertEqual(api_rerun.status_code, 201)
            self.assertEqual(api_rerun.json()["run_id"], planned.name)
            self.assertEqual(ui_rerun.status_code, 303)
            self.assertEqual(
                ui_rerun.headers["location"], f"/runs/{planned.name}?tab=configuration"
            )

    def test_markdown_report_projects_verdict_gaps_actions_and_evidence_links(self) -> None:
        result = _projected_gap_result()
        report = ReportInput(
            title="Decision report",
            service=ServiceMetadata("service", "team", "repo", "commit"),
            run=RunMetadata("run", "2026-08-08", 10, "ns", "kind", "completed"),
            scenario=TestedScenario("scenario", "journey", "config", "k6", 2, "none"),
            findings=(
                ReportFinding(
                    "finding-1",
                    "restart_loop",
                    "pod/service",
                    ("Pod restarted.",),
                    "Startup failed.",
                    "high",
                    "high",
                    ("kubernetes-command-1",),
                    (),
                    ("Fix startup.",),
                    (("kubernetes-command-1", "evidence/kubernetes-commands.json"),),
                ),
            ),
            evidence=(),
            reproduction=ReproductionDetails((), ()),
            retest_plan=(),
            cleanup_notes=(),
            limitations=(),
            assessment_status=str(result["status"]),
            readiness_score=None,
            verdict=result["verdict"],
            evidence_gaps=tuple(result["evidence_requirements"]["missing"]),
            next_actions=tuple(result["next_actions"]),
        )
        markdown = render_markdown_report(report)
        self.assertIn("## Operational Verdict", markdown)
        self.assertIn(result["verdict"]["why"], markdown)
        self.assertIn("## Missing Evidence", markdown)
        self.assertIn("`runtime.prometheusUrl`", markdown)
        self.assertIn("## Prioritized Next Actions", markdown)
        self.assertIn("Score: N/A", markdown)
        self.assertIn(
            "[kubernetes-command-1](evidence/kubernetes-commands.json)",
            markdown,
        )

    def test_existing_result_v1_artifact_remains_readable_without_new_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            run_dir = workspace / "runs" / "legacy"
            run_dir.mkdir(parents=True)
            legacy = {
                "schema_version": "chamber.ampule.dev/result/v1",
                "run_id": run_dir.name,
                "status": "inconclusive",
                "conclusive": False,
                "readiness_score": None,
                "missing_evidence_ids": ["k6-summary"],
            }
            write_json_atomic(run_dir / "run.json", {"run_id": run_dir.name, "state": "completed"})
            write_json_atomic(run_dir / "result.json", legacy)
            loaded = ChamberApplication(workspace).get_run(run_dir.name)
            self.assertEqual(loaded["result"], legacy)
            self.assertEqual(loaded["findings"], [])


def _config() -> dict[str, Any]:
    return {
        "apiVersion": "chamber.ampule.dev/v1alpha1",
        "kind": "ChamberConfig",
        "service": {"name": "payments", "repo": "/workspace/payments"},
        "scenario": {"id": "payments-resilience", "revision": "revision-32"},
        "traffic": {
            "journeys": [
                {
                    "name": "health",
                    "method": "GET",
                    "path": "/health",
                    "expectedStatus": 200,
                    "vus": 2,
                    "iterations": 4,
                }
            ]
        },
        "runtime": {
            "provider": "kubernetes",
            "mode": "deploy",
            "kubernetesContext": "kind-ampule",
            "namespace": "payments-test",
            "faults": [],
        },
    }


def _metadata(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": "assessed",
        "mode": "kubernetes",
        "success": True,
        "traffic_result": {"success": True},
        "cleanup_performed": True,
        "rollback": {"verified": True},
    }


def _write_required_evidence(run_dir: Path) -> None:
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("preflight", "kubernetes-commands", "k6-summary"):
        (evidence / f"{name}.json").write_text(json.dumps(evidence_payload(name)), encoding="utf-8")
    refresh_evidence_manifest(run_dir)


def _projected_gap_result() -> dict[str, Any]:
    with TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "gap"
        run_dir.mkdir()
        config = _config()
        config["runtime"]["prometheusUrl"] = "http://prometheus.invalid:9090"
        return build_assessment_result(
            run_dir,
            config=config,
            metadata=_metadata(run_dir.name),
            findings=(),
        )
