from __future__ import annotations

import unittest
from unittest.mock import patch

from chamber.agents import (
    AgentValidationError,
    ChamberAgentContext,
    EvidenceCitation,
    ReportNarrative,
    deterministic_evidence_brief,
    deterministic_report_narrative,
    deterministic_test_plan,
    validate_evidence_bound_output,
)
from chamber.agents.sdk import OpenAIAgentsSdkRunner


class Phase07AgentContractTests(unittest.TestCase):
    def test_deterministic_agent_outputs_are_evidence_bound(self) -> None:
        context = ChamberAgentContext(
            scenario_id="dependency-failure-001",
            scenario_path="scenarios/dependency-failure.yaml",
            service_name="sample-service",
            run_id="run-1",
            evidence_ids=("evidence-1", "evidence-2"),
            finding_ids=("finding-1",),
            artifact_paths=("evidence.json",),
            missing_signals=("traces",),
        )

        plan = deterministic_test_plan(context)
        brief = deterministic_evidence_brief(context)
        narrative = deterministic_report_narrative(context)

        self.assertEqual(plan.scenario_id, "dependency-failure-001")
        self.assertIn("traces", brief.missing_evidence)
        self.assertEqual(narrative.evidence_ids, ("evidence-1", "evidence-2"))
        validate_evidence_bound_output(plan, available_evidence_ids={"evidence-1", "evidence-2"})
        validate_evidence_bound_output(brief, available_evidence_ids={"evidence-1", "evidence-2"})
        validate_evidence_bound_output(
            narrative,
            available_evidence_ids={"evidence-1", "evidence-2"},
        )

    def test_evidence_validator_rejects_unavailable_citations(self) -> None:
        narrative = ReportNarrative(
            summary="Unsupported claim.",
            recommendations=("Fix it.",),
            evidence_ids=("missing-evidence",),
            limitations=(),
        )

        with self.assertRaisesRegex(AgentValidationError, "missing-evidence"):
            validate_evidence_bound_output(narrative, available_evidence_ids={"evidence-1"})

        citation = EvidenceCitation(evidence_id="other-missing", usage="claim")
        with self.assertRaisesRegex(AgentValidationError, "other-missing"):
            validate_evidence_bound_output(citation, available_evidence_ids=set())


class Phase07SdkRunnerTests(unittest.TestCase):
    def test_live_sdk_runner_requires_api_key_before_import(self) -> None:
        runner = OpenAIAgentsSdkRunner()

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(AgentValidationError, "OPENAI_API_KEY"):
                runner.run_structured(
                    name="planner",
                    instructions="Return a report narrative.",
                    input_text="input",
                    output_type=ReportNarrative,
                )


if __name__ == "__main__":
    unittest.main()
