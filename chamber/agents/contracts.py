"""Structured, evidence-bound contracts for chamber agent outputs."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any


class AgentValidationError(ValueError):
    """Raised when an agent output violates chamber evidence rules."""


@dataclass(frozen=True)
class EvidenceCitation:
    """Reference to evidence supplied to an agent."""

    evidence_id: str
    usage: str


@dataclass(frozen=True)
class ChamberAgentContext:
    """Bounded input context shared by phase 07 agents."""

    scenario_id: str
    scenario_path: str
    service_name: str
    run_id: str | None
    evidence_ids: tuple[str, ...]
    finding_ids: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    missing_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class RootCauseHypothesis:
    """Evidence-backed hypothesis, not an asserted fact."""

    summary: str
    confidence: str
    evidence_ids: tuple[str, ...]
    follow_up_checks: tuple[str, ...]


@dataclass(frozen=True)
class ChamberTestPlan:
    """Planner output for a bounded chamber run."""

    scenario_id: str
    test_goals: tuple[str, ...]
    risks: tuple[str, ...]
    expected_signals: tuple[str, ...]
    success_criteria: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceAnalysisBrief:
    """Observability analyst output over supplied artifacts."""

    scenario_id: str
    observed_facts: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    hypotheses: tuple[RootCauseHypothesis, ...]
    citations: tuple[EvidenceCitation, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ReportNarrative:
    """Report-writer output that can be merged into markdown reports."""

    summary: str
    recommendations: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


def validate_evidence_bound_output(output: object, *, available_evidence_ids: set[str]) -> None:
    """Reject outputs that cite evidence outside the supplied artifact set."""

    cited = _collect_evidence_ids(output)
    missing = sorted(cited - available_evidence_ids)
    if missing:
        raise AgentValidationError("agent output cites unavailable evidence: " + ", ".join(missing))


def deterministic_test_plan(context: ChamberAgentContext) -> ChamberTestPlan:
    """Build a stable planner fixture for tests and offline review."""

    citations = tuple(
        EvidenceCitation(evidence_id=item, usage="input evidence") for item in context.evidence_ids
    )
    return ChamberTestPlan(
        scenario_id=context.scenario_id,
        test_goals=(
            f"Exercise {context.service_name} with the supplied scenario contract.",
            "Capture runtime evidence before drawing reliability conclusions.",
        ),
        risks=(
            "Dependency failure can propagate to the target service.",
            "Resource limits can hide restart or latency failure modes.",
        ),
        expected_signals=(
            "pod_status",
            "kubernetes_events",
            "logs",
            "request_latency",
            "error_rate",
        ),
        success_criteria=(
            "All cited findings must reference supplied evidence ids.",
            "Missing telemetry must be reported as unavailable.",
        ),
        citations=citations,
        limitations=context.missing_signals,
    )


def deterministic_evidence_brief(context: ChamberAgentContext) -> EvidenceAnalysisBrief:
    """Build a stable observability brief from supplied evidence ids."""

    observed = tuple(
        f"Evidence artifact {item} was supplied for review." for item in context.evidence_ids
    )
    hypotheses = (
        RootCauseHypothesis(
            summary="No unsupported root cause is asserted by the offline brief.",
            confidence="low",
            evidence_ids=context.evidence_ids[:1],
            follow_up_checks=("Review live telemetry before promoting this hypothesis.",),
        ),
    )
    citations = tuple(
        EvidenceCitation(evidence_id=item, usage="observed artifact")
        for item in context.evidence_ids
    )
    return EvidenceAnalysisBrief(
        scenario_id=context.scenario_id,
        observed_facts=observed or ("No runtime evidence was supplied.",),
        missing_evidence=context.missing_signals,
        hypotheses=hypotheses,
        citations=citations,
        limitations=("Offline deterministic brief; no live LLM call was made.",),
    )


def deterministic_report_narrative(context: ChamberAgentContext) -> ReportNarrative:
    """Build stable report copy that stays within supplied evidence."""

    return ReportNarrative(
        summary=(
            f"{context.service_name} was reviewed for scenario {context.scenario_id}; "
            "recommendations are limited to supplied evidence."
        ),
        recommendations=(
            "Rerun the chamber after remediation and require no critical findings.",
            "Keep missing telemetry listed as a report limitation.",
        ),
        evidence_ids=context.evidence_ids,
        limitations=context.missing_signals,
    )


def _collect_evidence_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return set()
    if isinstance(value, EvidenceCitation):
        return {value.evidence_id}
    if isinstance(value, tuple | list | set | frozenset):
        ids: set[str] = set()
        for item in value:
            ids.update(_collect_evidence_ids(item))
        return ids
    if isinstance(value, dict):
        ids = set()
        for item in value.values():
            ids.update(_collect_evidence_ids(item))
        return ids
    if is_dataclass(value):
        ids = set()
        for field in fields(value):
            field_value: Any = getattr(value, field.name)
            if field.name == "evidence_ids" and isinstance(field_value, tuple):
                ids.update(str(item) for item in field_value)
            else:
                ids.update(_collect_evidence_ids(field_value))
        return ids
    return set()
