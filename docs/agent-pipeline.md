# Agent Pipeline

Ampule Chamber `1.0.0` includes six bounded agent roles.

| Agent | Responsibility |
| --- | --- |
| Onboarding agent | Inspect repository shape and draft reviewable chamber config assumptions. |
| Scenario planner agent | Convert service shape and config into bounded scenario intent. |
| Run supervisor agent | Explain readiness blockers, missing endpoints, failed probes, and unsafe preconditions. |
| Traffic and chaos agent | Recommend load and fault profiles inside approved policies. |
| Evidence analyst agent | Separate observed facts from hypotheses across supplied evidence. |
| Report writer agent | Improve report narrative using only validated evidence and limitations. |

## Modes

```yaml
agents:
  mode: offline
```

- `off`: skip agent output.
- `offline`: deterministic outputs for CI and repeatable local tests.
- `live`: use the OpenAI Agents SDK and require `OPENAI_API_KEY`.

All agent outputs are validated before they are persisted. Outputs that cite
unavailable evidence IDs fail validation.
