"""Bounded open-loop HTTP/Relayna load suites and evidence-backed performance gates."""

from __future__ import annotations

import json
import math
import os
import random
import re
import resource
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from chamber.load.relayna import _execute_task, _json_path, _multipart_upload_metadata
from chamber.runs import write_json_atomic

LIMITS = {
    "p95Ms",
    "p99Ms",
    "maxErrorRate",
    "minCompletionRate",
    "maxDeadlineMissRate",
    "maxQueueWaitMs",
    "maxWorkerMs",
}
PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def number(value: Any, name: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number between {low} and {high}")
    return float(value)


def validate_suite(traffic: dict[str, Any]) -> None:
    spec = traffic.get("load")
    if spec is None:
        return
    if not isinstance(spec, dict) or spec.get("model", "arrival") not in {
        "arrival",
        "capacity",
        "soak",
    }:
        raise ValueError("traffic.load.model must be arrival, capacity or soak")
    if spec.get("phase", "load") not in {"baseline", "load", "fault", "recovery"}:
        raise ValueError("Invalid load phase")
    if not isinstance(traffic.get("journeys"), list) or not 1 <= len(traffic["journeys"]) <= 20:
        raise ValueError("Load suites require 1–20 journeys")
    validate_thresholds(spec)
    spec.setdefault("model", "arrival")
    spec.setdefault("ratePerSecond", 1)
    spec.setdefault("durationSeconds", 60)
    spec.setdefault("maxInFlight", 16)
    spec.setdefault("timeoutSeconds", 30)
    spec.setdefault("warmupSeconds", 0)
    spec.setdefault("recoverySeconds", 0)
    spec.setdefault("recoveryRate", 1)
    spec.setdefault("failureWindows", 2)
    spec.setdefault("seed", 1)
    for name, low, high in (
        ("ratePerSecond", 0.1, 1000),
        ("durationSeconds", 1, 3600),
        ("maxInFlight", 1, 128),
        ("timeoutSeconds", 1, 300),
        ("warmupSeconds", 0, 300),
        ("recoverySeconds", 0, 300),
        ("recoveryRate", 0.1, 1000),
        ("failureWindows", 1, 20),
        ("seed", 0, 2**32 - 1),
    ):
        number(spec[name], f"traffic.load.{name}", low, high)
    for name in ("maxInFlight", "failureWindows", "seed"):
        if type(spec[name]) is not int:
            raise ValueError(f"traffic.load.{name} must be an integer")
    number(spec.get("windowSeconds", 30), "windowSeconds", 1, 300)
    stages = spec.get(
        "stages",
        [{"ratePerSecond": spec["ratePerSecond"], "durationSeconds": spec["durationSeconds"]}],
    )
    if not isinstance(stages, list) or not 1 <= len(stages) <= 30:
        raise ValueError("Load stages must contain 1–30 rate/duration steps")
    for stage in stages:
        if not isinstance(stage, dict):
            raise ValueError("Load stage must be an object")
        number(stage.get("ratePerSecond"), "stage rate", 0.1, 1000)
        number(stage.get("durationSeconds"), "stage duration", 1, 3600)
    spec["stages"] = stages
    if (
        sum(s["durationSeconds"] for s in stages) + spec["warmupSeconds"] + spec["recoverySeconds"]
        > 3600
    ):
        raise ValueError("Total scheduled suite duration cannot exceed one hour")
    if (
        sum(math.ceil(s["durationSeconds"] * s["ratePerSecond"]) for s in stages)
        + math.ceil(spec["warmupSeconds"] * spec["ratePerSecond"])
        + math.ceil(spec["recoverySeconds"] * spec["recoveryRate"])
        > 100000
    ):
        raise ValueError("Suite cannot schedule more than 100000 iterations")
    for key in ("maxTargetMemoryGrowthMiB", "maxFinalQueueDepth"):
        if key in spec:
            number(spec[key], key, 0, 1e12)
    metrics = spec.get("targetMetrics", [])
    if not isinstance(metrics, list) or len(metrics) > 4:
        raise ValueError("targetMetrics must contain at most four scoped queries")
    metric_names: set[str] = set()
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ValueError("Target metric must be an object")
        if (
            metric.get("name") not in {"memoryBytes", "queueDepth", "cpuThrottling"}
            or not isinstance(metric.get("query"), str)
            or not 1 <= len(metric["query"]) <= 2000
        ):
            raise ValueError("Invalid target metric name or query")
        if not isinstance(metric.get("labels"), dict) or not metric["labels"]:
            raise ValueError("Target metrics need explicit workload/queue identity labels")
        if (
            metric["name"] in metric_names
            or not all(
                isinstance(k, str) and isinstance(v, str) and v for k, v in metric["labels"].items()
            )
            or "namespace" in metric["labels"]
        ):
            raise ValueError(
                "Metric names must be unique; labels are strings, namespace is supplied"
            )
        metric_names.add(metric["name"])
    if "maxGeneratorMemoryGrowthMiB" in spec:
        number(spec["maxGeneratorMemoryGrowthMiB"], "memory growth budget", 0, 65536)
    datasets = spec.get("datasets", {})
    if not isinstance(datasets, dict) or len(json.dumps(datasets).encode()) > 1024 * 1024:
        raise ValueError("Datasets must be an object up to 1 MiB")
    for rows in datasets.values():
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= 10000
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise ValueError("Each dataset needs 1–10000 object rows")
    names = set()
    for journey in traffic["journeys"]:
        if not isinstance(journey, dict):
            raise ValueError("Journey must be an object")
        if journey.get("followUps"):
            raise ValueError(
                "Load suites use executable steps/assertJson instead of legacy followUps"
            )
        validate_thresholds(journey)
        name = journey.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Suite journeys require unique names")
        names.add(name)
        if journey.get("adapter", "http") not in {"http", "relayna"}:
            raise ValueError("Suite adapter must be http or relayna")
        number(journey.get("weight", 1), "journey weight", 0.001, 1000)
        if journey.get("dataset") is not None and (
            not isinstance(journey["dataset"], str) or journey["dataset"] not in datasets
        ):
            raise ValueError("Unknown journey dataset")
        for key, value in {**spec.get("thresholds", {}), **journey.get("thresholds", {})}.items():
            if key not in LIMITS:
                raise ValueError(f"Unknown performance threshold {key}")
            number(value, key, 0, 1 if "Rate" in key else 3600000)
        steps = journey.get("steps", [journey])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
            raise ValueError("A journey requires 1–20 steps")
        if journey.get("adapter") == "relayna" and "steps" in journey:
            raise ValueError(
                "Relayna already models submission and lifecycle; use HTTP steps separately"
            )
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Journey step must be an object")
            for field in ("extract", "assertJson"):
                if not isinstance(step.get(field, {}), dict):
                    raise ValueError(f"{field} must be an object")
            if any(not isinstance(v, str) for v in step.get("extract", {}).values()):
                raise ValueError("Extraction paths must be strings")
            if (
                type(step.get("textBytes", 0)) is not int
                or not 0 <= step.get("textBytes", 0) <= 1024 * 1024
            ):
                raise ValueError("Generated textBytes must be an integer up to 1 MiB")
            status = step.get("expectedStatus", 200)
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("Expected status must be an HTTP status number")
            path = step.get("path", "")
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                raise ValueError("Suite steps must use paths on the selected target")
            if step.get("method", "GET") not in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            }:
                raise ValueError("Unsupported suite HTTP method")
            if step.get("requestEncoding", "json" if "body" in step else "none") not in {
                "none",
                "json",
                "form",
                "raw",
                "multipart",
            }:
                raise ValueError("Unsupported request encoding")
        if not isinstance(journey.get("headersFromEnv", {}), dict):
            raise ValueError("headersFromEnv must be an object")
        for name, env in journey.get("headersFromEnv", {}).items():
            if (
                not isinstance(name, str)
                or not isinstance(env, str)
                or not re.fullmatch(r"[A-Za-z0-9-]+", name)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env)
            ):
                raise ValueError(
                    "Authentication requires header names mapped to environment variable names"
                )


