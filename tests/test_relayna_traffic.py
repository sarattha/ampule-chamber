from __future__ import annotations

import io
import json
import unittest
from email.parser import BytesParser
from email.policy import default
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import TracebackType
from typing import Any

import chamber.workflow as workflow
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
        self.assertEqual(summary["tasks"][0]["failure_stage"], "terminal_failure")

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

    def test_report_section_contains_safe_upload_metadata_only(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            evidence = run_dir / "evidence"
            evidence.mkdir()
            (evidence / "relayna-summary.json").write_text(
                json.dumps(
                    {
                        "inputs": [
                            {
                                "journey": "document-lifecycle",
                                "files": [
                                    {
                                        "field": "file",
                                        "filename": "document.png",
                                        "content_type": "image/png",
                                        "size_bytes": 12,
                                        "sha256": "abc123",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            sections = workflow._relayna_input_sections(run_dir)

        self.assertEqual(sections[0].heading, "Relayna Upload Inputs")
        self.assertIn("filename=document.png", sections[0].lines[0])
        self.assertIn("sha256=abc123", sections[0].lines[0])
        self.assertNotIn("path=", sections[0].lines[0])

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

    def test_multipart_submit_serializes_fields_files_and_safe_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            document = workspace / "document.png"
            roi = workspace / "roi.png"
            document.write_bytes(b"PNG-DOCUMENT-SECRET")
            roi.write_bytes(b"PNG-ROI-SECRET")
            journey = _multipart_journey(document, roi=roi)
            submitted_fields: dict[str, str] = {}
            submitted_files: dict[str, tuple[str, str, bytes]] = {}

            def opener(request: Any, *, timeout: int) -> _Response:
                del timeout
                if request.method == "POST":
                    submitted_fields.update(_multipart_fields(request))
                    submitted_files.update(_multipart_files(request))
                    return _Response(
                        b'{"task_id":"parent-task","task_ids":["child-1","child-2"]}',
                        status=202,
                    )
                return _Response(
                    b'event: status\ndata: {"status":"queued"}\n\n'
                    b'event: status\ndata: {"status":"completed"}\n\n'
                )

            summary = execute_relayna_journeys(
                (journey,),
                base_url="http://service",
                opener=opener,
                workspace=workspace,
            )

        self.assertTrue(summary["success"])
        self.assertEqual(summary["tasks"][0]["task_id"], "parent-task")
        self.assertEqual(summary["tasks"][0]["failure_stage"], None)
        self.assertTrue(submitted_fields["task_id"].startswith("document-smoke-1-"))
        self.assertEqual(submitted_fields["priority"], "5")
        self.assertEqual(submitted_fields["strict_mode"], "false")
        self.assertEqual(
            submitted_fields["extraction_fields"],
            '[{"name":"document_number","type":"string"}]',
        )
        self.assertEqual(
            submitted_fields["processing_config"],
            '{"engine":"internal","fallback":true}',
        )
        self.assertEqual(
            submitted_files,
            {
                "file": ("document.png", "image/png", b"PNG-DOCUMENT-SECRET"),
                "roi": ("roi-mask.png", "image/png", b"PNG-ROI-SECRET"),
            },
        )
        uploads = summary["tasks"][0]["uploads"]
        self.assertEqual([item["field"] for item in uploads], ["file", "roi"])
        self.assertEqual(uploads[0]["sha256"], sha256(b"PNG-DOCUMENT-SECRET").hexdigest())
        evidence_text = json.dumps(summary)
        self.assertNotIn("PNG-DOCUMENT-SECRET", evidence_text)
        self.assertNotIn(str(document), evidence_text)

    def test_multipart_optional_file_can_be_omitted(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            document = workspace / "document.png"
            document.write_bytes(b"image")
            journey = _multipart_journey(document, roi=None)

            def opener(request: Any, *, timeout: int) -> _Response:
                del timeout
                if request.method == "POST":
                    self.assertEqual(set(_multipart_files(request)), {"file"})
                    return _Response(b'{"task_id":"parent"}', status=202)
                return _Response(b'data: {"status":"completed"}\n\n')

            summary = execute_relayna_journeys(
                (journey,),
                base_url="http://service",
                opener=opener,
                workspace=workspace,
            )

        self.assertTrue(summary["success"])
        self.assertEqual([item["field"] for item in summary["inputs"][0]["files"]], ["file"])

    def test_multipart_total_request_limit_includes_encoding_overhead(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            document = workspace / "document.png"
            document.write_bytes(b"image")
            journey = _multipart_journey(document, roi=None)
            journey["multipart"]["maxTotalBytes"] = document.stat().st_size + 1

            with self.assertRaisesRegex(
                RelaynaJourneyError, "multipart request exceeds maxTotalBytes"
            ):
                validate_relayna_journey(journey, workspace=workspace)

    def test_multipart_security_and_bounds_are_validated(self) -> None:
        with TemporaryDirectory() as tmp, TemporaryDirectory() as outside_tmp:
            workspace = Path(tmp)
            document = workspace / "document.png"
            document.write_bytes(b"image")
            outside = Path(outside_tmp) / "outside.png"
            outside.write_bytes(b"outside")
            cases = (
                (
                    lambda item: item["multipart"]["files"][0].update(path=str(outside)),
                    "outside the approved",
                ),
                (
                    lambda item: item["multipart"]["files"][0].pop("contentType"),
                    "explicit contentType",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(contentType="text/html"),
                    "unsupported",
                ),
                (
                    lambda item: item["multipart"].update(maxFileBytes=4),
                    "exceeds maxFileBytes",
                ),
                (
                    lambda item: item["multipart"]["fields"].update(raw_object={"x": 1}),
                    "encoding: json",
                ),
                (lambda item: item.update(vus=33), "must not exceed 32"),
                (
                    lambda item: item["relayna"].update(timeoutSeconds=3601),
                    "must not exceed 3600",
                ),
            )
            for mutate, expected in cases:
                journey = _multipart_journey(document, roi=None)
                mutate(journey)
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(RelaynaJourneyError, expected):
                        validate_relayna_journey(journey, workspace=workspace)

            symlink = workspace / "escaped.png"
            symlink.symlink_to(outside)
            journey = _multipart_journey(symlink, roi=None)
            with self.assertRaisesRegex(RelaynaJourneyError, "outside the approved"):
                validate_relayna_journey(journey, workspace=workspace)

            journey = _multipart_journey(document, roi=None)
            journey["multipart"]["files"] = [
                {
                    "field": "file",
                    "required": False,
                    "contentType": "image/png",
                }
            ]
            with self.assertRaisesRegex(RelaynaJourneyError, "one readable file"):
                validate_relayna_journey(journey, workspace=workspace)

    def test_multipart_contract_errors_are_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            document = workspace / "document.png"
            document.write_bytes(b"image")
            empty = workspace / "empty.png"
            empty.touch()

            cases = (
                (lambda item: item.update(requestEncoding="xml"), "json or multipart"),
                (
                    lambda item: item["relayna"].update(taskIdPath=""),
                    "non-empty response path",
                ),
                (lambda item: item.update(multipart=[]), "must be a mapping"),
                (
                    lambda item: item["multipart"].update(fields=[]),
                    "fields must be a mapping",
                ),
                (
                    lambda item: item["multipart"].update(fields={"bad field": "x"}),
                    "field names must use",
                ),
                (
                    lambda item: item["multipart"].update(uniqueTaskIdField=3),
                    "uniqueTaskIdField must be a string",
                ),
                (
                    lambda item: item["multipart"].update(uniqueTaskIdField="bad field"),
                    "uniqueTaskIdField is not a valid",
                ),
                (
                    lambda item: item["multipart"].update(files=[]),
                    "must contain at least one file",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(field="bad field"),
                    "file 1 field must use",
                ),
                (
                    lambda item: item["multipart"]["files"].append(
                        {
                            "field": "file",
                            "path": str(document),
                            "contentType": "image/png",
                        }
                    ),
                    "file fields must be unique",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(required="yes"),
                    "required must be a boolean",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(path=str(empty)),
                    "must not be empty",
                ),
                (
                    lambda item: item["multipart"].update(maxTotalBytes=4),
                    "files exceed maxTotalBytes",
                ),
                (
                    lambda item: item["multipart"].update(files={}),
                    "files must be a list",
                ),
                (
                    lambda item: item["multipart"].update(files=["file"]),
                    "each multipart file must be a mapping",
                ),
                (
                    lambda item: item["multipart"]["files"][0].pop("path"),
                    "has no path",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(path=3),
                    "path must be a string",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(
                        path=str(workspace / "missing.png")
                    ),
                    "path is not readable",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(path=str(workspace)),
                    "path is not readable",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(filename=3),
                    "filename must be a non-empty string",
                ),
                (
                    lambda item: item["multipart"]["files"][0].update(filename="../document.png"),
                    "filename must not contain paths",
                ),
                (
                    lambda item: item["multipart"]["fields"].update(
                        schema={"encoding": "json", "value": [], "extra": True}
                    ),
                    "supports encoding and value only",
                ),
                (
                    lambda item: item["multipart"]["fields"].update(
                        schema={"encoding": "json", "value": 3}
                    ),
                    "requires an object or array",
                ),
                (
                    lambda item: item["multipart"]["fields"].update(
                        schema={"encoding": "json", "value": [float("nan")]}
                    ),
                    "not valid JSON",
                ),
                (
                    lambda item: item["multipart"]["fields"].update(schema=["field"]),
                    "objects and arrays require",
                ),
                (lambda item: item.update(iterations=0), "positive integer"),
            )
            for mutate, expected in cases:
                journey = _multipart_journey(document, roi=None)
                mutate(journey)
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(RelaynaJourneyError, expected):
                        validate_relayna_journey(journey, workspace=workspace)

    def test_real_http_fixture_executes_multipart_lifecycle(self) -> None:
        class FixtureHandler(BaseHTTPRequestHandler):
            submitted_content_type = ""
            submitted_body = b""

            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                type(self).submitted_content_type = self.headers["Content-Type"]
                type(self).submitted_body = self.rfile.read(length)
                payload = b'{"task_id":"fixture-parent","task_ids":["fixture-child"]}'
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                payload = b'data: {"status":"processing"}\n\ndata: {"status":"completed"}\n\n'
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                document = workspace / "document.png"
                document.write_bytes(b"real-network-image")
                summary = execute_relayna_journeys(
                    (_multipart_journey(document, roi=None),),
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    workspace=workspace,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(summary["success"])
        self.assertEqual(summary["tasks"][0]["statuses"], ("processing", "completed"))
        self.assertIn("multipart/form-data", FixtureHandler.submitted_content_type)
        self.assertIn(b"real-network-image", FixtureHandler.submitted_body)

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
                expected_stage = (
                    "admission" if label == "unexpected submission status" else "task_id_extraction"
                )
                self.assertEqual(summary["tasks"][0]["failure_stage"], expected_stage)

        def admission_timeout(request: Any, *, timeout: int) -> _Response:
            del request
            raise TimeoutError(f"admission timed out after {timeout}s")

        summary = execute_relayna_journeys(
            (_journey(),), base_url="http://service", opener=admission_timeout
        )
        self.assertIn("admission timed out", summary["tasks"][0]["error"])
        self.assertEqual(summary["tasks"][0]["failure_stage"], "timeout")

        def stream_failure(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                return _Response(b'{"task_id":"task-1"}', status=202)
            return _Response(b"not found", status=404)

        summary = execute_relayna_journeys(
            (_journey(),), base_url="http://service", opener=stream_failure
        )
        self.assertIn("event stream returned HTTP 404", summary["tasks"][0]["error"])
        self.assertEqual(summary["tasks"][0]["failure_stage"], "sse_connection")

        def stream_timeout(request: Any, *, timeout: int) -> _Response:
            del timeout
            if request.method == "POST":
                return _Response(b'{"task_id":"task-1"}', status=202)
            raise TimeoutError("event stream timed out")

        summary = execute_relayna_journeys(
            (_journey(),), base_url="http://service", opener=stream_timeout
        )
        self.assertIn("timed out", summary["tasks"][0]["error"])
        self.assertEqual(summary["tasks"][0]["failure_stage"], "timeout")

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
        self.assertEqual(summary["tasks"][0]["failure_stage"], "terminal_failure")

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


def _multipart_journey(document: Path, *, roi: Path | None) -> dict[str, Any]:
    files: list[dict[str, Any]] = [
        {
            "field": "file",
            "path": str(document),
            "filename": "document.png",
            "contentType": "image/png",
            "required": True,
        }
    ]
    files.append(
        {
            "field": "roi",
            **({"path": str(roi)} if roi is not None else {}),
            "filename": "roi-mask.png",
            "contentType": "image/png",
            "required": False,
        }
    )
    return {
        "name": "document-lifecycle",
        "adapter": "relayna",
        "method": "POST",
        "path": "/tasks",
        "expectedStatus": 202,
        "requestEncoding": "multipart",
        "multipart": {
            "fields": {
                "task_id": "document-smoke",
                "priority": 5,
                "strict_mode": False,
                "extraction_fields": {
                    "encoding": "json",
                    "value": [{"name": "document_number", "type": "string"}],
                },
                "processing_config": {
                    "encoding": "json",
                    "value": {"fallback": True, "engine": "internal"},
                },
            },
            "files": files,
        },
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


def _multipart_message(request: Any) -> Any:
    headers = (
        f"Content-Type: {request.headers['Content-type']}\r\nMIME-Version: 1.0\r\n\r\n"
    ).encode()
    return BytesParser(policy=default).parsebytes(headers + request.data)


def _multipart_fields(request: Any) -> dict[str, str]:
    return {
        str(part.get_param("name", header="content-disposition")): part.get_content()
        for part in _multipart_message(request).iter_parts()
        if part.get_filename() is None
    }


def _multipart_files(request: Any) -> dict[str, tuple[str, str, bytes]]:
    return {
        str(part.get_param("name", header="content-disposition")): (
            str(part.get_filename()),
            part.get_content_type(),
            part.get_payload(decode=True),
        )
        for part in _multipart_message(request).iter_parts()
        if part.get_filename() is not None
    }


if __name__ == "__main__":
    unittest.main()
