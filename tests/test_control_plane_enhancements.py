from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from chamber.application.service import ChamberApplication
from chamber.control_plane.server import create_app
from chamber.runs import initialize_run_record, refresh_evidence_manifest, write_json_atomic


class RunWorkspaceProjectionTests(unittest.TestCase):
    def test_search_filters_pagination_regressions_tags_and_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            first = _run(
                workspace,
                "payments-old",
                created_at="2026-08-01T00:00:00+00:00",
                score=95,
                coverage=100,
                tags=("release",),
            )
            second = _run(
                workspace,
                "payments-new",
                created_at="2026-08-02T00:00:00+00:00",
                score=70,
                coverage=75,
                findings=2,
                fault="pod_kill",
            )
            _run(
                workspace,
                "catalog-run",
                service="catalog",
                created_at="2026-08-03T00:00:00+00:00",
                score=None,
                coverage=0,
                outcome="inconclusive",
            )
            application = ChamberApplication(workspace)

            regressions = application.query_runs(view="recent_regressions")
            self.assertEqual([item["run_id"] for item in regressions["runs"]], [second.name])
            self.assertEqual(regressions["summary"]["regressed"], 1)

            searched = application.query_runs(
                search="payments-v2",
                environment="kubernetes",
                coverage="partial",
                fault="pod_kill",
            )
            self.assertEqual([item["run_id"] for item in searched["runs"]], [second.name])
            self.assertEqual(searched["pagination"]["total_items"], 1)

            application.set_run_tags(first.name, tags=("baseline", "team-payments"))
            self.assertEqual(
                application.query_runs(search="team-payments")["runs"][0]["run_id"],
                first.name,
            )
            application.set_run_archived(first.name, archived=True)
            self.assertFalse(application.query_runs(search=first.name)["runs"])
            self.assertEqual(
                application.query_runs(search=first.name, include_archived=True)["runs"][0][
                    "run_id"
                ],
                first.name,
            )

    def test_runs_html_and_api_share_url_backed_filters_and_states(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            _run(
                workspace,
                "partial",
                created_at="2026-08-02T00:00:00+00:00",
                score=None,
                coverage=50,
                outcome="inconclusive",
            )
            client = TestClient(create_app(workspace))

            page = client.get("/runs?outcome=inconclusive&coverage=partial")
            api = client.get("/api/v1/runs?outcome=inconclusive&coverage=partial").json()

            self.assertEqual(page.status_code, 200)
            self.assertIn("Operational reliability", page.text)
            self.assertIn("Needs attention", page.text)
            self.assertIn("My services", page.text)
            self.assertIn("No runs match these filters", client.get("/runs?q=missing").text)
            self.assertEqual(api["pagination"]["total_items"], 1)
            self.assertEqual(api["filters"]["outcome"], "inconclusive")

    def test_legacy_limit_can_return_more_than_the_interactive_page_cap(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / ".chamber")
            rows = tuple(
                {
                    "run_id": f"run-{index:03d}",
                    "run_dir": f"/workspace/run-{index:03d}",
                    "service_name": "payments",
                    "state": "completed",
                    "status": "ready",
                    "readiness_score": 90,
                    "evidence_coverage_percent": 100,
                    "cleanup_verified": True,
                    "finding_count": 0,
                    "environment": "kubernetes",
                    "fault_types": (),
                    "archived": False,
                    "created_at": f"2026-08-08T00:{index % 60:02d}:00+00:00",
                    "tags": (),
                }
                for index in range(150)
            )
            with patch.object(app.state.chamber, "list_runs", return_value=rows):
                response = TestClient(app).get("/api/v1/runs?limit=150")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["runs"]), 150)
            self.assertEqual(response.json()["pagination"]["page_size"], 150)


