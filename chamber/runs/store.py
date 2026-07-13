"""Collision-safe, reconstructable storage for chamber runs."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_SCHEMA_VERSION = "chamber.ampule.dev/run/v1"
EVENT_SCHEMA_VERSION = "chamber.ampule.dev/run-event/v1"
EVIDENCE_SCHEMA_VERSION = "chamber.ampule.dev/evidence-manifest/v1"


def new_run_directory(root: Path, name: str, *, now: datetime | None = None) -> Path:
    """Create and return a unique run directory using exclusive creation."""

    root.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d%H%M%S%f")[:-3]
    safe_name = _dns_fragment(name or "assessment")
    for _ in range(10):
        suffix = uuid.uuid4().hex[:8]
        path = root / f"chamber-{safe_name}-{timestamp}-{suffix}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise RuntimeError("could not allocate a unique chamber run directory")


def initialize_run_record(
    run_dir: Path,
    *,
    service_name: str = "unknown",
    mode: str = "planned",
) -> dict[str, Any]:
    """Initialize a canonical run record and created lifecycle event."""

    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    if path.exists():
        payload = read_json_value(path)
        if isinstance(payload, dict):
            return payload
    now = _now()
    payload = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_dir.name,
        "service_name": service_name,
        "mode": mode,
        "state": "created",
        "created_at": now,
        "updated_at": now,
        "result_status": None,
    }
    write_json_atomic(path, payload)
    append_run_event(run_dir, state="created", event_type="run_created")
    return payload


def sync_run_record(run_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Project workflow metadata into the canonical run record."""

    current = initialize_run_record(
        run_dir,
        service_name=str(metadata.get("service_name", "unknown")),
        mode=str(metadata.get("mode", "planned")),
    )
    previous_state = str(current.get("state", "created"))
    next_state = _canonical_state(str(metadata.get("stage", previous_state)))
    current.update(
        {
            "run_id": str(metadata.get("run_id", run_dir.name)),
            "service_name": str(
                metadata.get("service_name", current.get("service_name", "unknown"))
            ),
            "mode": str(metadata.get("mode", current.get("mode", "planned"))),
            "runtime_mode": metadata.get("runtime_mode"),
            "state": next_state,
            "updated_at": _now(),
            "context": metadata.get("context"),
            "namespace": metadata.get("namespace"),
            "success": metadata.get("success"),
            "cleanup_performed": metadata.get("cleanup_performed"),
            "error": metadata.get("error"),
            "scenario_id": _mapping(metadata.get("scenario")).get("id"),
            "scenario_source": _mapping(metadata.get("scenario")).get("source"),
            "scenario_revision": _mapping(metadata.get("scenario")).get("revision"),
        }
    )
    write_json_atomic(run_dir / "run.json", current)
    if next_state != previous_state:
        append_run_event(
            run_dir,
            state=next_state,
            event_type="state_changed",
            payload={"previous_state": previous_state},
        )
    return current


