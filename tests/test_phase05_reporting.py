from __future__ import annotations

import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from chamber.report import (
    EvidenceReference,
    ReportFinding,
    ReportInput,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    load_report_input,
    render_markdown_report,
    score_readiness,
)
from chamber.report.cli import main as report_cli_main

ROOT = Path(__file__).resolve().parents[1]
PHASE05_ARTIFACT_DIR = ROOT / "docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts"


class Phase05ReportingTests(unittest.TestCase):
    def test_readiness_score_uses_conservative_severity_weighting(self) -> None:
        high = _finding("high", finding_id="finding-high")
        medium = _finding("medium", finding_id="finding-medium")

        readiness = score_readiness((high, medium))

        self.assertEqual(readiness.score, 65)
        self.assertEqual(readiness.label, "not_ready")

        critical = _finding("critical", finding_id="finding-critical")
        readiness = score_readiness((critical,))

        self.assertEqual(readiness.score, 60)
        self.assertEqual(readiness.label, "not_ready")

        low = _finding("low", finding_id="finding-low")
        readiness = score_readiness((low,))

        self.assertEqual(readiness.score, 95)
        self.assertEqual(readiness.label, "ready")

    def test_rendered_report_is_deterministic_and_includes_required_sections(self) -> None:
        report = _report_input(findings=(_finding("high"),))

        first = render_markdown_report(report)
        second = render_markdown_report(report)

        self.assertEqual(first, second)
        for heading in (
            "## Service Metadata",
            "## Run Metadata",
            "## Tested Scenario",
            "## Readiness Score",
            "## Key Findings",
            "## Evidence References",
            "## Reproduction Details",
            "## Recommendations",
            "## Retest Plan",
            "## Cleanup Notes",
            "## Known Limitations",
        ):
            self.assertIn(heading, first)

    def test_finding_format_includes_confidence_evidence_cause_and_recommendations(self) -> None:
        report = _report_input(findings=(_finding("high"),))

        markdown = render_markdown_report(report)

        self.assertIn("- Severity: high", markdown)
        self.assertIn("- Confidence: high", markdown)
        self.assertIn("- Evidence: evidence-1", markdown)
        self.assertIn("- Suspected cause: The dependency was unavailable during load.", markdown)
        self.assertIn("Retry after downstream recovery.", markdown)
        self.assertIn("traffic-start, downstream-api-unavailable-start", markdown)

    def test_load_report_input_rejects_invalid_fixture_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON object"):
                load_report_input(fixture_path)

    def test_cli_renders_fixture_to_output_file(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            output_path = Path(tmp) / "report.md"
            fixture_path.write_text(json.dumps(_fixture_dict()), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "chamber.report.cli",
                    "--fixture",
                    str(fixture_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("# Ampule Chamber Reliability Report", output_path.read_text())

    def test_cli_writes_to_stdout_when_output_is_omitted(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
            stdout = StringIO()

            with patch(
                "sys.argv",
                ["ampule-chamber-report", "--fixture", str(fixture_path)],
            ):
                with redirect_stdout(stdout):
                    exit_code = report_cli_main()

            self.assertEqual(exit_code, 0)
            self.assertIn("# Ampule Chamber Reliability Report", stdout.getvalue())

    def test_phase05_sample_fixture_renders_expected_sections(self) -> None:
        fixture_path = PHASE05_ARTIFACT_DIR / "sample-report-input.json"
        if not fixture_path.exists():
            self.skipTest("phase 05 sample fixture is created during implementation")

        report = load_report_input(fixture_path)
        markdown = render_markdown_report(report)

        self.assertIn("dependency-failure-001", markdown)
        self.assertIn("live-dependency-fault-k6-summary.json", markdown)
        self.assertIn("## Known Limitations", markdown)


def _report_input(
    *,
    findings: tuple[ReportFinding, ...] = (),
) -> ReportInput:
    return ReportInput(
        title="Ampule Chamber Reliability Report",
        service=ServiceMetadata(
            name="sample-service",
            owner="platform",
            repository="https://example.invalid/repo",
            commit="fixture",
        ),
        run=RunMetadata(
            run_id="phase05-test",
            test_date="2026-06-12",
            duration_seconds=420,
            namespace="chamber-local-dependency-failure-001",
            provider="kind",
            lifecycle_state="completed",
        ),
        scenario=TestedScenario(
            scenario_id="dependency-failure-001",
            name="Dependency Failure",
            path="scenarios/dependency-failure.yaml",
            traffic_tool="k6",
            max_virtual_users=2,
            fault_summary="dependency_unavailable",
        ),
        findings=findings,
        evidence=(
            EvidenceReference(
                evidence_id="evidence-1",
                source="k6",
                signal_type="traffic_summary",
                resource="sample-service",
                collected_at="from-file",
                artifact_path="artifact.json",
            ),
        ),
        reproduction=ReproductionDetails(
            commands=("uv run ampule-chamber-report --fixture fixture.json",),
            artifacts=("artifact.json",),
        ),
        retest_plan=("Rerun the dependency failure scenario.",),
        cleanup_notes=("Delete chamber-owned Kubernetes namespace after live runs.",),
        limitations=("Fixture-backed report; no live Prometheus required.",),
    )


def _finding(
    severity: str,
    *,
    finding_id: str = "finding-1",
) -> ReportFinding:
    return ReportFinding(
        finding_id=finding_id,
        signal_type="error_rate",
        affected_resource="sample-service",
        observed_facts=("k6 HTTP failure rate was above threshold.",),
        suspected_cause="The dependency was unavailable during load.",
        severity=severity,
        confidence="high",
        evidence_ids=("evidence-1",),
        related_timeline_ids=("traffic-start", "downstream-api-unavailable-start"),
        recommendations=("Retry after downstream recovery.",),
    )


def _fixture_dict() -> dict[str, object]:
    report = _report_input(findings=(_finding("high"),))
    return {
        "title": report.title,
        "service": report.service.__dict__,
        "run": report.run.__dict__,
        "scenario": report.scenario.__dict__,
        "findings": [finding.__dict__ for finding in report.findings],
        "evidence": [evidence.__dict__ for evidence in report.evidence],
        "reproduction": report.reproduction.__dict__,
        "retest_plan": report.retest_plan,
        "cleanup_notes": report.cleanup_notes,
        "limitations": report.limitations,
    }


if __name__ == "__main__":
    unittest.main()
