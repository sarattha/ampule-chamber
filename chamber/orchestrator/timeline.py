"""Experiment timeline assembly for traffic and fault phases."""

from __future__ import annotations

from dataclasses import dataclass

from chamber.chaos import FaultPlan
from chamber.contracts.scenario import Scenario
from chamber.load import TrafficPlan
from chamber.load.planning import parse_duration_seconds


@dataclass(frozen=True)
class TimelineEvent:
    """Ordered experiment event for observability and reports."""

    offset_seconds: int
    event_type: str
    name: str
    source: str
    description: str
    details: dict[str, str]


@dataclass(frozen=True)
class ExperimentTimeline:
    """Combined traffic, fault, and recovery timeline."""

    run_id: str
    scenario_id: str
    events: tuple[TimelineEvent, ...]


def build_experiment_timeline(
    scenario: Scenario,
    *,
    traffic_plan: TrafficPlan,
    fault_plan: FaultPlan,
) -> ExperimentTimeline:
    """Combine traffic and fault plans into a deterministic event timeline."""

    events = [
        TimelineEvent(
            offset_seconds=0,
            event_type="traffic_start",
            name="traffic-start",
            source="load",
            description=f"Start {traffic_plan.tool} traffic against {traffic_plan.target_url}.",
            details={"targetUrl": traffic_plan.target_url},
        ),
        TimelineEvent(
            offset_seconds=traffic_plan.total_duration_seconds,
            event_type="traffic_stop",
            name="traffic-stop",
            source="load",
            description="Stop traffic after all scenario stages complete.",
            details={"totalDurationSeconds": str(traffic_plan.total_duration_seconds)},
        ),
    ]
    for event in fault_plan.events:
        events.append(
            TimelineEvent(
                offset_seconds=event.offset_seconds,
                event_type=event.event_type,
                name=event.name,
                source="chaos",
                description=event.description,
                details={"target": event.target},
            )
        )

    recovery_at = _recovery_offset(scenario, traffic_plan=traffic_plan, fault_plan=fault_plan)
    events.append(
        TimelineEvent(
            offset_seconds=recovery_at,
            event_type="recovery_validate",
            name="recovery-validate",
            source="orchestrator",
            description="Validate recovery conditions after traffic and fault windows.",
            details={},
        )
    )
    ordered = tuple(sorted(events, key=lambda event: (event.offset_seconds, _priority(event))))
    return ExperimentTimeline(
        run_id=traffic_plan.run_id,
        scenario_id=traffic_plan.scenario_id,
        events=ordered,
    )


def _recovery_offset(
    scenario: Scenario,
    *,
    traffic_plan: TrafficPlan,
    fault_plan: FaultPlan,
) -> int:
    last_fault_offset = max((event.offset_seconds for event in fault_plan.events), default=0)
    recovery_window = _recovery_window_seconds(scenario)
    return max(traffic_plan.total_duration_seconds, last_fault_offset + recovery_window)


def _recovery_window_seconds(scenario: Scenario) -> int:
    for section in ("successConditions", "failureConditions"):
        for condition in scenario.document.get(section, []):
            if not isinstance(condition, dict):
                continue
            if condition.get("type") in {"recovery_time_below", "recovery_time_above"}:
                threshold = condition.get("threshold")
                if threshold is not None:
                    return parse_duration_seconds(threshold)
    return 120


def _priority(event: TimelineEvent) -> int:
    return {
        "traffic_start": 0,
        "fault_start": 1,
        "fault_removed": 2,
        "traffic_stop": 3,
        "recovery_validate": 4,
    }.get(event.event_type, 99)