def append_run_event(
    run_dir: Path,
    *,
    state: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one durable lifecycle event to a run event stream."""

    events_path = run_dir / "events.jsonl"
    sequence = _event_sequence(events_path) + 1
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": f"{run_dir.name}:{sequence}",
        "sequence": sequence,
        "run_id": run_dir.name,
        "state": state,
        "event_type": event_type,
        "observed_at": _now(),
        "payload": payload or {},
    }
    line = (json.dumps(event, sort_keys=True) + "\n").encode()
    descriptor = os.open(events_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return event


def refresh_evidence_manifest(run_dir: Path) -> dict[str, Any]:
    """Register the current evidence directory with run-bound digests."""

    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in sorted(item for item in evidence_dir.rglob("*") if item.is_file()):
        if path.name == "manifest.json":
            continue
        relative_path = path.relative_to(run_dir)
        source, signal_type = _evidence_identity(path)
        entries.append(
            {
                "evidence_id": _evidence_id(path),
                "run_id": run_dir.name,
                "source": source,
                "signal_type": signal_type,
                "resource": run_dir.name,
                "relative_path": str(relative_path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "collected_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "available": True,
                "redacted": path.name == "kubernetes-commands.json",
            }
        )
    manifest = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "run_id": run_dir.name,
        "generated_at": _now(),
        "entries": entries,
    }
    write_json_atomic(evidence_dir / "manifest.json", manifest)
    return manifest


def registered_evidence(run_dir: Path) -> tuple[dict[str, Any], ...]:
    """Return only evidence registered to this run and still digest-valid."""

    path = run_dir / "evidence/manifest.json"
    if not path.exists():
        return ()
    payload = read_json_value(path)
    if not isinstance(payload, dict) or payload.get("run_id") != run_dir.name:
        return ()
    values = payload.get("entries")
    if not isinstance(values, list):
        return ()
    valid = []
    for item in values:
        if not isinstance(item, dict) or item.get("run_id") != run_dir.name:
            continue
        relative = item.get("relative_path")
        if not isinstance(relative, str):
            continue
        evidence_path = (run_dir / relative).resolve()
        if not evidence_path.is_relative_to(run_dir.resolve()) or not evidence_path.is_file():
            continue
        if str(item.get("sha256")) != _sha256(evidence_path):
            continue
        valid.append(item)
    return tuple(valid)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON atomically in the destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json_value(path: Path) -> Any:
    """Read any JSON value from disk."""

    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class RunIndex:
    """Rebuildable SQLite summary index for the local control plane."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.runs_dir = workspace / "runs"
        self.path = workspace / "index.sqlite3"

    def rebuild(self) -> int:
        """Replace index contents from canonical run directories."""

        self.workspace.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("DELETE FROM runs")
                count = 0
                for run_dir in sorted(self.runs_dir.glob("*")):
                    if not run_dir.is_dir():
                        continue
                    record = _run_summary(run_dir)
                    if record is None:
                        continue
                    connection.execute(
                        """
                        INSERT INTO runs (
                            run_id, service_name, state, mode, runtime_mode,
                            created_at, updated_at, result_status, run_dir
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record["run_id"],
                            record["service_name"],
                            record["state"],
                            record["mode"],
                            record["runtime_mode"],
                            record["created_at"],
                            record["updated_at"],
                            record["result_status"],
                            str(run_dir),
                        ),
                    )
                    count += 1
        return count

    def list_runs(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """List recent indexed runs."""

        self.workspace.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT run_id, service_name, state, mode, runtime_mode,
                       created_at, updated_at, result_status, run_dir
                FROM runs ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        keys = (
            "run_id",
            "service_name",
            "state",
            "mode",
            "runtime_mode",
            "created_at",
            "updated_at",
            "result_status",
            "run_dir",
        )
        return tuple(dict(zip(keys, row, strict=True)) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                service_name TEXT NOT NULL,
                state TEXT NOT NULL,
                mode TEXT NOT NULL,
                runtime_mode TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_status TEXT,
                run_dir TEXT NOT NULL
            )
            """
        )
        return connection


def _run_summary(run_dir: Path) -> dict[str, Any] | None:
    run_path = run_dir / "run.json"
    if run_path.exists():
        value = read_json_value(run_path)
        if isinstance(value, dict):
            return {
                "run_id": str(value.get("run_id", run_dir.name)),
                "service_name": str(value.get("service_name", "unknown")),
                "state": str(value.get("state", "unknown")),
                "mode": str(value.get("mode", "unknown")),
                "runtime_mode": value.get("runtime_mode"),
                "created_at": str(value.get("created_at", "")),
                "updated_at": str(value.get("updated_at", "")),
                "result_status": value.get("result_status"),
            }
    metadata_path = run_dir / "run-metadata.json"
    if not metadata_path.exists():
        return None
    value = read_json_value(metadata_path)
    if not isinstance(value, dict):
        return None
    timestamp = datetime.fromtimestamp(metadata_path.stat().st_mtime, UTC).isoformat()
    return {
        "run_id": str(value.get("run_id", run_dir.name)),
        "service_name": _service_name(run_dir),
        "state": _canonical_state(str(value.get("stage", "unknown"))),
        "mode": str(value.get("mode", "unknown")),
        "runtime_mode": value.get("runtime_mode"),
        "created_at": timestamp,
        "updated_at": timestamp,
        "result_status": _result_status(run_dir),
    }


def _service_name(run_dir: Path) -> str:
    config_path = run_dir / "chamber.yaml"
    if not config_path.exists():
        return "unknown"
    try:
        import yaml

        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(value, dict):
        return "unknown"
    service = value.get("service")
    return str(service.get("name", "unknown")) if isinstance(service, dict) else "unknown"


def _result_status(run_dir: Path) -> str | None:
    path = run_dir / "result.json"
    if not path.exists():
        return None
    value = read_json_value(path)
    return str(value.get("status")) if isinstance(value, dict) and value.get("status") else None


def _canonical_state(stage: str) -> str:
    return {
        "planned": "intake_validated",
        "assessed": "completed",
        "preflight_failed": "failed",
        "cancelled": "cancelled",
    }.get(stage, stage)


def _event_sequence(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def _evidence_identity(path: Path) -> tuple[str, str]:
    identities = {
        "preflight.json": ("kubectl", "kubernetes_preflight"),
        "kubernetes-commands.json": ("kubectl", "kubernetes_runtime"),
        "attach-discovery.json": ("kubectl", "attach_target_discovery"),
        "pre-test-state.json": ("kubectl", "attach_pre_test_state"),
        "rollback.json": ("kubectl", "attach_fault_rollback"),
        "k6-summary.json": ("k6", "traffic_summary"),
        "k6.js": ("k6", "traffic_script"),
        "relayna-summary.json": ("relayna", "task_lifecycle_summary"),
        "prometheus-memory.json": ("prometheus", "metrics_snapshot"),
        "local-assessment.json": ("ampule-chamber", "local_assessment"),
    }
    return identities.get(path.name, ("ampule-chamber", path.stem.replace("-", "_")))


def _evidence_id(path: Path) -> str:
    return path.stem.replace("_", "-")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dns_fragment(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "service"


def _now() -> str:
    return datetime.now(UTC).isoformat()
