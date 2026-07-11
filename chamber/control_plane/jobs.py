"""Background assessment subprocesses with safe cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class AssessmentJob:
    """Mutable process state guarded by the manager lock."""

    job_id: str
    config_path: Path
    mode: str
    context: str | None
    prometheus_url: str | None
    created_at: str
    state: str = "queued"
    run_id: str | None = None
    returncode: int | None = None
    output: str = ""
    error: str | None = None
    cancel_requested: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "config_path": str(self.config_path),
            "mode": self.mode,
            "context": self.context,
            "prometheus_url": self.prometheus_url,
            "created_at": self.created_at,
            "state": self.state,
            "run_id": self.run_id,
            "returncode": self.returncode,
            "output": self.output,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
        }


class AssessmentJobManager:
    """Run assessments in child processes so SIGINT can trigger cleanup."""

    def __init__(self, workspace: Path, *, cwd: Path | None = None) -> None:
        self.workspace = workspace
        self.cwd = (cwd or Path.cwd()).resolve()
        self._jobs: dict[str, AssessmentJob] = {}
        self._lock = threading.Lock()

    def start(
        self,
        config_path: Path,
        *,
        mode: str,
        context: str | None = None,
        prometheus_url: str | None = None,
    ) -> dict[str, Any]:
        job = AssessmentJob(
            job_id=uuid.uuid4().hex,
            config_path=config_path.resolve(),
            mode=mode,
            context=context,
            prometheus_url=prometheus_url,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        threading.Thread(target=self._execute, args=(job.job_id,), daemon=True).start()
        return job.summary()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job.summary()

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.state in TERMINAL_JOB_STATES:
                return job.summary()
            job.cancel_requested = True
            job.state = "cancelling"
            process = job.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
        return self.get(job_id)

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested:
                job.state = "cancelled"
                return
            before = _run_directories(self.workspace)
            command = _command(job)
            try:
                environment = os.environ.copy()
                environment["AMPULE_CHAMBER_WORKSPACE"] = str(self.workspace.resolve())
                process = subprocess.Popen(
                    command,
                    cwd=self.cwd,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError as exc:
                job.state = "failed"
                job.error = str(exc)
                return
            job.process = process
            if job.cancel_requested:
                job.state = "cancelling"
                process.send_signal(signal.SIGINT)
            else:
                job.state = "running"
        while process.poll() is None:
            self._discover_run(job_id, before)
            time.sleep(0.2)
        output, _ = process.communicate()
        self._discover_run(job_id, before)
        with self._lock:
            job = self._jobs[job_id]
            job.returncode = process.returncode
            job.output = output
            job.process = None
            if job.cancel_requested:
                job.state = "cancelled"
            elif process.returncode == 0:
                job.state = "completed"
            else:
                job.state = "failed"
                job.error = _last_line(output) or f"assessment exited {process.returncode}"

    def _discover_run(self, job_id: str, before: set[str]) -> None:
        created = _run_directories(self.workspace) - before
        if not created:
            return
        newest = max(
            created,
            key=lambda item: (self.workspace / "runs" / item).stat().st_mtime,
        )
        with self._lock:
            self._jobs[job_id].run_id = newest


def _command(job: AssessmentJob) -> list[str]:
    command = [
        sys.executable,
        "-c",
        "from chamber.workflow import main; raise SystemExit(main())",
        "assess",
        "--config",
        str(job.config_path),
        "--mode",
        job.mode,
    ]
    if job.context:
        command.extend(("--context", job.context))
    if job.prometheus_url:
        command.extend(("--prometheus-url", job.prometheus_url))
    return command


def _run_directories(workspace: Path) -> set[str]:
    runs_dir = workspace / "runs"
    if not runs_dir.exists():
        return set()
    return {path.name for path in runs_dir.iterdir() if path.is_dir()}


def _last_line(value: str) -> str:
    return next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")
