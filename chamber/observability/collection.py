"""Runtime evidence collection for chamber runs."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from chamber.environment import EnvironmentMetadata

PROMETHEUS_URL_ENV = "PROMETHEUS_URL"


class ObservabilityCollectionError(RuntimeError):
    """Raised when required runtime evidence cannot be collected."""


@dataclass(frozen=True)
class CollectionDiagnostic:
    """Collector diagnostic attached to evidence or surfaced on failure."""

    severity: str
    message: str
    source: str


@dataclass(frozen=True)
class EvidenceArtifact:
    """Raw evidence captured for one run and signal."""

    evidence_id: str
    run_id: str
    scenario_id: str
    source: str
    signal_type: str
    resource: str
    collected_at: str
    start_time: str | None
    end_time: str | None
    payload: dict[str, Any]
    diagnostics: tuple[CollectionDiagnostic, ...] = ()


@dataclass(frozen=True)
class PrometheusQuery:
    """Prometheus instant query mapped to an MVP signal."""

    signal_type: str
    resource: str
    query: str


DEFAULT_PROMETHEUS_QUERIES = (
    PrometheusQuery(
        signal_type="memory_usage",
        resource="target-pods",
        query=("100 * container_memory_working_set_bytes / container_spec_memory_limit_bytes"),
    ),
    PrometheusQuery(
        signal_type="cpu_usage",
        resource="target-pods",
        query="100 * rate(container_cpu_usage_seconds_total[5m])",
    ),
    PrometheusQuery(
        signal_type="cpu_throttling",
        resource="target-pods",
        query=(
            "100 * rate(container_cpu_cfs_throttled_periods_total[5m])"
            " / rate(container_cpu_cfs_periods_total[5m])"
        ),
    ),
    PrometheusQuery(
        signal_type="request_latency",
        resource="target-service",
        query=(
            "histogram_quantile(0.95, "
            "sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) * 1000"
        ),
    ),
    PrometheusQuery(
        signal_type="error_rate",
        resource="target-service",
        query=(
            '100 * sum(rate(http_requests_total{status=~"5.."}[5m]))'
            " / sum(rate(http_requests_total[5m]))"
        ),
    ),
)


def collect_kubernetes_evidence(
    environment: EnvironmentMetadata,
    *,
    kubectl: str = "kubectl",
) -> tuple[EvidenceArtifact, ...]:
    """Collect Kubernetes pod, event, and log evidence scoped to a chamber run."""

    selector = _label_selector(environment.cleanup_selectors)
    pods = _kubectl_json(
        kubectl,
        "-n",
        environment.namespace,
        "get",
        "pods",
        "-l",
        selector,
        "-o",
        "json",
    )
    events = _kubectl_json(
        kubectl,
        "-n",
        environment.namespace,
        "get",
        "events",
        "-l",
        selector,
        "-o",
        "json",
    )
    collected_at = _now()
    evidence = [
        _artifact(
            environment,
            source="kubernetes",
            signal_type="pod_status",
            resource=environment.namespace,
            payload=pods,
            collected_at=collected_at,
        ),
        _artifact(
            environment,
            source="kubernetes",
            signal_type="kubernetes_events",
            resource=environment.namespace,
            payload=events,
            collected_at=collected_at,
        ),
    ]
    for pod_name in _pod_names(pods):
        logs = _kubectl_text(
            kubectl,
            "-n",
            environment.namespace,
            "logs",
            pod_name,
            "--all-containers=true",
            "--tail=200",
        )
        evidence.append(
            _artifact(
                environment,
                source="kubernetes",
                signal_type="logs",
                resource=pod_name,
                payload={"text": logs},
                collected_at=collected_at,
            )
        )
    return tuple(evidence)


def collect_prometheus_evidence(
    environment: EnvironmentMetadata,
    *,
    prometheus_url: str | None = None,
    queries: tuple[PrometheusQuery, ...] = DEFAULT_PROMETHEUS_QUERIES,
    timeout_seconds: float = 10,
) -> tuple[EvidenceArtifact, ...]:
    """Collect required Prometheus evidence for MVP metrics."""

    base_url = (prometheus_url or os.environ.get(PROMETHEUS_URL_ENV) or "").rstrip("/")
    if not base_url:
        raise ObservabilityCollectionError(
            f"{PROMETHEUS_URL_ENV} is required to collect Prometheus evidence"
        )

    collected_at = _now()
    evidence: list[EvidenceArtifact] = []
    for query in queries:
        payload = _prometheus_query(base_url, query.query, timeout_seconds=timeout_seconds)
        if payload.get("status") != "success":
            raise ObservabilityCollectionError(
                f"Prometheus query for {query.signal_type} did not succeed"
            )
        evidence.append(
            _artifact(
                environment,
                source="prometheus",
                signal_type=query.signal_type,
                resource=query.resource,
                payload={
                    "query": query.query,
                    "result": payload,
                },
                collected_at=collected_at,
            )
        )
    return tuple(evidence)


def _kubectl_json(kubectl: str, *args: str) -> dict[str, Any]:
    text = _kubectl_text(kubectl, *args)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ObservabilityCollectionError(f"kubectl returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ObservabilityCollectionError("kubectl JSON output must be an object")
    return value


def _kubectl_text(kubectl: str, *args: str) -> str:
    command = (kubectl, *args)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or "no stderr"
        raise ObservabilityCollectionError(
            f"{' '.join(command)} failed with exit {completed.returncode}: {stderr}"
        )
    return completed.stdout


def _prometheus_query(base_url: str, query: str, *, timeout_seconds: float) -> dict[str, Any]:
    url = f"{base_url}/api/v1/query?{urlencode({'query': query})}"
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except URLError as exc:
        raise ObservabilityCollectionError(
            f"Prometheus endpoint {base_url!r} is unreachable: {exc}"
        ) from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ObservabilityCollectionError(f"Prometheus returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ObservabilityCollectionError("Prometheus response must be an object")
    return value


def _artifact(
    environment: EnvironmentMetadata,
    *,
    source: str,
    signal_type: str,
    resource: str,
    payload: dict[str, Any],
    collected_at: str,
) -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=f"{environment.run_id}:{source}:{signal_type}:{resource}",
        run_id=environment.run_id,
        scenario_id=environment.scenario_id,
        source=source,
        signal_type=signal_type,
        resource=resource,
        collected_at=collected_at,
        start_time=None,
        end_time=None,
        payload=payload,
    )


def _label_selector(labels: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def _pod_names(pods: dict[str, Any]) -> tuple[str, ...]:
    items = pods.get("items")
    if not isinstance(items, list):
        return ()
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        name = metadata.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
    return tuple(names)


def _now() -> str:
    return datetime.now(UTC).isoformat()
