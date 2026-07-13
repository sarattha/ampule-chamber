"""Stateful submit-and-stream traffic execution for Relayna runtime services."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_RELAYNA_VUS = 32
MAX_RELAYNA_ITERATIONS = 1000
MAX_RELAYNA_TIMEOUT_SECONDS = 3600
MAX_RELAYNA_DURATION_SECONDS = 86400
MAX_MULTIPART_FILE_BYTES = 128 * 1024 * 1024
MAX_MULTIPART_TOTAL_BYTES = 256 * 1024 * 1024
SUPPORTED_MULTIPART_CONTENT_TYPES = {
    "application/pdf",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
MULTIPART_FIELD_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class RelaynaJourneyError(ValueError):
    """Raised when a Relayna journey contract or response is invalid."""


class RelaynaTimeoutError(RelaynaJourneyError):
    """Raised when a Relayna lifecycle exceeds its configured timeout."""


@dataclass(frozen=True)
class RelaynaTaskResult:
    """Observed lifecycle for one submitted Relayna task."""

    iteration: int
    journey: str
    task_id: str | None
    submit_status: int | None
    terminal_status: str | None
    success: bool
    submission_duration_ms: float
    stream_duration_ms: float
    total_duration_ms: float
    event_count: int
    statuses: tuple[str, ...]
    failure_stage: str | None
    uploads: tuple[dict[str, Any], ...]
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
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Execute bounded Relayna journeys and return evidence-safe summary data."""

    started = time.monotonic()
    results: list[RelaynaTaskResult] = []
    journey_names: list[str] = []
    inputs: list[dict[str, Any]] = []
    workspace_path = Path(workspace).resolve() if workspace is not None else None
    for journey in journeys:
        if str(journey.get("adapter", "http")) != "relayna":
            raise RelaynaJourneyError("Relayna execution cannot mix adapter types")
        validate_relayna_journey(journey, workspace=workspace_path)
        journey_name = str(journey.get("name") or journey.get("path") or "relayna")
        journey_names.append(journey_name)
        upload_metadata = _multipart_upload_metadata(
            journey,
            workspace=workspace_path,
            include_digest=True,
        )
        if str(journey.get("requestEncoding", "json")) == "multipart":
            inputs.append({"journey": journey_name, "files": upload_metadata})
        iterations = _bounded_positive_int(
            journey.get("iterations", 1), "iterations", maximum=MAX_RELAYNA_ITERATIONS
        )
        vus = min(
            _bounded_positive_int(journey.get("vus", 1), "vus", maximum=MAX_RELAYNA_VUS),
            iterations,
        )
        with ThreadPoolExecutor(max_workers=vus) as executor:
            futures = [
                executor.submit(
                    _execute_task,
                    journey,
                    base_url=base_url,
                    iteration=index,
                    opener=opener,
                    workspace=workspace_path,
                    upload_metadata=upload_metadata,
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
        "inputs": inputs,
        "tasks": [asdict(item) for item in results],
    }


def validate_relayna_journey(
    journey: dict[str, Any],
    *,
    workspace: str | Path | None = None,
) -> None:
    """Validate the stable Relayna journey contract before planning a run."""

    if str(journey.get("method", "POST")).upper() != "POST":
        raise RelaynaJourneyError("Relayna submission method must be POST")
    path = str(journey.get("path", ""))
    if not path.startswith("/"):
        raise RelaynaJourneyError("Relayna submission path must be absolute")
    expected_status = journey.get("expectedStatus", 202)
    if (
        not isinstance(expected_status, int)
        or isinstance(expected_status, bool)
        or expected_status < 100
        or expected_status > 599
    ):
        raise RelaynaJourneyError("Relayna expectedStatus must be an integer")
    encoding = str(journey.get("requestEncoding", "json" if "body" in journey else "json"))
    if encoding == "json":
        body = journey.get("body")
        if not isinstance(body, dict) or not body:
            raise RelaynaJourneyError("Relayna submission requires a non-empty JSON body")
    elif encoding == "multipart":
        _multipart_upload_metadata(
            journey,
            workspace=Path(workspace).resolve() if workspace is not None else None,
            include_digest=False,
        )
    else:
        raise RelaynaJourneyError("Relayna requestEncoding must be json or multipart")
    _bounded_positive_int(
        journey.get("iterations", 1), "iterations", maximum=MAX_RELAYNA_ITERATIONS
    )
    _bounded_positive_int(journey.get("vus", 1), "vus", maximum=MAX_RELAYNA_VUS)
    if "durationSeconds" in journey:
        _bounded_positive_int(
            journey["durationSeconds"],
            "durationSeconds",
            maximum=MAX_RELAYNA_DURATION_SECONDS,
        )
    _relayna_contract(journey)


def _execute_task(
    journey: dict[str, Any],
    *,
    base_url: str,
    iteration: int,
    opener: UrlOpener,
    workspace: Path | None,
    upload_metadata: tuple[dict[str, Any], ...],
) -> RelaynaTaskResult:
    started = time.monotonic()
    submission_duration_ms = 0.0
    stream_duration_ms = 0.0
    task_id: str | None = None
    submit_status: int | None = None
    terminal_status: str | None = None
    statuses: tuple[str, ...] = ()
    event_count = 0
    journey_name = str(journey.get("name") or journey.get("path") or "relayna")
    failure_stage: str | None = "admission"
    try:
        contract = _relayna_contract(journey)
        request = _submission_request(
            journey,
            base_url=base_url,
            iteration=iteration,
            workspace=workspace,
        )
        submission_started = time.monotonic()
        try:
            with opener(request, timeout=contract["timeout_seconds"]) as response:
                submit_status = int(response.status)
                raw = response.read().decode("utf-8")
        finally:
            submission_duration_ms = round((time.monotonic() - submission_started) * 1000, 3)
        expected_status = int(journey.get("expectedStatus", 202))
        if submit_status != expected_status:
            raise RelaynaJourneyError(
                f"submission returned HTTP {submit_status}; expected {expected_status}"
            )
        failure_stage = "task_id_extraction"
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
        failure_stage = "sse_connection"
        events_path = contract["events_path"].replace("{task_id}", quote(task_id, safe=""))
        stream_request = Request(
            base_url.rstrip("/") + events_path,
            headers={"Accept": "text/event-stream"},
            method="GET",
        )
        stream_started = time.monotonic()
        try:
            with opener(stream_request, timeout=contract["timeout_seconds"]) as response:
                if int(response.status) != 200:
                    raise RelaynaJourneyError(f"event stream returned HTTP {response.status}")
                terminal_status, statuses, event_count = _consume_sse(
                    response,
                    terminal_statuses=contract["terminal_statuses"],
                    timeout_seconds=contract["timeout_seconds"],
                    started=stream_started,
                )
        finally:
            stream_duration_ms = round((time.monotonic() - stream_started) * 1000, 3)
        failure_stage = "terminal_failure"
        if terminal_status is None:
            raise RelaynaJourneyError("event stream ended without a terminal status")
        success = terminal_status in contract["success_statuses"]
        error = None if success else f"task ended with status {terminal_status!r}"
        if success:
            failure_stage = None
    except Exception as exc:  # noqa: BLE001 - execution evidence must retain adapter failures
        success = False
        error = str(exc)
        if isinstance(exc, RelaynaTimeoutError) or (
            failure_stage == "sse_connection" and _looks_like_timeout(exc)
        ):
            failure_stage = "timeout"
    total_duration_ms = round((time.monotonic() - started) * 1000, 3)
    return RelaynaTaskResult(
        iteration=iteration,
        journey=journey_name,
        task_id=task_id,
        submit_status=submit_status,
        terminal_status=terminal_status,
        success=success,
        submission_duration_ms=submission_duration_ms,
        stream_duration_ms=stream_duration_ms,
        total_duration_ms=total_duration_ms,
        event_count=event_count,
        statuses=statuses,
        failure_stage=failure_stage,
        uploads=upload_metadata,
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
    task_id_path = value.get("taskIdPath", "task_id")
    if not isinstance(task_id_path, str) or not task_id_path.strip():
        raise RelaynaJourneyError("relayna.taskIdPath must be a non-empty response path")
    return {
        "task_id_path": task_id_path.strip(),
        "events_path": events_path,
        "terminal_statuses": terminal_statuses,
        "success_statuses": success_statuses,
        "timeout_seconds": _bounded_positive_int(
            value.get("timeoutSeconds", 300),
            "timeoutSeconds",
            maximum=MAX_RELAYNA_TIMEOUT_SECONDS,
        ),
    }


def _submission_request(
    journey: dict[str, Any],
    *,
    base_url: str,
    iteration: int,
    workspace: Path | None,
) -> Request:
    encoding = str(journey.get("requestEncoding", "json"))
    headers = {"Accept": "application/json"}
    if encoding == "multipart":
        data, content_type = _multipart_body(
            journey,
            iteration=iteration,
            workspace=workspace,
        )
        headers["Content-Type"] = content_type
    else:
        body = _request_body(journey, iteration=iteration)
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return Request(
        base_url.rstrip("/") + str(journey.get("path", "/translations")),
        data=data,
        headers=headers,
        method="POST",
    )


def _multipart_body(
    journey: dict[str, Any],
    *,
    iteration: int,
    workspace: Path | None,
) -> tuple[bytes, str]:
    multipart = _multipart_mapping(journey)
    boundary = f"ampule-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    fields = multipart.get("fields", {})
    assert isinstance(fields, dict)
    unique_field = str(multipart.get("uniqueTaskIdField", "task_id")).strip()
    for name in sorted(fields):
        value = _serialize_multipart_field(fields[name], name=name)
        if unique_field and name == unique_field and value:
            value = f"{value}-{iteration}-{time.time_ns()}"
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    for item in _multipart_file_items(multipart):
        path = _validated_file_path(item, workspace=workspace)
        if path is None:
            continue
        content_type = _content_type(item)
        filename = _filename(item, path=path)
        payload = path.read_bytes()
        max_file_bytes, _ = _multipart_limits(multipart)
        if len(payload) > max_file_bytes:
            raise RelaynaJourneyError(
                f"multipart file {filename!r} exceeds maxFileBytes {max_file_bytes}"
            )
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{item["field"]}"; '
                    f'filename="{_quoted_header(filename)}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                payload,
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    _, max_total_bytes = _multipart_limits(multipart)
    if len(body) > max_total_bytes:
        raise RelaynaJourneyError(f"multipart request exceeds maxTotalBytes {max_total_bytes}")
    return body, f"multipart/form-data; boundary={boundary}"


def _multipart_upload_metadata(
    journey: dict[str, Any],
    *,
    workspace: Path | None,
    include_digest: bool,
) -> tuple[dict[str, Any], ...]:
    if str(journey.get("requestEncoding", "json")) != "multipart":
        return ()
    multipart = _multipart_mapping(journey)
    fields = multipart.get("fields", {})
    if not isinstance(fields, dict):
        raise RelaynaJourneyError("multipart.fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not MULTIPART_FIELD_NAME.fullmatch(name):
            raise RelaynaJourneyError(
                "multipart field names must use letters, numbers, dot, underscore, or hyphen"
            )
        _serialize_multipart_field(value, name=name)
    unique_field = multipart.get("uniqueTaskIdField", "task_id")
    if not isinstance(unique_field, str):
        raise RelaynaJourneyError("multipart.uniqueTaskIdField must be a string")
    if unique_field and not MULTIPART_FIELD_NAME.fullmatch(unique_field):
        raise RelaynaJourneyError("multipart.uniqueTaskIdField is not a valid field name")
    files = _multipart_file_items(multipart)
    if not files:
        raise RelaynaJourneyError("multipart.files must contain at least one file")
    max_file_bytes, max_total_bytes = _multipart_limits(multipart)
    seen: set[str] = set()
    total_size = 0
    metadata: list[dict[str, Any]] = []
    request_files: list[tuple[dict[str, Any], Path, int]] = []
    for index, item in enumerate(files, start=1):
        field = item.get("field")
        if not isinstance(field, str) or not MULTIPART_FIELD_NAME.fullmatch(field):
            raise RelaynaJourneyError(
                f"multipart file {index} field must use letters, numbers, dot, "
                "underscore, or hyphen"
            )
        if field in seen:
            raise RelaynaJourneyError("multipart file fields must be unique")
        seen.add(field)
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise RelaynaJourneyError(f"multipart file {field!r} required must be a boolean")
        path = _validated_file_path(item, workspace=workspace)
        if path is None:
            continue
        size = path.stat().st_size
        if size == 0:
            raise RelaynaJourneyError(f"multipart file {field!r} must not be empty")
        if size > max_file_bytes:
            raise RelaynaJourneyError(
                f"multipart file {field!r} exceeds maxFileBytes {max_file_bytes}"
            )
        total_size += size
        if total_size > max_total_bytes:
            raise RelaynaJourneyError(f"multipart files exceed maxTotalBytes {max_total_bytes}")
        filename = _filename(item, path=path)
        content_type = _content_type(item)
        safe = {
            "field": field,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size,
            "required": required,
        }
        if include_digest:
            safe["sha256"] = _file_digest(path)
        metadata.append(safe)
        request_files.append((item, path, size))
    if not metadata:
        raise RelaynaJourneyError("multipart requires at least one readable file")
    if _multipart_encoded_size(multipart, files=request_files) > max_total_bytes:
        raise RelaynaJourneyError(f"multipart request exceeds maxTotalBytes {max_total_bytes}")
    return tuple(metadata)


def _multipart_encoded_size(
    multipart: dict[str, Any],
    *,
    files: list[tuple[dict[str, Any], Path, int]],
) -> int:
    """Return a conservative encoded request size without loading file contents."""

    boundary = f"ampule-{'0' * 32}"
    size = 0
    fields = multipart.get("fields", {})
    assert isinstance(fields, dict)
    unique_field = str(multipart.get("uniqueTaskIdField", "task_id")).strip()
    for name in sorted(fields):
        value = _serialize_multipart_field(fields[name], name=name)
        if unique_field and name == unique_field and value:
            value = f"{value}-{MAX_RELAYNA_ITERATIONS}-{'0' * 20}"
        size += len(f"--{boundary}\r\n".encode())
        size += len(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        size += len(value.encode("utf-8")) + len(b"\r\n")
    for item, path, payload_size in files:
        filename = _filename(item, path=path)
        content_type = _content_type(item)
        size += len(f"--{boundary}\r\n".encode())
        size += len(
            (
                f'Content-Disposition: form-data; name="{item["field"]}"; '
                f'filename="{_quoted_header(filename)}"\r\n'
            ).encode()
        )
        size += len(f"Content-Type: {content_type}\r\n\r\n".encode())
        size += payload_size + len(b"\r\n")
    return size + len(f"--{boundary}--\r\n".encode())


def _multipart_mapping(journey: dict[str, Any]) -> dict[str, Any]:
    multipart = journey.get("multipart")
    if not isinstance(multipart, dict):
        raise RelaynaJourneyError("multipart must be a mapping")
    return multipart


def _multipart_file_items(multipart: dict[str, Any]) -> list[dict[str, Any]]:
    raw = multipart.get("files")
    if not isinstance(raw, list):
        raise RelaynaJourneyError("multipart.files must be a list")
    if any(not isinstance(item, dict) for item in raw):
        raise RelaynaJourneyError("each multipart file must be a mapping")
    return raw


def _multipart_limits(multipart: dict[str, Any]) -> tuple[int, int]:
    max_file = _bounded_positive_int(
        multipart.get("maxFileBytes", MAX_MULTIPART_FILE_BYTES),
        "multipart.maxFileBytes",
        maximum=MAX_MULTIPART_FILE_BYTES,
    )
    max_total = _bounded_positive_int(
        multipart.get("maxTotalBytes", MAX_MULTIPART_TOTAL_BYTES),
        "multipart.maxTotalBytes",
        maximum=MAX_MULTIPART_TOTAL_BYTES,
    )
    return max_file, max_total


def _validated_file_path(item: dict[str, Any], *, workspace: Path | None) -> Path | None:
    required = bool(item.get("required", True))
    raw_path = item.get("path")
    if raw_path is None or raw_path == "":
        if required:
            raise RelaynaJourneyError(
                f"required multipart file {item.get('field', '<unknown>')!r} has no path"
            )
        return None
    if not isinstance(raw_path, str):
        raise RelaynaJourneyError("multipart file path must be a string")
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RelaynaJourneyError(f"multipart file path is not readable: {raw_path}") from exc
    if workspace is not None and not path.is_relative_to(workspace.resolve()):
        raise RelaynaJourneyError(
            f"multipart file path is outside the approved Chamber workspace: {raw_path}"
        )
    if not path.is_file() or not os.access(path, os.R_OK):
        raise RelaynaJourneyError(f"multipart file path is not readable: {raw_path}")
    return path


def _filename(item: dict[str, Any], *, path: Path) -> str:
    value = item.get("filename") or path.name
    if not isinstance(value, str) or not value.strip():
        raise RelaynaJourneyError("multipart filename must be a non-empty string")
    filename = value.strip()
    if Path(filename).name != filename or any(character in filename for character in "\r\n"):
        raise RelaynaJourneyError("multipart filename must not contain paths or newlines")
    return filename


def _content_type(item: dict[str, Any]) -> str:
    value = item.get("contentType")
    if not isinstance(value, str) or not value.strip():
        raise RelaynaJourneyError("multipart files require an explicit contentType")
    content_type = value.split(";", 1)[0].lower().strip()
    if content_type not in SUPPORTED_MULTIPART_CONTENT_TYPES:
        raise RelaynaJourneyError(f"unsupported multipart contentType: {content_type}")
    return content_type


def _serialize_multipart_field(value: Any, *, name: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict) and value.get("encoding") == "json" and "value" in value:
        if set(value) != {"encoding", "value"}:
            raise RelaynaJourneyError(
                f"multipart field {name!r} json descriptor supports encoding and value only"
            )
        payload = value["value"]
        if not isinstance(payload, dict | list):
            raise RelaynaJourneyError(
                f"multipart field {name!r} with json encoding requires an object or array value"
            )
        try:
            return json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise RelaynaJourneyError(
                f"multipart field {name!r} contains a value that is not valid JSON"
            ) from exc
    if isinstance(value, dict | list):
        raise RelaynaJourneyError(
            f"multipart field {name!r} objects and arrays require encoding: json and value"
        )
    raise RelaynaJourneyError(
        f"multipart field {name!r} must be a string, integer, boolean, or json descriptor"
    )


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quoted_header(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


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
            raise RelaynaTimeoutError(f"event stream exceeded {timeout_seconds}s timeout")
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


def _bounded_positive_int(value: Any, name: str, *, maximum: int) -> int:
    result = _positive_int(value, name)
    if result > maximum:
        raise RelaynaJourneyError(f"{name} must not exceed {maximum}")
    return result


def _looks_like_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
