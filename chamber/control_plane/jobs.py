"""Durable single-host assessment jobs with bounded subprocess supervision."""

from __future__ import annotations

import fcntl
import hashlib
import json
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

from chamber.runs import initialize_run_record, new_run_directory, write_json_atomic

TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})
OUTPUT_LIMIT = 64 * 1024
CANCEL_GRACE_SECONDS = 10.0
KILL_GRACE_SECONDS = 5.0


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
    idempotency_key: str | None = None
    request_digest: str | None = None
    cleanup_required: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    cancel_at: float | None = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(self).items()
            if key not in {"process", "cancel_at"}
        }


class AssessmentJobManager:
    """Persist jobs; a per-job OS lease distinguishes active and interrupted work."""

    def __init__(self, workspace: Path, *, cwd: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.cwd = (cwd or Path.cwd()).resolve()
        self.directory = self.workspace / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, AssessmentJob] = {}
        self._lock = threading.RLock()
        self._restore()

    def _restore(self) -> None:
        for path in self.directory.glob("*.json"):
            data: dict[str, Any] = json.loads(path.read_text())
            data["config_path"] = Path(data["config_path"])
            job = AssessmentJob(**data)
            if job.state in TERMINAL_JOB_STATES:
                continue
            with (self.directory / f"{job.job_id}.lock").open("a") as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                job.state = "failed"
                job.error = (
                    "Assessment interrupted: supervisor stopped. Verify cleanup before retesting."
                )
                job.cleanup_required = job.mode == "kubernetes"
                self._persist(job)
                self._record_failure(job)

    def _persist(self, job: AssessmentJob) -> None:
        write_json_atomic(self.directory / f"{job.job_id}.json", job.summary())

    def start(
        self,
        config_path: Path,
        *,
        mode: str,
        context: str | None = None,
        prometheus_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        config_bytes = config_path.read_bytes()
        digest = hashlib.sha256(
            config_bytes
            + json.dumps([mode, context, prometheus_url], separators=(",", ":")).encode()
        ).hexdigest()
        # Serialize submission across HTTP workers, including idempotency lookup.
        with self._lock, (self.directory / ".submit.lock").open("a") as submission:
            fcntl.flock(submission, fcntl.LOCK_EX)
            if idempotency_key:
                for path in self.directory.glob("*.json"):
                    previous = json.loads(path.read_text())
                    if previous.get("idempotency_key") == idempotency_key:
                        if previous.get("request_digest") != digest:
                            raise ValueError("Idempotency key already used with a different plan")
                        return previous
            run_dir = new_run_directory(self.workspace / "runs", "assessment")
            initialize_run_record(run_dir, mode=mode)
            job_id = uuid.uuid4().hex
            snapshot = self.directory / f"{job_id}.yaml"
            snapshot.write_bytes(config_bytes)
            job = AssessmentJob(
                job_id=job_id,
                config_path=snapshot,
                mode=mode,
                context=context,
                prometheus_url=prometheus_url,
                created_at=datetime.now(UTC).isoformat(),
                run_id=run_dir.name,
                idempotency_key=idempotency_key,
                request_digest=digest,
            )
            lease = (self.directory / f"{job_id}.lock").open("a")
            fcntl.flock(lease, fcntl.LOCK_EX)
            self._jobs[job_id] = job
            self._persist(job)

            def execute() -> None:
                try:
                    self._execute(job_id)
                except Exception as exc:
                    with self._lock:
                        if job.process is not None and job.process.poll() is None:
                            _signal(job.process, signal.SIGKILL)
                        job.state = "failed"
                        job.error = f"Assessment supervisor failed: {exc}"
                        job.cleanup_required = job.mode == "kubernetes"
                        self._persist(job)
                        self._record_failure(job)
                finally:
                    lease.close()

            threading.Thread(target=execute, daemon=True).start()
            return job.summary()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                # IDs are opaque; never interpret an API ID as a filesystem path.
                if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                    raise KeyError(job_id)
                path = self.directory / f"{job_id}.json"
                if not path.exists():
                    raise KeyError(job_id)
                return json.loads(path.read_text())
            job = self._jobs[job_id]
            path = self.directory / f"{job_id}.json"
            return json.loads(path.read_text()) if path.exists() else job.summary()

    def list(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(
                sorted(
                    (json.loads(path.read_text()) for path in self.directory.glob("*.json")),
                    key=lambda item: item["created_at"],
                    reverse=True,
                )
            )

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            current = self.get(job_id)
            if current["state"] in TERMINAL_JOB_STATES:
                return current
            # A separate marker also supports cancellation from another HTTP worker.
            (self.directory / f"{job_id}.cancel").touch()
            job = self._jobs.get(job_id)
            if job is not None:
                job.cancel_requested = True
                job.state = "cancelling"
                if job.cancel_at is None:
                    job.cancel_at = time.monotonic()
                    if job.process is not None and job.process.poll() is None:
                        _signal(job.process, signal.SIGINT)
                self._persist(job)
            return self.get(job_id)

    def _execute(self, job_id: str) -> None:
        job = self._jobs[job_id]
        with self._lock:
            if job.cancel_requested:
                job.state = "cancelled"
                self._persist(job)
                self._record_failure(job)
                return
            try:
                environment = os.environ.copy()
                environment["AMPULE_CHAMBER_WORKSPACE"] = str(self.workspace)
                environment["PYTHONUNBUFFERED"] = "1"
                process = subprocess.Popen(
                    _command(job),
                    cwd=self.cwd,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    start_new_session=True,
                )
            except OSError as exc:
                job.state = "failed"
                job.error = str(exc)
                self._persist(job)
                self._record_failure(job)
                return
            job.process = process
            job.state = "running"
            self._persist(job)
        reader = threading.Thread(target=self._drain, args=(job, process), daemon=True)
        reader.start()
        forced = False
        while process.poll() is None:
            if (self.directory / f"{job_id}.cancel").exists() and not job.cancel_requested:
                self.cancel(job_id)
            if job.cancel_at is not None:
                elapsed = time.monotonic() - job.cancel_at
                if elapsed >= CANCEL_GRACE_SECONDS + KILL_GRACE_SECONDS:
                    _signal(process, signal.SIGKILL)
                    forced = True
                elif elapsed >= CANCEL_GRACE_SECONDS:
                    _signal(process, signal.SIGTERM)
                    forced = True
            time.sleep(0.05)
        reader.join(timeout=1)
        with self._lock:
            job.returncode = process.returncode
            job.process = None
            job.state = (
                "cancelled"
                if job.cancel_requested
                else ("completed" if process.returncode == 0 else "failed")
            )
            if job.state == "failed":
                job.error = _last_line(job.output) or f"assessment exited {process.returncode}"
            job.cleanup_required = job.state != "completed" and job.mode == "kubernetes"
            if forced:
                job.error = (
                    "Cancellation required forced termination; verify cleanup before retesting."
                )
            self._persist(job)
            if job.state != "completed":
                self._record_failure(job)

    def _drain(self, job: AssessmentJob, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            while chunk := process.stdout.read(1024):
                with self._lock:
                    job.output = (job.output + chunk)[-OUTPUT_LIMIT:]
                    self._persist(job)
        finally:
            process.stdout.close()

    def _record_failure(self, job: AssessmentJob) -> None:
        if job.run_id is None:
            return
        run_dir = self.workspace / "runs" / job.run_id
        # Keep interruption terminal even if an orphaned child later writes metadata.
        write_json_atomic(
            run_dir / "execution-failure.json",
            {
                "stage": job.state,
                "error": job.error,
                "success": False,
                "cleanup_required": job.cleanup_required,
            },
        )
        record = initialize_run_record(run_dir, mode=job.mode)
        record.update(
            state=job.state,
            execution_error=job.error,
            error=job.error,
            success=False,
            cleanup_required=job.cleanup_required,
            job_id=job.job_id,
        )
        write_json_atomic(run_dir / "run.json", record)
        metadata_path = run_dir / "run-metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            metadata.update(stage=job.state, error=job.error, success=False)
            if job.cleanup_required:
                metadata.update(cleanup_performed=False, rollback={"verified": False})
            write_json_atomic(metadata_path, metadata)
        path = run_dir / "result.json"
        if path.exists():
            result = json.loads(path.read_text())
            result.update(
                status=job.state, conclusive=False, readiness_score=None, confidence="limited"
            )
            if job.cleanup_required:
                result.update(cleanup_verified=False, rollback_verified=False)
            result["verdict"] = {
                "headline": "Assessment " + job.state,
                "what_happened": "The assessment did not finish successfully.",
                "why": job.error or "Execution was cancelled.",
                "next_step": "Review execution details and verify cleanup before retesting.",
            }
            write_json_atomic(path, result)


def _signal(process: subprocess.Popen[str], value: int) -> None:
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        pass


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
    if job.run_id:
        command.extend(("--run-dir", str(job.config_path.parent.parent / "runs" / job.run_id)))
    if job.context:
        command.extend(("--context", job.context))
    if job.prometheus_url:
        command.extend(("--prometheus-url", job.prometheus_url))
    return command


def _last_line(value: str) -> str:
    return next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")
