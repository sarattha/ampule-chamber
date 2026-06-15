# Phase 07 Sample Agent Briefs

Generated from deterministic offline agent contracts for acceptance review. No
live OpenAI API call was made for this artifact.

## Planner Brief

- Scenario: `dependency-failure-001`
- Goals:
  - Exercise the target service with the supplied scenario contract.
  - Capture runtime evidence before drawing reliability conclusions.
- Risks:
  - Dependency failure can propagate to the target service.
  - Resource limits can hide restart or latency failure modes.
- Evidence rule: every cited finding must reference supplied evidence ids.

## Observability Analyst Brief

- Observed facts are limited to supplied evidence artifacts.
- Missing signals, such as unavailable traces or Prometheus backends, must stay
  listed as missing evidence.
- Root-cause statements remain hypotheses with confidence labels.

## Report Writer Brief

- Summary and recommendations must cite supplied evidence ids.
- Missing telemetry remains in the report limitations.
- Readiness status is not rewritten without scoring input or findings.

## SDK Boundary

Live agent execution uses `openai-agents` through
`chamber.agents.OpenAIAgentsSdkRunner`. Repository tests use deterministic
outputs and require no `OPENAI_API_KEY`.
