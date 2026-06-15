from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    mode = "target"
    downstream_url = "http://downstream-api:8081"
    fault_status = 0
    fault_delay_ms = 0

    def do_GET(self) -> None:
        if self.mode == "downstream" and self.path in {"/healthz", "/readyz", "/dependency"}:
            self._downstream()
        elif self.path == "/healthz":
            self._json(200, {"status": "healthy"})
        elif self.path == "/readyz":
            self._json(200, {"status": "ready"})
        elif self.path == "/work":
            time.sleep(0.02)
            self._json(200, {"status": "ok", "workMs": 20})
        elif self.path == "/dependency":
            self._dependency()
        else:
            self._json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        print(json.dumps({"path": self.path, "message": format % args}), flush=True)

    def _dependency(self) -> None:
        try:
            with urllib.request.urlopen(f"{self.downstream_url}/healthz", timeout=1) as response:
                status = response.status
        except (urllib.error.URLError, TimeoutError) as exc:
            self._json(503, {"dependency": "unavailable", "error": str(exc)})
            return

        self._json(200, {"dependency": "ok", "status": status})

    def _downstream(self) -> None:
        if self.fault_delay_ms > 0:
            time.sleep(self.fault_delay_ms / 1000)
        if self.fault_status:
            self._json(self.fault_status, {"dependency": "fault", "status": self.fault_status})
            return
        self._json(200, {"dependency": "ok"})

    def _json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mode", choices=["target", "downstream"], default="target")
    args = parser.parse_args()

    Handler.mode = args.mode
    Handler.downstream_url = os.environ.get("DOWNSTREAM_URL", Handler.downstream_url)
    Handler.fault_status = int(os.environ.get("FAULT_STATUS", "0"))
    Handler.fault_delay_ms = int(os.environ.get("FAULT_DELAY_MS", "0"))
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(json.dumps({"event": "listening", "mode": args.mode, "port": args.port}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