class DetailedComparisonTests(unittest.TestCase):
    def test_recommended_baselines_are_compatible_and_older_than_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            older = _run(
                workspace,
                "older",
                created_at="2026-08-01T00:00:00+00:00",
                score=90,
                coverage=100,
            )
            candidate = _run(
                workspace,
                "candidate",
                created_at="2026-08-02T00:00:00+00:00",
                score=92,
                coverage=100,
            )
            newer = _run(
                workspace,
                "newer",
                created_at="2026-08-03T00:00:00+00:00",
                score=94,
                coverage=100,
            )

            page = TestClient(create_app(workspace)).get(f"/compare?candidate={candidate.name}")

            self.assertIn(f'value="{older.name}"   data-recommended="true"', page.text)
            self.assertNotIn(f'value="{newer.name}"   data-recommended="true"', page.text)

    def test_compare_selector_includes_archived_runs_and_requests_full_index(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            candidate = _run(
                workspace,
                "archived-candidate",
                created_at="2026-08-02T00:00:00+00:00",
                score=92,
                coverage=100,
            )
            app = create_app(workspace)
            app.state.chamber.set_run_archived(candidate.name, archived=True)
            with patch.object(
                app.state.chamber,
                "query_runs",
                wraps=app.state.chamber.query_runs,
            ) as query_runs:
                page = TestClient(app).get(f"/compare?candidate={candidate.name}")

            self.assertEqual(page.status_code, 200)
            self.assertIn(f'value="{candidate.name}"', page.text)
            query_runs.assert_called_once_with(
                page_size=1000,
                include_archived=True,
                max_page_size=1000,
            )

    def test_unknown_dimensions_are_incompatible_and_not_recommended(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            baseline = _run(
                workspace,
                "legacy-baseline",
                created_at="2026-08-01T00:00:00+00:00",
                score=90,
                coverage=100,
            )
            candidate = _run(
                workspace,
                "legacy-candidate",
                created_at="2026-08-02T00:00:00+00:00",
                score=92,
                coverage=100,
            )
            for run_dir in (baseline, candidate):
                config = yaml.safe_load((run_dir / "chamber.yaml").read_text(encoding="utf-8"))
                config["scenario"].pop("revision")
                (run_dir / "chamber.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

            comparison = ChamberApplication(workspace).compare(baseline.name, candidate.name)
            page = TestClient(create_app(workspace)).get(f"/compare?candidate={candidate.name}")

            self.assertFalse(comparison.compatible)
            self.assertTrue(
                any(
                    item["dimension"] == "scenario revision" and "unavailable" in item["detail"]
                    for item in comparison.compatibility_reasons
                )
            )
            self.assertNotIn(f'value="{baseline.name}"   data-recommended="true"', page.text)

    def test_compatible_runs_include_signal_finding_and_config_deltas(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            baseline = _run(
                workspace,
                "baseline",
                created_at="2026-08-01T00:00:00+00:00",
                score=90,
                coverage=100,
                latency=220,
                failure_rate=0.02,
                restarts=2,
                finding_severity="high",
            )
            candidate = _run(
                workspace,
                "candidate",
                created_at="2026-08-02T00:00:00+00:00",
                score=95,
                coverage=100,
                latency=180,
                failure_rate=0.01,
                restarts=0,
                finding_severity="medium",
                fault="pod_kill",
            )

            comparison = ChamberApplication(workspace).compare(baseline.name, candidate.name)

            self.assertTrue(comparison.compatible)
            latency = next(
                item for item in comparison.signal_deltas if item["key"] == "latency_p95_ms"
            )
            self.assertEqual(latency["delta"], -40)
            self.assertEqual(latency["direction"], "improved")
            self.assertEqual(comparison.finding_changes[0]["change"], "improved")
            self.assertTrue(
                any(item["path"] == "runtime.faultTypes" for item in comparison.config_changes)
            )

    def test_incompatible_or_missing_evidence_never_appears_as_improvement(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            baseline = _run(
                workspace,
                "baseline",
                created_at="2026-08-01T00:00:00+00:00",
                score=90,
                coverage=100,
                latency=220,
                finding_severity="high",
            )
            candidate = _run(
                workspace,
                "candidate",
                service="catalog",
                created_at="2026-08-02T00:00:00+00:00",
                score=100,
                coverage=0,
            )

            comparison = ChamberApplication(workspace).compare(baseline.name, candidate.name)

            self.assertFalse(comparison.compatible)
            self.assertIsNone(comparison.score_delta)
            self.assertIsNone(comparison.evidence_coverage_delta)
            self.assertEqual(comparison.added_finding_ids, ())
            self.assertEqual(comparison.resolved_finding_ids, ())
            self.assertEqual(comparison.finding_changes, ())
            self.assertTrue(all(not item["comparable"] for item in comparison.signal_deltas))
            self.assertTrue(
                any(
                    "Service differs" in item["detail"] for item in comparison.compatibility_reasons
                )
            )


class StructuredReportAndAccessibilityTests(unittest.TestCase):
    def test_structured_report_uses_projections_and_keeps_exports(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            run_dir = _run(
                workspace,
                "report",
                created_at="2026-08-02T00:00:00+00:00",
                score=None,
                coverage=75,
                outcome="inconclusive",
                latency=240,
            )
            (run_dir / "report.md").write_text("RAW-MARKDOWN-SENTINEL", encoding="utf-8")
            app = create_app(workspace)
            client = TestClient(app)
            html = client.get(f"/api/v1/runs/{run_dir.name}/report?format=html")
            json_export = client.get(f"/api/v1/runs/{run_dir.name}/report?format=json")
            with patch.object(
                app.state.chamber, "report", return_value=run_dir / "report.md"
            ) as render_markdown:
                markdown = client.get(f"/api/v1/runs/{run_dir.name}/report")

            self.assertEqual(html.status_code, 200)
            self.assertIn("Tested scenario and environment", html.text)
            self.assertIn("Evidence coverage and limitations", html.text)
            self.assertIn("Remediation and retest plan", html.text)
            self.assertIn('role="img"', html.text)
            self.assertNotIn("RAW-MARKDOWN-SENTINEL", html.text)
            self.assertEqual(markdown.text, "RAW-MARKDOWN-SENTINEL")
            self.assertIn("tested_scope", json_export.json())
            self.assertEqual(render_markdown.call_count, 1)
            self.assertEqual(render_markdown.call_args.args[0].name, run_dir.name)

    def test_main_surfaces_include_accessible_status_focus_and_overflow_contracts(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".chamber"
            run_dir = _run(
                workspace,
                "accessible",
                created_at="2026-08-02T00:00:00+00:00",
                score=None,
                coverage=50,
                outcome="inconclusive",
            )
            app = create_app(workspace)
            client = TestClient(app)
            new_page = client.get("/new")
            run_page = client.get(f"/runs/{run_dir.name}")
            css = client.get("/static/app.css").text
            script = client.get("/static/app.js").text

            self.assertIn('role="status"', new_page.text)
            self.assertEqual(new_page.text.count('tabindex="-1"'), 4)
            self.assertIn('aria-labelledby="operational-verdict"', run_page.text)
            self.assertIn("No findings does not mean success", run_page.text)
            self.assertIn("--target-min: 44px", css)
            self.assertIn("mask-image", css)
            self.assertNotIn(".topbar-status { font-size: 0; }", css)
            self.assertIn("announcedState", script)
            self.assertIn("invalidField.focus", script)


def _run(
    workspace: Path,
    name: str,
    *,
    created_at: str,
    service: str = "payments",
    score: int | None,
    coverage: int,
    outcome: str = "ready",
    tags: tuple[str, ...] = (),
    findings: int = 0,
    fault: str | None = None,
    latency: float | None = None,
    failure_rate: float | None = None,
    restarts: int | None = None,
    finding_severity: str | None = None,
) -> Path:
    run_dir = workspace / "runs" / name
    record = initialize_run_record(run_dir, service_name=service, mode="kubernetes")
    record.update(
        {
            "state": "completed",
            "created_at": created_at,
            "updated_at": created_at,
            "tags": list(tags),
        }
    )
    write_json_atomic(run_dir / "run.json", record)
    config = {
        "apiVersion": "chamber.ampule.dev/v1alpha1",
        "kind": "ChamberConfig",
        "owner": "platform",
        "service": {"name": service, "repo": f"/workspace/{service}", "commit": f"{service}-v2"},
        "scenario": {
            "id": f"{service}-resilience",
            "revision": "revision-1",
            "tags": ["reliability"],
        },
        "traffic": {"journeys": [{"name": "health", "method": "GET", "path": "/health"}]},
        "runtime": {
            "provider": "kubernetes",
            "mode": "attach",
            "namespace": f"{service}-test",
            "faults": [{"type": fault}] if fault else [],
        },
    }
    (run_dir / "chamber.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    write_json_atomic(
        run_dir / "run-metadata.json",
        {"run_id": name, "stage": "assessed", "mode": "kubernetes", "namespace": f"{service}-test"},
    )
    result = {
        "schema_version": "chamber.ampule.dev/result/v1",
        "run_id": name,
        "status": outcome,
        "conclusive": score is not None,
        "confidence": "high" if score is not None else "limited",
        "readiness_score": score,
        "evidence_coverage_percent": coverage,
        "execution_coverage_percent": 100,
        "cleanup_verified": True,
        "rollback_verified": True,
        "verdict": {
            "headline": "Fixture verdict",
            "what_happened": "The fixture assessment completed.",
            "why": "Fixture evidence determines this result.",
            "next_step": "Review the fixture evidence.",
        },
        "evidence_requirements": {"required": [], "present": [], "missing": []},
        "evidence_limitations": [],
        "next_actions": [
            {"priority": 1, "category": "review", "title": "Review", "rationale": "Check evidence."}
        ],
    }
    write_json_atomic(run_dir / "result.json", result)
    finding_items = []
    if finding_severity:
        finding_items.append(
            {
                "finding_id": "shared-finding",
                "signal_type": "request_latency",
                "title": "Latency risk",
                "severity": finding_severity,
                "confidence": "high",
                "evidence_ids": ["k6-summary"],
            }
        )
    for index in range(findings - len(finding_items)):
        finding_items.append(
            {"finding_id": f"finding-{index}", "signal_type": "error_rate", "severity": "high"}
        )
    write_json_atomic(run_dir / "findings.json", finding_items)
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    if latency is not None or failure_rate is not None:
        metrics: dict[str, object] = {}
        if latency is not None:
            metrics["http_req_duration"] = {"p(95)": latency}
        if failure_rate is not None:
            metrics["http_req_failed"] = {"value": failure_rate}
        write_json_atomic(evidence_dir / "k6-summary.json", {"metrics": metrics})
    if restarts is not None:
        write_json_atomic(
            evidence_dir / "prometheus-memory.json",
            {
                "summaries": [
                    {
                        "pod_name": f"{service}-pod",
                        "peak_memory_bytes": 128 * 1024 * 1024,
                        "peak_cpu_cores": 0.2,
                        "max_restarts": restarts,
                    }
                ]
            },
        )
    refresh_evidence_manifest(run_dir)
    return run_dir
