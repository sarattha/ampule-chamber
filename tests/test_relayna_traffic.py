from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any

from chamber.application import build_assessment_result
from chamber.load import (
    RelaynaJourneyError,
    execute_relayna_journeys,
    validate_relayna_journey,
)
from chamber.runs import refresh_evidence_manifest


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


class RelaynaTrafficTests(unittest.TestCase):
    def test_submit_extract_stream_and_terminal_completion(self) -> None:
        requested_urls: list[str] = []
        requested_timeouts: list[int] = []

        def opener(request: Any, *, timeout: int) -> _Response:
            requested_urls.append(request.full_url)
            requested_timeouts.append(timeout)
            self.assertGreater(timeout, 0)
            if request.method == "POST":
                self.assertEqual(
                    json.loads(request.data), {"text": "Hello", "language_target": "Thai"}
                )
                return _Response(b'{"task_id":"task/123","task_ids":["task/123"]}', status=202)
            return _Response(
                b"event: ready\ndata: {}\n\n"
                b'event: status\ndata: {"task_id":"task/123","status":"queued"}\n\n'
                b'event: status\ndata: {"task_id":"task/123","status":"completed"}\n\n'
            )

        summary = execute_relayna_journeys(
            (_journey(),),
            base_url="http://127.0.0.1:8080",
            opener=opener,
        )

        self.assertTrue(summary["success"])
        self.assertEqual(summary["completed_count"], 1)
        self.assertEqual(summary["tasks"][0]["task_id"], "task/123")
        self.assertEqual(summary["tasks"][0]["statuses"], ("queued", "completed"))
        self.assertEqual(
            requested_urls,
            [
                "http://127.0.0.1:8080/translations",
                "http://127.0.0.1:8080/events/task%2F123",
            ],
        )
        self.assertEqual(requested_timeouts, [30, 30])

    def test_failed_terminal_status_fails_journey(self) -> None:
        def opener(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                return _Response(b'{"task_id":"task-1"}', status=202)
            return _Response(b'event: status\ndata: {"task_id":"task-1","status":"failed"}\n\n')

        summary = execute_relayna_journeys(
            (_journey(),),
            base_url="http://service",
            opener=opener,
        )

        self.assertFalse(summary["success"])
        self.assertEqual(summary["failed_count"], 1)
        self.assertIn("failed", summary["tasks"][0]["error"])

    def test_invalid_events_path_is_rejected_before_execution(self) -> None:
        journey = _journey()
        journey["relayna"]["eventsPath"] = "/events/static"

        with self.assertRaisesRegex(RelaynaJourneyError, "task_id"):
            execute_relayna_journeys(
                (journey,),
                base_url="http://service",
                opener=lambda *_args, **_kwargs: _Response(b""),
            )

    def test_relayna_summary_satisfies_traffic_evidence_requirement(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            evidence = run_dir / "evidence"
            evidence.mkdir()
            for name in ("preflight.json", "kubernetes-commands.json", "relayna-summary.json"):
                (evidence / name).write_text("{}", encoding="utf-8")
            refresh_evidence_manifest(run_dir)

            result = build_assessment_result(
                run_dir,
                config={
                    "runtime": {"provider": "kubernetes", "mode": "deploy"},
                    "traffic": {"journeys": [{"adapter": "relayna"}]},
                },
                metadata={
                    "stage": "assessed",
                    "mode": "kubernetes",
                    "success": True,
                    "cleanup_performed": True,
                    "traffic_result": {"success": True},
                },
                findings=(),
            )

        self.assertEqual(result["status"], "ready")
        self.assertIn("relayna-summary", result["required_evidence_ids"])
        self.assertNotIn("k6-summary", result["required_evidence_ids"])

    def test_mixed_adapters_are_rejected(self) -> None:
        with self.assertRaises(RelaynaJourneyError):
            execute_relayna_journeys(
                (_journey(), {"adapter": "http"}),
                base_url="http://service",
                opener=lambda *_args, **_kwargs: _Response(b""),
            )

    def test_nested_task_id_path_and_multiple_iterations(self) -> None:
        submitted_bodies: list[dict[str, Any]] = []

        def opener(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                body = json.loads(request.data)
                submitted_bodies.append(body)
                return _Response(
                    json.dumps({"data": {"task_id": body["task_id"]}}).encode(), status=202
                )
            task_id = request.full_url.rsplit("/", 1)[-1]
            return _Response(
                f'event: status\ndata: {{"task_id":"{task_id}","status":"completed"}}\n\n'.encode()
            )

        journey = _journey()
        journey["body"]["task_id"] = "ampule"
        journey["iterations"] = 2
        journey["vus"] = 2
        journey["relayna"]["taskIdPath"] = "data.task_id"

        summary = execute_relayna_journeys((journey,), base_url="http://service", opener=opener)

        self.assertTrue(summary["success"])
        self.assertEqual(summary["task_count"], 2)
        self.assertEqual(len({item["task_id"] for item in submitted_bodies}), 2)
        self.assertTrue(all(item["task_id"].startswith("ampule-") for item in submitted_bodies))

    def test_submission_and_stream_failures_are_recorded(self) -> None:
        cases = (
            (
                "unexpected submission status",
                lambda request, timeout: _Response(b"{}", status=500),
                "expected 202",
            ),
            (
                "non-json submission",
                lambda request, timeout: _Response(b"not-json", status=202),
                "not JSON",
            ),
            (
                "missing task id",
                lambda request, timeout: _Response(b"{}", status=202),
                "no task id",
            ),
        )
        for label, opener, expected in cases:
            with self.subTest(label=label):
                summary = execute_relayna_journeys(
                    (_journey(),), base_url="http://service", opener=opener
                )
                self.assertFalse(summary["success"])
                self.assertIn(expected, summary["tasks"][0]["error"])

        def stream_failure(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                return _Response(b'{"task_id":"task-1"}', status=202)
            return _Response(b"not found", status=404)

        summary = execute_relayna_journeys(
            (_journey(),), base_url="http://service", opener=stream_failure
        )
        self.assertIn("event stream returned HTTP 404", summary["tasks"][0]["error"])

    def test_stream_without_terminal_status_is_recorded(self) -> None:
        def opener(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                return _Response(b'{"task_id":"task-1"}', status=202)
            return _Response(b': keepalive\n\ndata: not-json\n\ndata: {"status":"queued"}\n\n')

        summary = execute_relayna_journeys((_journey(),), base_url="http://service", opener=opener)

        self.assertFalse(summary["success"])
        self.assertEqual(summary["tasks"][0]["statuses"], ("queued",))
        self.assertEqual(summary["tasks"][0]["event_count"], 2)
        self.assertIn("without a terminal status", summary["tasks"][0]["error"])

    def test_relayna_contract_validation_rejects_invalid_shapes(self) -> None:
        mutations = (
            (lambda item: item.update(method="GET"), "method"),
            (lambda item: item.update(path="translations"), "absolute"),
            (lambda item: item.update(expectedStatus="202"), "expectedStatus"),
            (lambda item: item.update(body={}), "non-empty JSON body"),
            (lambda item: item.update(iterations=0), "iterations"),
            (lambda item: item.update(vus=True), "vus"),
            (lambda item: item.update(relayna=[]), "mapping"),
            (
                lambda item: item["relayna"].update(terminalStatuses=[]),
                "status list",
            ),
            (
                lambda item: item["relayna"].update(
                    terminalStatuses=["failed"], successStatuses=["completed"]
                ),
                "also be terminal",
            ),
            (
                lambda item: item["relayna"].update(timeoutSeconds=0),
                "timeoutSeconds",
            ),
        )
        for mutate, expected in mutations:
            journey = _journey()
            mutate(journey)
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(RelaynaJourneyError, expected):
                    validate_relayna_journey(journey)


def _journey() -> dict[str, Any]:
    return {
        "name": "translation-lifecycle",
        "adapter": "relayna",
        "path": "/translations",
        "expectedStatus": 202,
        "body": {"text": "Hello", "language_target": "Thai"},
        "iterations": 1,
        "vus": 1,
        "relayna": {
            "taskIdPath": "task_id",
            "eventsPath": "/events/{task_id}",
            "terminalStatuses": ["completed", "failed"],
            "successStatuses": ["completed"],
            "timeoutSeconds": 30,
        },
    }


if __name__ == "__main__":
    unittest.main()