def validate_thresholds(owner: dict[str, Any]) -> None:
    phases = owner.get("phaseThresholds", {})
    if not isinstance(phases, dict) or any(
        key not in {"warmup", "baseline", "load", "fault", "recovery"} for key in phases
    ):
        raise ValueError("phaseThresholds must map known phases to threshold objects")
    for thresholds in [owner.get("thresholds", {}), *phases.values()]:
        if not isinstance(thresholds, dict):
            raise ValueError("Thresholds must be an object")
        for key, value in thresholds.items():
            if key not in LIMITS:
                raise ValueError(f"Unknown performance threshold {key}")
            number(value, key, 0, 1 if "Rate" in key else 3600000)


def expand(value: Any, variables: dict[str, Any], *, path: bool = False) -> Any:
    if isinstance(value, dict):
        return {key: expand(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, variables) for item in value]
    if not isinstance(value, str):
        return value
    match = PLACEHOLDER.fullmatch(value)

    def lookup(key: str) -> Any:
        result = _json_path(variables, key)
        if result is None:
            raise ValueError("Missing dataset or extracted variable")
        return result

    if match and not path:
        return deepcopy(lookup(match[1]))
    return PLACEHOLDER.sub(
        lambda m: quote(str(lookup(m[1])), safe="") if path else str(lookup(m[1])), value
    )


