# Chamber Run Lifecycle

The run lifecycle is the source of truth for orchestrator state and report
timelines. States are monotonic except for terminal failure or cancellation.

## States

| State | Meaning |
| --- | --- |
| `created` | Run record exists but intake has not been validated. |
| `intake_validated` | Scenario, target contract, and safety guardrails are structurally valid. |
| `environment_ready` | Isolated runtime exists and the target can be deployed or reached. |
| `baseline_running` | Startup, readiness, health, and basic telemetry checks are active. |
| `baseline_passed` | Baseline checks passed and experiments may begin. |
| `experiment_running` | Load, soak, or other non-fault experiment is active. |
| `fault_active` | Controlled fault is currently injected. |
| `recovery_validating` | Fault has been removed and recovery conditions are being measured. |
| `analyzing` | Evidence is being correlated into findings and hypotheses. |
| `reporting` | Final report artifacts are being generated. |
| `completed` | Run finished and report artifacts were produced. |
| `failed` | Run stopped because a chamber operation failed or a required criterion could not be evaluated. |
| `cancelled` | Run was intentionally stopped before completion. |

## Allowed Transitions

```text
created -> intake_validated | failed | cancelled
intake_validated -> environment_ready | failed | cancelled
environment_ready -> baseline_running | failed | cancelled
baseline_running -> baseline_passed | analyzing | failed | cancelled
baseline_passed -> experiment_running | analyzing | failed | cancelled
experiment_running -> fault_active | recovery_validating | analyzing | failed | cancelled
fault_active -> recovery_validating | analyzing | failed | cancelled
recovery_validating -> analyzing | failed | cancelled
analyzing -> reporting | failed | cancelled
reporting -> completed | failed | cancelled
completed -> terminal
failed -> terminal
cancelled -> terminal
```

The executable representation lives in
`chamber/contracts/lifecycle.py`.
