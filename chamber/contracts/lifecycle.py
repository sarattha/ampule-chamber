"""Run lifecycle states shared by orchestration and reporting."""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    CREATED = "created"
    INTAKE_VALIDATED = "intake_validated"
    ENVIRONMENT_READY = "environment_ready"
    BASELINE_RUNNING = "baseline_running"
    BASELINE_PASSED = "baseline_passed"
    EXPERIMENT_RUNNING = "experiment_running"
    FAULT_ACTIVE = "fault_active"
    RECOVERY_VALIDATING = "recovery_validating"
    ANALYZING = "analyzing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
    }
)

ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset(
        {
            RunState.INTAKE_VALIDATED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.INTAKE_VALIDATED: frozenset(
        {
            RunState.ENVIRONMENT_READY,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.ENVIRONMENT_READY: frozenset(
        {
            RunState.BASELINE_RUNNING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.BASELINE_RUNNING: frozenset(
        {
            RunState.BASELINE_PASSED,
            RunState.ANALYZING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.BASELINE_PASSED: frozenset(
        {
            RunState.EXPERIMENT_RUNNING,
            RunState.ANALYZING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.EXPERIMENT_RUNNING: frozenset(
        {
            RunState.FAULT_ACTIVE,
            RunState.RECOVERY_VALIDATING,
            RunState.ANALYZING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.FAULT_ACTIVE: frozenset(
        {
            RunState.RECOVERY_VALIDATING,
            RunState.ANALYZING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.RECOVERY_VALIDATING: frozenset(
        {
            RunState.ANALYZING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.ANALYZING: frozenset(
        {
            RunState.REPORTING,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.REPORTING: frozenset(
        {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def allowed_next_states(state: RunState | str) -> frozenset[RunState]:
    """Return allowed next states for a run lifecycle state."""

    parsed = RunState(state)
    return ALLOWED_TRANSITIONS[parsed]


def is_allowed_transition(current: RunState | str, next_state: RunState | str) -> bool:
    """Return whether a run can move from current to next_state."""

    return RunState(next_state) in allowed_next_states(current)