def sized_body(body: Any, size: int) -> Any:
    if not size:
        return body
    if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"]:
        raise ValueError("Generated text requires a nonempty body.text string")
    encoded = body["text"].encode()
    text = (encoded * math.ceil(size / len(encoded)))[:size].decode("utf-8", errors="ignore")
    return {**body, "text": text + " " * (size - len(text.encode()))}


def percentile(values: list[float], fraction: float) -> float | None:
    return (
        round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3) if values else None
    )


def measurements(rows: list[dict[str, Any]], requested: int, dropped: int) -> dict[str, Any]:
    durations = [row["total_ms"] for row in rows]
    successful = sum(row["success"] for row in rows)
    return {
        "requested": requested,
        "started": len(rows),
        "completed": len(rows),
        "successful": successful,
        "dropped": dropped,
        "p95Ms": percentile(durations, 0.95),
        "p99Ms": percentile(durations, 0.99),
        "errorRate": 1 - successful / len(rows) if rows else None,
        "completionRate": successful / requested if requested else None,
        "deadlineMissRate": sum(row["deadline_missed"] for row in rows) / len(rows)
        if rows
        else None,
        "queueWaitP95Ms": percentile(
            [row["queue_wait_ms"] for row in rows if row.get("queue_wait_ms") is not None], 0.95
        ),
        "workerP95Ms": percentile(
            [row["worker_ms"] for row in rows if row.get("worker_ms") is not None], 0.95
        ),
        "admissionP95Ms": percentile(
            [row["admission_ms"] for row in rows if row.get("admission_ms") is not None], 0.95
        ),
        "queueTimingSamples": sum(row.get("queue_wait_ms") is not None for row in rows),
        "workerTimingSamples": sum(row.get("worker_ms") is not None for row in rows),
    }


