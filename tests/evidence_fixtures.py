"""Small recorded collector payloads used by readiness gate tests."""


def evidence_payload(name: str) -> dict:
    name = name.removesuffix(".json")
    return {
        "preflight": {"ready": True, "blockers": []},
        "kubernetes-commands": {
            "commands": [
                {
                    "command": ["kubectl", "get", "events"],
                    "exit_status": 0,
                    "stdout": '{"items": []}',
                },
                {
                    "command": ["kubectl", "logs", "sample"],
                    "exit_status": 0,
                    "stdout": "request completed",
                },
                {
                    "command": ["kubectl", "get", "pods", "-o", "json"],
                    "exit_status": 0,
                    "stdout": '{"items": [{"metadata": {"name": "sample"}}]}',
                },
            ]
        },
        "k6-summary": {
            "metrics": {
                "http_reqs": {"values": {"count": 10}},
                "http_req_failed": {"values": {"rate": 0}},
                "http_req_duration": {"values": {"p(95)": 10}},
            }
        },
        "relayna-summary": {"task_count": 1, "tasks": [{"task_id": "fixture", "success": True}]},
    }[name]
