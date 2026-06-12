"""Deterministic markdown rendering for reliability reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEVERITY_PENALTIES = {
    "critical": 40,
    "high": 25,
    "medium": 10,
    "low": 5,
}


@dataclass(frozen=True)
class ServiceMetadata:
    """Service identity shown in the report."""

    name: str
    owner: str
    repository: str
    commit: str


@dataclass(frozen=True)
class RunMetadata:
    """Chamber run metadata shown in the report."""

    run_id: str
    test_date: str
    duration_seconds: int
    namespace: str
    provider: str
    lifecycle_state: str


@dataclass(frozen=True)
class TestedScenario:
    """Scenario summary included in the report."""

    scenario_id: str
    name: str
    path: str
    traffic_tool: str
    max_virtual_users: int
    fault_summary: str


@dataclass(frozen=True)
class EvidenceReference:
    """A compact pointer to preserved runtime evidence."""

    evidence_id: str
    source: str
    signal_type: str
    resource: str
    collected_at: str
    artifact_path: str


@dataclass(frozen=True)
class ReportFinding:
    """Report-ready reliability finding."""

    finding_id: str
    signal_type: str
    affected_resource: str
    observed_facts: tuple[str, ...]
    suspected_cause: str
    severity: str
    confidence: str
    evidence_ids: tuple[str, ...]
    related_timeline_ids: tuple[str, ...]
    recommendations: tuple[str, ...]


@dataclass(frozen=True)
class ReproductionDetails:
    """Commands and artifacts needed to reproduce the reported result."""

    commands: tuple[str, ...]
    artifacts: tuple[str, ...]


@dataclass(frozen=True)
class ReportInput:
    """All data required to render a reliability report."""

    title: str
    service: ServiceMetadata
    run: RunMetadata
    scenario: TestedScenario
    findings: tuple[ReportFinding, ...]
    evidence: tuple[EvidenceReference, ...]
    reproduction: ReproductionDetails
    retest_plan: tuple[str, ...]
    cleanup_notes: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ReadinessScore:
    """Deployment readiness score and status label."""

    score: int
    label: str


def load_report_input(path: str | Path) -> ReportInput:
    """Load a JSON report fixture."""

    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("report fixture must be a JSON object")
    return _report_input(raw)


def score_readiness(findings: tuple[ReportFinding, ...]) -> ReadinessScore:
    """Calculate conservative deployment readiness from finding severity."""

    score = 100
    has_critical = False
    has_high = False
    for finding in findings:
        severity = finding.severity.lower()
        score -= SEVERITY_PENALTIES.get(severity, 0)
        has_critical = has_critical or severity == "critical"
        has_high = has_high or severity == "high"

    score = max(score, 0)
    if has_critical:
        label = "not_ready"
    elif score >= 90 and not has_high:
        label = "ready"
    elif score >= 70:
        label = "conditional"
    else:
        label = "not_ready"
    return ReadinessScore(score=score, label=label)


def render_markdown_report(report: ReportInput) -> str:
    """Render a deterministic markdown reliability report."""

    readiness = score_readiness(report.findings)
    lines = [
        f"# {report.title}",
        "",
        "## Service Metadata",
        f"- Service: {report.service.name}",
        f"- Owner: {report.service.owner}",
        f"- Repository: {report.service.repository}",
        f"- Commit: {report.service.commit}",
        "",
        "## Run Metadata",
        f"- Run ID: {report.run.run_id}",
        f"- Test date: {report.run.test_date}",
        f"- Duration: {report.run.duration_seconds} seconds",
        f"- Namespace: {report.run.namespace}",
        f"- Provider: {report.run.provider}",
        f"- Lifecycle state: {report.run.lifecycle_state}",
        "",
        "## Tested Scenario",
        f"- Scenario: {report.scenario.scenario_id} ({report.scenario.name})",
        f"- Path: {report.scenario.path}",
        f"- Traffic tool: {report.scenario.traffic_tool}",
        f"- Max virtual users: {report.scenario.max_virtual_users}",
        f"- Faults: {report.scenario.fault_summary}",
        "",
        "## Readiness Score",
        f"- Score: {readiness.score}/100",
        f"- Status: {readiness.label}",
        "",
        "## Key Findings",
    ]
    if report.findings:
        for index, finding in enumerate(report.findings, start=1):
            lines.extend(_finding_lines(index, finding))
    else:
        lines.append("- No reliability findings detected in the supplied evidence.")
    lines.extend(
        [
            "",
            "## Evidence References",
            *_evidence_lines(report.evidence),
            "",
            "## Reproduction Details",
            "### Commands",
            *_list_lines(report.reproduction.commands, empty="No reproduction commands recorded."),
            "",
            "### Artifacts",
            *_list_lines(
                report.reproduction.artifacts, empty="No reproduction artifacts recorded."
            ),
            "",
            "## Recommendations",
            *_recommendation_lines(report.findings),
            "",
            "## Retest Plan",
            *_list_lines(report.retest_plan, empty="No retest plan recorded."),
            "",
            "## Cleanup Notes",
            *_list_lines(report.cleanup_notes, empty="No cleanup notes recorded."),
            "",
            "## Known Limitations",
            *_list_lines(report.limitations, empty="No limitations recorded."),
            "",
        ]
    )
    return "\n".join(lines)


def _finding_lines(index: int, finding: ReportFinding) -> list[str]:
    lines = [
        f"### {index}. {finding.signal_type} on {finding.affected_resource}",
        f"- Severity: {finding.severity}",
        f"- Confidence: {finding.confidence}",
        f"- Evidence: {', '.join(finding.evidence_ids)}",
        f"- Related timeline events: {_join_or_none(finding.related_timeline_ids)}",
        "- Observed facts:",
        *_indented_list(finding.observed_facts),
        f"- Suspected cause: {finding.suspected_cause}",
        "- Recommended actions:",
        *_indented_list(finding.recommendations),
        "",
    ]
    return lines


def _evidence_lines(evidence: tuple[EvidenceReference, ...]) -> list[str]:
    if not evidence:
        return ["- No evidence references recorded."]
    return [
        (
            f"- {item.evidence_id}: {item.source}/{item.signal_type} on {item.resource}; "
            f"collected {item.collected_at}; artifact {item.artifact_path}"
        )
        for item in evidence
    ]


def _recommendation_lines(findings: tuple[ReportFinding, ...]) -> list[str]:
    recommendations: list[str] = []
    for finding in findings:
        for recommendation in finding.recommendations:
            recommendations.append(f"{finding.finding_id}: {recommendation}")
    return _list_lines(tuple(recommendations), empty="No recommendations recorded.")


def _list_lines(values: tuple[str, ...], *, empty: str) -> list[str]:
    if not values:
        return [f"- {empty}"]
    return [f"- {value}" for value in values]


def _indented_list(values: tuple[str, ...]) -> list[str]:
    if not values:
        return ["  - None recorded."]
    return [f"  - {value}" for value in values]


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _report_input(raw: dict[str, Any]) -> ReportInput:
    return ReportInput(
        title=_string(raw, "title"),
        service=_service(_mapping(raw, "service")),
        run=_run(_mapping(raw, "run")),
        scenario=_scenario(_mapping(raw, "scenario")),
        findings=tuple(_finding(item) for item in _dict_list(raw, "findings")),
        evidence=tuple(_evidence(item) for item in _dict_list(raw, "evidence")),
        reproduction=_reproduction(_mapping(raw, "reproduction")),
        retest_plan=tuple(_string_list(raw, "retest_plan")),
        cleanup_notes=tuple(_string_list(raw, "cleanup_notes")),
        limitations=tuple(_string_list(raw, "limitations")),
    )


def _service(raw: dict[str, Any]) -> ServiceMetadata:
    return ServiceMetadata(
        name=_string(raw, "name"),
        owner=_string(raw, "owner"),
        repository=_string(raw, "repository"),
        commit=_string(raw, "commit"),
    )


def _run(raw: dict[str, Any]) -> RunMetadata:
    return RunMetadata(
        run_id=_string(raw, "run_id"),
        test_date=_string(raw, "test_date"),
        duration_seconds=_integer(raw, "duration_seconds"),
        namespace=_string(raw, "namespace"),
        provider=_string(raw, "provider"),
        lifecycle_state=_string(raw, "lifecycle_state"),
    )


def _scenario(raw: dict[str, Any]) -> TestedScenario:
    return TestedScenario(
        scenario_id=_string(raw, "scenario_id"),
        name=_string(raw, "name"),
        path=_string(raw, "path"),
        traffic_tool=_string(raw, "traffic_tool"),
        max_virtual_users=_integer(raw, "max_virtual_users"),
        fault_summary=_string(raw, "fault_summary"),
    )


def _evidence(raw: dict[str, Any]) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=_string(raw, "evidence_id"),
        source=_string(raw, "source"),
        signal_type=_string(raw, "signal_type"),
        resource=_string(raw, "resource"),
        collected_at=_string(raw, "collected_at"),
        artifact_path=_string(raw, "artifact_path"),
    )


def _finding(raw: dict[str, Any]) -> ReportFinding:
    return ReportFinding(
        finding_id=_string(raw, "finding_id"),
        signal_type=_string(raw, "signal_type"),
        affected_resource=_string(raw, "affected_resource"),
        observed_facts=tuple(_string_list(raw, "observed_facts")),
        suspected_cause=_string(raw, "suspected_cause"),
        severity=_string(raw, "severity"),
        confidence=_string(raw, "confidence"),
        evidence_ids=tuple(_string_list(raw, "evidence_ids")),
        related_timeline_ids=tuple(_string_list(raw, "related_timeline_ids")),
        recommendations=tuple(_string_list(raw, "recommendations")),
    )


def _reproduction(raw: dict[str, Any]) -> ReproductionDetails:
    return ReproductionDetails(
        commands=tuple(_string_list(raw, "commands")),
        artifacts=tuple(_string_list(raw, "artifacts")),
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"report fixture field {key!r} must be an object")
    return value


def _dict_list(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = _list(raw, key)
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"report fixture field {key!r} must contain objects")
    return values


def _string_list(raw: dict[str, Any], key: str) -> list[str]:
    values = _list(raw, key)
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"report fixture field {key!r} must contain strings")
    return values


def _list(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"report fixture field {key!r} must be a list")
    return value


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"report fixture field {key!r} must be a non-empty string")
    return value


def _integer(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise ValueError(f"report fixture field {key!r} must be an integer")
    return value