def assertions(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    fields = {
        "maxErrorRate": "errorRate",
        "minCompletionRate": "completionRate",
        "maxDeadlineMissRate": "deadlineMissRate",
        "maxQueueWaitMs": "queueWaitP95Ms",
        "maxWorkerMs": "workerP95Ms",
    }
    for key, expected in thresholds.items():
        observed = metrics.get(fields.get(key, key))
        missing = (
            observed is None
            or (key == "maxQueueWaitMs" and metrics["queueTimingSamples"] != metrics["started"])
            or (key == "maxWorkerMs" and metrics["workerTimingSamples"] != metrics["started"])
        )
        passed = not missing and (
            observed >= expected if key == "minCompletionRate" else observed <= expected
        )
        result.append(
            {
                "name": key,
                "expected": expected,
                "observed": observed,
                "state": "missing" if missing else "pass" if passed else "fail",
            }
        )
    return result


class DeadlineResponse:
    """Bound capture and reapply the remaining deadline before every socket read."""

    def __init__(self, response: Any, deadline: float):
        self.response, self.deadline = response, deadline
        self.status = response.status

    def chunk(self, size: int) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Journey deadline exceeded")
        if self.response.fp is not None:
            self.response.fp.raw._sock.settimeout(remaining)
        return self.response.read1(size)

    def read(self, size: int = 1024 * 1024 + 1) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.chunk(min(65536, size - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > 1024 * 1024:
            raise ValueError("Response exceeded bounded capture limit")
        return bytes(data)

    def __iter__(self) -> Iterator[bytes]:
        pending = b""
        total = 0
        while chunk := self.chunk(4096):
            total += len(chunk)
            if total > 1024 * 1024:
                raise ValueError("Event stream exceeded bounded capture limit")
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line + b"\n"
        if pending:
            yield pending


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # Never forward target credentials or traffic to a redirected origin.
        return None


class Generator:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak_active = 0
        self.peak_lag = 0.0
        self.started = time.monotonic()
        self.cpu = time.process_time()
        self.initial_memory = self.memory()
        self.samples: list[dict[str, Any]] = []
        self.target_samples: list[dict[str, Any]] = []

    @staticmethod
    def memory() -> float:
        # ru_maxrss is a high-water mark, explicitly labelled, not current RSS.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
            1024**2 if sys.platform == "darwin" else 1024
        )

    def sample(self, lag: float, in_flight: int) -> None:
        now = time.monotonic()
        elapsed = now - self.started
        cpu_seconds = time.process_time() - self.cpu
        previous = self.samples[-1] if self.samples else {"elapsedSeconds": 0, "cpuSeconds": 0}
        cpu_percent = (
            100
            * (cpu_seconds - previous["cpuSeconds"])
            / max(0.001, elapsed - previous["elapsedSeconds"])
        )
        self.samples.append(
            {
                "cpuPercentOneCore": round(cpu_percent, 3),
                "elapsedSeconds": round(now - self.started, 3),
                "cpuSeconds": round(time.process_time() - self.cpu, 4),
                "rssHighWaterMiB": round(self.memory(), 3),
                "inFlight": in_flight,
                "activeRequests": self.active,
                "schedulingLagMs": round(lag * 1000, 3),
            }
        )

    def sample_targets(
        self, metrics: list[dict[str, Any]], url: str | None, namespace: str
    ) -> None:
        sample: dict[str, Any] = {
            "elapsedSeconds": round(time.monotonic() - self.started, 3),
            "values": {},
            "errors": [],
        }
        for metric in metrics:
            try:
                if not url:
                    raise ValueError("Prometheus URL is missing")
                query_url = (
                    url.rstrip("/") + "/api/v1/query?" + urlencode({"query": metric["query"]})
                )
                with urlopen(query_url, timeout=2) as response:
                    payload = json.loads(response.read(1024 * 1024))
                series = payload["data"]["result"]
                if payload.get("status") != "success" or len(series) != 1:
                    raise ValueError("Expected one series")
                item = series[0]
                identity = {**metric["labels"], "namespace": namespace}
                if not namespace or any(item["metric"].get(k) != v for k, v in identity.items()):
                    raise ValueError("Wrong target identity")
                timestamp, value = map(float, item["value"])
                if (
                    not math.isfinite(timestamp)
                    or not 0 <= time.time() - timestamp <= 15
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Stale or nonfinite metric")
                sample["values"][metric["name"]] = value
            except Exception:
                sample["errors"].append(metric["name"])
        self.target_samples.append(sample)

    def opener(self, request: Request, **kwargs: Any) -> Any:
        monitor = self
        deadline = kwargs.pop("deadline")

        class Response:
            def __enter__(self) -> Any:
                with monitor.lock:
                    monitor.active += 1
                    monitor.peak_active = max(monitor.peak_active, monitor.active)
                try:
                    self.response = build_opener(NoRedirect()).open(request, **kwargs)
                    return DeadlineResponse(self.response, deadline)
                except BaseException:
                    with monitor.lock:
                        monitor.active -= 1
                    raise

            def __exit__(self, *args: Any) -> None:
                self.response.close()
                with monitor.lock:
                    monitor.active -= 1

        return Response()


def execute_iteration(
    journey: dict[str, Any],
    *,
    base_url: str,
    variables: dict[str, Any],
    iteration: int,
    timeout: float,
    workspace: Path | None,
    generator: Generator,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout
    result: dict[str, Any] = {
        "journey": journey["name"],
        "success": False,
        "deadline_missed": False,
        "queue_wait_ms": None,
        "worker_ms": None,
        "admission_ms": None,
        "timing_source": "unavailable",
    }
    try:
        headers = {key: os.environ[env] for key, env in journey.get("headersFromEnv", {}).items()}
        if any("\n" in value or "\r" in value for value in headers.values()):
            raise ValueError("Invalid authentication header")

        def opener(request: Request, **kwargs: Any) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Journey deadline exceeded")
            for key, value in headers.items():
                request.add_header(key, value)
            kwargs["timeout"] = min(float(kwargs.get("timeout", remaining)), remaining)
            return generator.opener(request, deadline=deadline, **kwargs)

        if journey.get("adapter") == "relayna":
            resolved = expand(journey, variables)
            resolved["body"] = sized_body(resolved.get("body"), resolved.get("textBytes", 0))
            resolved["path"] = expand(journey["path"], variables, path=True)
            resolved.setdefault("relayna", {})["timeoutSeconds"] = min(
                math.ceil(timeout), resolved.get("relayna", {}).get("timeoutSeconds", 300)
            )
            task = _execute_task(
                resolved,
                base_url=base_url,
                iteration=iteration,
                opener=opener,
                workspace=workspace,
                upload_metadata=_multipart_upload_metadata(
                    resolved, workspace=workspace, include_digest=True
                ),
            )
            task_data = asdict(task)
            # Omit response content, extracted/auth values and raw exception text.
            task_data["error"] = "lifecycle_failed" if task.error else None
            result.update(
                success=task.success, admission_ms=task.submission_duration_ms, task=task_data
            )
            starts = [
                event.get("received_elapsed_ms")
                for event in task.events
                if event.get("status") in journey.get("runningStatuses", ["running", "processing"])
            ]
            ends = [
                event.get("received_elapsed_ms")
                for event in task.events
                if event.get("status")
                in resolved.get("relayna", {}).get("successStatuses", ["completed"])
            ]
            if (
                starts
                and ends
                and isinstance(starts[0], (int, float))
                and isinstance(ends[-1], (int, float))
                and ends[-1] >= starts[0]
            ):
                result.update(
                    queue_wait_ms=starts[0],
                    worker_ms=ends[-1] - starts[0],
                    timing_source="client_observed_stream_events",
                )
            task_data["events"] = task_data["events"][:32]
            task_data["events_truncated"] = task.event_count > 32
            result["deadline_missed"] = task.failure_stage == "timeout"
        else:
            for step in journey.get("steps", [journey]):
                path = expand(step["path"], variables, path=True)
                if not path.startswith("/") or path.startswith("//"):
                    raise ValueError("Invalid target path")
                body = sized_body(expand(step.get("body"), variables), step.get("textBytes", 0))
                encoding = step.get("requestEncoding", "json" if body is not None else "none")
                request_headers = {"Accept": "application/json"}
                if encoding == "json":
                    body = json.dumps(body).encode() if body is not None else None
                    request_headers["Content-Type"] = "application/json"
                elif encoding == "form":
                    body = urlencode(expand(step.get("form", {}), variables)).encode()
                    request_headers["Content-Type"] = "application/x-www-form-urlencoded"
                elif encoding == "raw":
                    body = str(body or "").encode()
                    request_headers["Content-Type"] = step.get("contentType", "text/plain")
                elif encoding == "multipart":
                    from chamber.load.relayna import _multipart_body

                    body, content_type = _multipart_body(
                        expand(step, variables), iteration=iteration, workspace=workspace
                    )
                    request_headers["Content-Type"] = content_type
                else:
                    body = None
                request = Request(
                    base_url.rstrip("/") + path,
                    data=body,
                    headers=request_headers,
                    method=step.get("method", "GET"),
                )
                try:
                    with opener(request) as response:
                        status = response.status
                        raw = response.read(1024 * 1024 + 1)
                except HTTPError as exc:
                    status = exc.code
                    try:
                        raw = DeadlineResponse(exc, deadline).read()
                    finally:
                        exc.close()
                if status != step.get("expectedStatus", 200):
                    raise ValueError("Unexpected HTTP status")
                if len(raw) > 1024 * 1024:
                    raise ValueError("Response exceeded bounded capture limit")
                if step.get("extract") or step.get("assertJson"):
                    payload = json.loads(raw)
                    for key, path_value in step.get("extract", {}).items():
                        value = _json_path(payload, path_value)
                        if value is None:
                            raise ValueError("Extraction did not match")
                        variables[key] = value
                    for path_value, expected in step.get("assertJson", {}).items():
                        if _json_path(payload, path_value) != expand(expected, variables):
                            raise ValueError("Business assertion failed")
            result["success"] = True
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["deadline_missed"] = isinstance(exc, TimeoutError)
    result["finished_monotonic"] = time.monotonic()
    result["total_ms"] = round((time.monotonic() - started) * 1000, 3)
    if time.monotonic() > deadline:
        result.update(success=False, deadline_missed=True)
    return result


def _window_result(
    stage: dict[str, Any], journeys: list[dict[str, Any]], spec: dict[str, Any]
) -> dict[str, Any]:
    rows = stage.pop("rows")
    counts = stage.pop("counts")
    per_journey: list[dict[str, Any]] = []
    for journey in journeys:
        name = journey["name"]
        metrics = measurements([row for row in rows if row["journey"] == name], **counts[name])
        thresholds = {
            "maxErrorRate": 0,
            **spec.get("thresholds", {}),
            **journey.get("thresholds", {}),
            **spec.get("phaseThresholds", {}).get(stage["phase"], {}),
            **journey.get("phaseThresholds", {}).get(stage["phase"], {}),
        }
        checks = assertions(metrics, thresholds)
        if counts[name]["requested"] == 0:
            checks.append(
                {"name": "Workload exercised", "expected": ">0", "observed": 0, "state": "missing"}
            )
        per_journey.append({"name": name, "metrics": metrics, "assertions": checks})
    checks = [a for journey in per_journey for a in journey["assertions"]]
    requested = sum(c["requested"] for c in counts.values())
    dropped = sum(c["dropped"] for c in counts.values())
    incomplete = dropped > 0 or any(a["state"] == "missing" for a in checks)
    passing = bool(rows) and all(a["state"] == "pass" for a in checks)
    return {
        **stage,
        "requested": requested,
        "started": requested - dropped,
        "completed": len(rows),
        "achievedStartsPerSecond": round((requested - dropped) / stage["durationSeconds"], 3),
        "successfulCompletionsPerSecond": round(
            sum(r["success"] for r in rows) / max(stage["actualSeconds"], 0.001), 3
        ),
        "state": "inconclusive" if incomplete else "pass" if passing else "fail",
        "journeys": per_journey,
    }


def execute_suite(
    traffic: dict[str, Any],
    *,
    base_url: str,
    output: Path,
    workspace: Path | None = None,
    prometheus_url: str | None = None,
    namespace: str = "",
) -> dict[str, Any]:
    validate_suite(traffic)
    spec = traffic["load"]
    journeys = traffic["journeys"]
    for journey in journeys:
        for env in journey.get("headersFromEnv", {}).values():
            if not os.environ.get(env) or any(c in os.environ[env] for c in "\r\n"):
                raise ValueError(
                    f"Required load authentication environment variable is unavailable: {env}"
                )
    generator = Generator()
    randomizer = random.Random(spec["seed"])
    stages: list[dict[str, Any]] = []
    if spec["warmupSeconds"]:
        stages.append(
            {
                "phase": "warmup",
                "ratePerSecond": spec["ratePerSecond"],
                "durationSeconds": spec["warmupSeconds"],
            }
        )
    for i, stage in enumerate(spec["stages"]):
        remaining = stage["durationSeconds"]
        while remaining > 0:
            duration = (
                min(remaining, spec.get("windowSeconds", 30))
                if spec["model"] == "soak"
                else remaining
            )
            stages.append(
                {
                    **stage,
                    "durationSeconds": duration,
                    "phase": spec.get("phase", "load"),
                    "step": i + 1,
                }
            )
            remaining -= duration
    if spec["recoverySeconds"]:
        stages.append(
            {
                "phase": "recovery",
                "ratePerSecond": spec["recoveryRate"],
                "durationSeconds": spec["recoverySeconds"],
            }
        )
    all_rows: list[dict[str, Any]] = []
    windows: list[dict[str, Any]] = []
    executed: list[dict[str, Any]] = []
    totals = {journey["name"]: {"requested": 0, "dropped": 0} for journey in journeys}
    failures = 0
    stop_reason = None
    pending: dict[Future, dict[str, Any]] = {}
    sequence = 0
    last_sample = 0.0

    def collect(wait: bool = False) -> None:
        for future in list(pending):
            if wait or future.done():
                row = future.result()
                owner = pending.pop(future)
                row.update(phase=owner["phase"], step=owner.get("step", 0))
                owner["rows"].append(row)
                if len(all_rows) >= 1000:
                    row.pop("task", None)
                all_rows.append(row)
                owner["lastCompletionSeconds"] = max(
                    owner.get("lastCompletionSeconds", 0),
                    row["finished_monotonic"] - owner["start"],
                )

    monitor_stop = threading.Event()
    monitor: threading.Thread | None = None
    target_metrics = spec.get("targetMetrics", [])
    if target_metrics:
        generator.sample_targets(target_metrics, prometheus_url, namespace)

        def observe_targets() -> None:
            while not monitor_stop.wait(5):
                generator.sample_targets(target_metrics, prometheus_url, namespace)

        monitor = threading.Thread(target=observe_targets, daemon=True)
        monitor.start()
    planned_start = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=spec["maxInFlight"]) as pool:
            for stage in stages:
                if stop_reason and stage["phase"] != "recovery":
                    continue
                stage.update(
                    start=planned_start,
                    rows=[],
                    counts={j["name"]: {"requested": 0, "dropped": 0} for j in journeys},
                )
                executed.append(stage)
                scheduled = math.ceil(stage["ratePerSecond"] * stage["durationSeconds"])
                for slot in range(scheduled):
                    target = planned_start + slot / stage["ratePerSecond"]
                    remaining = target - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    now = time.monotonic()
                    lag = max(0, now - target)
                    generator.peak_lag = max(generator.peak_lag, lag)
                    collect()
                    selected = randomizer.choices(
                        journeys, weights=[j.get("weight", 1) for j in journeys], k=1
                    )[0]
                    name = selected["name"]
                    stage["counts"][name]["requested"] += 1
                    totals[name]["requested"] += 1
                    if len(pending) >= spec["maxInFlight"] or lag > max(
                        0.05, 1 / stage["ratePerSecond"]
                    ):
                        stage["counts"][name]["dropped"] += 1
                        totals[name]["dropped"] += 1
                    else:
                        dataset = spec.get("datasets", {}).get(selected.get("dataset"), [{}])
                        variables = deepcopy(randomizer.choice(dataset))
                        sequence += 1
                        variables["iteration"] = sequence
                        future = pool.submit(
                            execute_iteration,
                            selected,
                            base_url=base_url,
                            variables=variables,
                            iteration=sequence,
                            timeout=spec["timeoutSeconds"],
                            workspace=workspace,
                            generator=generator,
                        )
                        pending[future] = stage
                    if now - last_sample >= 1:
                        generator.sample(lag, len(pending))
                        last_sample = now
                planned_start += stage["durationSeconds"]
                if spec["model"] == "capacity":
                    remaining = planned_start - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    collect(wait=True)
                    stage["actualSeconds"] = max(
                        stage["durationSeconds"], stage.get("lastCompletionSeconds", 0)
                    )
                    window = _window_result(
                        {
                            k: v
                            for k, v in stage.items()
                            if k not in {"start", "lastCompletionSeconds"}
                        },
                        journeys,
                        spec,
                    )
                    windows.append(window)
                    if stage["phase"] not in {"warmup", "recovery"}:
                        failures = 0 if window["state"] == "pass" else failures + 1
                        if failures >= spec["failureWindows"]:
                            stop_reason = "Sustained performance or generator delivery failure"
                    planned_start = time.monotonic()
            remaining = planned_start - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            collect(wait=True)
    finally:
        monitor_stop.set()
        if monitor:
            monitor.join()
    if spec["model"] != "capacity":
        for stage in executed:
            stage["actualSeconds"] = max(
                stage["durationSeconds"], stage.get("lastCompletionSeconds", 0)
            )
            windows.append(
                _window_result(
                    {k: v for k, v in stage.items() if k not in {"start", "lastCompletionSeconds"}},
                    journeys,
                    spec,
                )
            )
    if target_metrics:
        generator.sample_targets(target_metrics, prometheus_url, namespace)
    generator.sample(0, 0)
    total_requested = sum(t["requested"] for t in totals.values())
    dropped = sum(t["dropped"] for t in totals.values())
    aggregate = measurements(all_rows, total_requested, dropped)
    observed = [w for w in windows if w["phase"] != "warmup"]
    states = [w["state"] for w in observed]
    memory_growth = max(0, generator.memory() - generator.initial_memory)
    memory_limit = spec.get("maxGeneratorMemoryGrowthMiB")
    memory_check = {
        "name": "Generator RSS high-water growth MiB",
        "expected": memory_limit,
        "observed": round(memory_growth, 3),
        "state": "not_configured"
        if memory_limit is None
        else "pass"
        if memory_growth <= memory_limit
        else "fail",
    }
    limited = dropped > 0 or memory_check["state"] == "fail"
    status = (
        "inconclusive"
        if limited or "inconclusive" in states
        else "pass"
        if states and all(s == "pass" for s in states)
        else "fail"
    )
    load_windows = [w for w in observed if w["phase"] != "recovery"]
    throughput_change = (
        load_windows[-1]["successfulCompletionsPerSecond"]
        - load_windows[0]["successfulCompletionsPerSecond"]
        if len(load_windows) > 1
        else None
    )
    target_assertions = []
    for key, metric in (
        ("maxTargetMemoryGrowthMiB", "memoryBytes"),
        ("maxFinalQueueDepth", "queueDepth"),
    ):
        if key not in spec:
            continue
        samples = generator.target_samples
        complete = len(samples) >= 2 and all(metric in sample["values"] for sample in samples)
        value = None
        if complete:
            value = (
                max(0, (samples[-1]["values"][metric] - samples[0]["values"][metric]) / 1024**2)
                if metric == "memoryBytes"
                else samples[-1]["values"][metric]
            )
        target_assertions.append(
            {
                "name": key,
                "expected": spec[key],
                "observed": value,
                "state": "missing" if value is None else "pass" if value <= spec[key] else "fail",
            }
        )
    if any(sample["errors"] for sample in generator.target_samples) or any(
        a["state"] == "missing" for a in target_assertions
    ):
        status = "inconclusive"
    elif status == "pass" and any(a["state"] == "fail" for a in target_assertions):
        status = "fail"
    summary = {
        "targetMetrics": {"samples": generator.target_samples, "assertions": target_assertions},
        "schema_version": "chamber.ampule.dev/load-suite/v1",
        "adapter": "mixed",
        "status": status,
        "success": status == "pass",
        "metrics": aggregate,
        "windows": windows,
        "generator": {
            "samples": generator.samples,
            "peakActiveRequests": generator.peak_active,
            "peakSchedulingLagMs": round(generator.peak_lag * 1000, 3),
            "rssHighWaterGrowthMiB": round(memory_growth, 3),
            "limited": limited,
            "memoryAssertion": memory_check,
        },
        "capacity": {
            "highestPassingRate": max(
                (w["ratePerSecond"] for w in load_windows if w["state"] == "pass"), default=None
            ),
            "stopReason": stop_reason,
        },
        "soak": {"throughputChangePerSecond": throughput_change, "windowCount": len(load_windows)},
        "tasks": [r["task"] for r in all_rows if "task" in r][:1000],
        "observations": [
            {k: v for k, v in row.items() if k not in {"task", "finished_monotonic"}}
            for row in all_rows[:1000]
        ],
        "observationsTruncated": len(all_rows) > 1000,
        "timingNote": (
            "Lifecycle intervals use client-observed stream transitions; missing transitions "
            "stay unavailable. Generator memory is RSS high-water growth."
        ),
        "seed": spec["seed"],
    }
    write_json_atomic(output, summary)
    return summary
