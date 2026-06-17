# Phase 12 Agent Capability Validation

Source requirement: GitHub issue #9, "Feature request: automatic chamber
workflow from service onboarding to report."

## Capability Matrix

| Agent | Issue #9 capability | Implementation evidence |
| --- | --- | --- |
| Onboarding agent | Inspect repository layout, manifests, Dockerfiles, ports, env vars, and dependency hints; produce draft config; do not invent secrets or mutate target repo. | `OnboardingAgentDraft` structured output, deterministic offline output, live prompt in `chamber.workflow`, persisted `agent/onboarding-agent.json`. |
| Scenario planner agent | Convert service shape and config into baseline, traffic, dependency, recovery, and fault scenarios; produce a bounded executable plan. | `ScenarioPlannerBrief` structured output with planned scenarios, required evidence, and safety constraints; persisted `agent/scenario-planner-agent.json`. |
| Run supervisor agent | Explain blocked readiness, missing endpoints, failed probes, or unsafe preconditions; do not run arbitrary commands or bypass safety checks. | `RunSupervisorBrief` structured output with status, blockers, readiness notes, citations, and limitations; persisted `agent/run-supervisor-agent.json`. |
| Traffic and chaos agent | Recommend load and fault profiles from the approved plan; stay inside chamber-owned resources and configured policies. | `TrafficChaosRecommendation` structured output with profiles and safety constraints; persisted `agent/traffic-chaos-agent.json`. |
| Evidence analyst agent | Review Kubernetes events, logs, metrics, k6 summaries, timelines, and findings; separate observed facts from hypotheses. | `EvidenceAnalystBrief` structured output with observed facts and `RootCauseHypothesis` values; persisted `agent/evidence-analyst-agent.json`. |
| Report writer agent | Improve report narrative using only validated evidence, findings, and limitations; cite only supplied evidence IDs. | `ReportNarrative` structured output, evidence ID validation, and report sections from `agent/report-writer-agent.json`. |

## Validation Evidence

- Offline mode is deterministic and covered by
  `tests.test_phase11_12_13_workflow.Phase12AgentPipelineTests`.
- Live mode requires `OPENAI_API_KEY` before any live call.
- Live mode now calls the OpenAI Agents SDK for all six role names:
  `onboarding-agent`, `scenario-planner-agent`, `run-supervisor-agent`,
  `traffic-chaos-agent`, `evidence-analyst-agent`, and `report-writer-agent`.
- A live validation run using `gpt-5.4-mini` completed successfully with the key
  supplied only through process environment. It produced all six expected
  structured JSON files and passed evidence-citation validation.

## Safety Notes

- No secret value is stored in this artifact.
- Raw model responses are not stored; the run-directory writer persists only
  structured, evidence-validated role outputs.
- Any output citing unavailable evidence fails before it can be rendered into a
  report.
