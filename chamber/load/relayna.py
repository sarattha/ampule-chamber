"""Stateful submit-and-stream traffic execution for Relayna runtime services."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen


class RelaynaJourneyError(ValueError):
    """Raised when a Relayna journey contract or response is invalid."""


@dataclass(frozen=True)
class RelaynaTaskResult:
    """Observed lifecycle for one submitted Relayna task."""

    iteration: int
    task_id: str | None
    submit_status: int | None
    terminal_status: str | None
    success: bool
    submission_duration_ms: float
    stream_duration_ms: float
    total_duration_ms: float
    event_count: int
    statuses: tuple[str, ...]
    error: str | None


class HttpResponse(Protocol):
    """Small urllib response surface used by the Relayna adapter."""

    status: int

    def read(self) -> bytes: ...

    def __iter__(self) -> Iterator[bytes]: ...

    def __enter__(self) -> HttpResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


UrlOpener = Callable[..., HttpResponse]


def execute_relayna_journeys(
    journeys: tuple[dict[str, Any], ...],
    *,
    base_url: str,
    opener: UrlOpener = urlopen,
) -> dict[str, Any]:
    """Execute bounded Relayna journeys and return evidence-safe summary data."""

    started = time.monotonic()
    results: list[RelaynaTaskResult] = []
    journey_names: list[str] = []
    for journey in journeys:
        if str(journey.get("adapter", "http")) != "relayna":
            raise RelaynaJourneyError("Relayna execution cannot mix adapter types")
        validate_relayna_journey(journey)
        journey_names.append(str(journey.get("name") or journey.get("path") or "relayna"))
        iterations = _positive_int(journey.get("iterations", 1), "iterations")
        vus = min(_positive_int(journey.get("vus", 1), "vus"), iterations)
        with ThreadPoolExecutor(max_workers=vus) as executor:
            futures = [
                executor.submit(
                    _execute_task,
                    journey,
                    base_url=base_url,
                    iteration=index,
                    opener=opener,
                )
                for index in range(1, iterations + 1)
            ]
            results.extend(future.result() for future in futures)
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    completed = sum(item.terminal_status == "completed" for item in results)
    failed = sum(item.terminal_status == "failed" for item in results)
    successful = sum(item.success for item in results)
    return {
        "adapter": "relayna",
        "success": bool(results) and successful == len(results),
        "journeys": journey_names,
        "task_count": len(results),
        "completed_count": completed,
        "failed_count": failed,
        "successful_count": successful,
        "duration_ms": duration_ms,
        "tasks": [asdict(item) for item in results],
    }


def validate_relayna_journey(journey: dict[str, Any]) -> None:
    """Validate the stable Relayna journey contract before planning a run."""

    if str(journey.get("method", "POST")).upper() != "POST":
        raise RelaynaJourneyError("Relayna submission method must be POST")
    path = str(journey.get("path", ""))
    if not path.startswith("/"):
        raise RelaynaJourneyError("Relayna submission path must be absolute")
    if not isinstance(journey.get("expectedStatus", 202), int):
        raise RelaynaJourneyError("Relayna expectedStatus must be an integer")
    body = journey.get("body")
    if not isinstance(body, dict) or not body:
        raise RelaynaJourneyError("Relayna submission requires a non-empty JSON body")
    _positive_int(journey.get("iterations", 1), "iterations")
    _positive_int(journey.get("vus", 1), "vus")
    _relayna_contract(journey)


def _execute_task(
    journey: dict[str, Any],
    *,
    base_url: str,
    iteration: int,
    opener: UrlOpener,
) -> RelaynaTaskResult:
    started = time.monotonic()
    submission_duration_ms = 0.0
    stream_duration_ms = 0.0
    task_id: str | None = None
    submit_status: int | None = None
    terminal_status: str | None = None
    statuses: tuple[str, ...] = ()
    event_count = 0
    try:
        contract = _relayna_contract(journey)
        body = _request_body(journey, iteration=iteration)
        request = Request(
            base_url.rstrip("/") + str(journey.get("path", "/translations")),
            data=json.dumps(body).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        submission_started = time.monotonic()
        with opener(request, timeout=contract["timeout_seconds"]) as response:
            submit_status = int(response.status)
            raw = response.read().decode("utf-8")
        submission_duration_ms = round((time.monotonic() - submission_started) * 1000, 3)
        expected_status = int(journey.get("expectedStatus", 202))
        if submit_status != expected_status:
            raise RelaynaJourneyError(
                f"submission returned HTTP {submit_status}; expected {expected_status}"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RelaynaJourneyError("submission response is not JSON") from exc
        task_id_value = _json_path(payload, contract["task_id_path"])
        if not isinstance(task_id_value, str) or not task_id_value.strip():
            raise RelaynaJourneyError(
                f"submission response has no task id at {contract['task_id_path']!r}"
            )
        task_id = task_id_value.strip()
        events_path = contract["events_path"].replace("{task_id}", quote(task_id, safe=""))
        stream_request = Request(
            base_url.rstrip("/") + events_path,
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        stream_started = time.monotonic()
        with opener(stream_request, timeout=contract["timeout_seconds"]) as response:
            if int(response.status) != 200:
                raise RelaynaJourneyError(f"event stream returned HTTP {response.status}")
            terminal_status, statuses, event_count = _consume_sse(
                response,
                terminal_statuses=contract["terminal_statuses"],
                timeout_seconds=contract["timeout_seconds"],
                started=stream_started,
            )
        stream_duration_ms = round((time.monotonic() - stream_started) * 1000, 3)
        if terminal_status is None:
            raise RelaynaJourneyError("event stream ended without a terminal status")
        success = terminal_status in contract["success_statuses"]
        error = None if success else f"task ended with status {terminal_status!r}"
    except Exception as exc:  # noqa: BLE001 - execution evidence must retain adapter failures
        success = False
        error = str(exc)
    total_duration_ms = round((time.monotonic() - started) * 1000, 3)
    return RelaynaTaskResult(
        iteration=iteration,
        task_id=task_id,
        submit_status=submit_status,
        terminal_status=terminal_status,
        success=success,
        submission_duration_ms=submission_duration_ms,
        stream_duration_ms=stream_duration_ms,
        total_duration_ms=total_duration_ms,
        event_count=event_count,
        statuses=statuses,
        error=error,
    )


def _relayna_contract(journey: dict[str, Any]) -> dict[str, Any]:
    value = journey.get("relayna", {})
    if not isinstance(value, dict):
        raise RelaynaJourneyError("relayna must be a mapping")
    events_path = str(value.get("eventsPath", "/events/{task_id}"))
    if "{task_id}" not in events_path or not events_path.startswith("/"):
        raise RelaynaJourneyError(
            "relayna.eventsPath must be an absolute path containing {task_id}"
        )
    terminal_statuses = _statuses(value.get("terminalStatuses", ["completed", "failed"]))
    success_statuses = _statuses(value.get("successStatuses", ["completed"]))
    if not set(success_statuses).issubset(terminal_statuses):
        raise RelaynaJourneyError("success statuses must also be terminal statuses")
    return {
        "task_id_path": str(value.get("taskIdPath", "task_id")),
        "events_path": events_path,
        "terminal_statuses": terminal_statuses,
        "success_statuses": success_statuses,
        "timeout_seconds": _positive_int(value.get("timeoutSeconds", 300), "timeoutSeconds"),
    }


def _request_body(journey: dict[str, Any], *, iteration: int) -> dict[str, Any]:
    body = journey.get("body")
    if not isinstance(body, dict) or not body:
        raise RelaynaJourneyError("Relayna submission requires a non-empty JSON body")
    copied = deepcopy(body)
    if isinstance(copied.get("task_id"), str) and copied["task_id"]:
        copied["task_id"] = f"{copied['task_id']}-{iteration}-{time.time_ns()}"
    return copied


def _consume_sse(
    response: HttpResponse,
    *,
    terminal_statuses: tuple[str, ...],
    timeout_seconds: int,
    started: float,
) -> tuple[str | None, tuple[str, ...], int]:
    data_lines: list[str] = []
    statuses: list[str] = []
    event_count = 0
    for raw_line in response:
        if time.monotonic() - started > timeout_seconds:
            raise RelaynaJourneyError(f"event stream exceeded {timeout_seconds}s timeout")
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines:
                event_count += 1
                status = _event_status("\n".join(data_lines))
                if status:
                    statuses.append(status)
                    if status in terminal_statuses:
                        return status, tuple(statuses), event_count
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return None, tuple(statuses), event_count


def _event_status(data: str) -> str | None:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") is None:
        return None
    return str(payload["status"]).lower()


def _json_path(payload: Any, path: str) -> Any:
    current = payload
    normalized = path.removeprefix("$.")
    for part in normalized.split("."):
        if not part or not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _statuses(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RelaynaJourneyError("status list must contain at least one value")
    statuses = tuple(str(item).lower().strip() for item in value if str(item).strip())
    if not statuses:
        raise RelaynaJourneyError("status list must contain at least one value")
    return statuses


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RelaynaJourneyError(f"{name} must be a positive integer")
    return value
