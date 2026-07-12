"""Namespace-scoped discovery of services and backing workloads."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from chamber.environment.preflight import validate_kubernetes_attach_target

DISCOVERY_NAMESPACES_ENV = "AMPULE_CHAMBER_DISCOVERY_NAMESPACES"
KUBERNETES_CONTEXT_ENV = "AMPULE_CHAMBER_KUBERNETES_CONTEXT"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


class DiscoveryError(RuntimeError):
    """Raised when declared-target discovery cannot complete safely."""


class DiscoveryRunner(Protocol):
    def run(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]: ...


class SubprocessDiscoveryRunner:
    def run(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, check=False, capture_output=True, text=True)


@dataclass(frozen=True)
class DiscoverySettings:
    context: str
    namespaces: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> DiscoverySettings:
        namespaces = tuple(
            dict.fromkeys(
                value.strip()
                for value in os.environ.get(DISCOVERY_NAMESPACES_ENV, "").split(",")
                if value.strip()
            )
        )
        return cls(
            context=os.environ.get(KUBERNETES_CONTEXT_ENV, "in-cluster").strip() or "in-cluster",
            namespaces=namespaces,
        )


class KubernetesDiscovery:
    """Read Kubernetes targets through the same explicit context used by runs."""

    def __init__(
        self,
        settings: DiscoverySettings,
        *,
        runner: DiscoveryRunner | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner or SubprocessDiscoveryRunner()

    def discover(self, *, context: str, namespace: str) -> dict[str, Any]:
        selected_context = context.strip() or self.settings.context
        selected_namespace = namespace.strip()
        validate_kubernetes_attach_target(selected_context, selected_namespace)
        if not _DNS_LABEL.fullmatch(selected_namespace):
            raise DiscoveryError("namespace must be a valid Kubernetes DNS label")
        if self.settings.context and selected_context != self.settings.context:
            raise DiscoveryError(
                f"context {selected_context!r} is not the configured discovery context"
            )
        if selected_namespace not in self.settings.namespaces:
            raise DiscoveryError(f"namespace {selected_namespace!r} is not declared for discovery")

        services = self._items(selected_context, selected_namespace, "services")
        deployments = self._items(selected_context, selected_namespace, "deployments.apps")
        statefulsets = self._items(selected_context, selected_namespace, "statefulsets.apps")
        workloads = tuple(_workload(item, kind="Deployment") for item in deployments) + tuple(
            _workload(item, kind="StatefulSet") for item in statefulsets
        )
        discovered_services = tuple(_service(item, workloads=workloads) for item in services)
        return {
            "context": selected_context,
            "namespace": selected_namespace,
            "services": discovered_services,
            "workloads": workloads,
        }

    def _items(self, context: str, namespace: str, resource: str) -> list[dict[str, Any]]:
        command = (
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "get",
            resource,
            "-o",
            "json",
        )
        completed = self.runner.run(command)
        if completed.returncode != 0:
            detail = _last_line(completed.stderr) or _last_line(completed.stdout)
            raise DiscoveryError(
                f"could not discover {resource} in {namespace!r}"
                + (f": {detail}" if detail else "")
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"kubectl returned invalid JSON for {resource}") from exc
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise DiscoveryError(f"kubectl returned no item list for {resource}")
        return [item for item in items if isinstance(item, dict)]


def _workload(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    metadata = _mapping(item.get("metadata"))
    spec = _mapping(item.get("spec"))
    status = _mapping(item.get("status"))
    template = _mapping(spec.get("template"))
    template_metadata = _mapping(template.get("metadata"))
    return {
        "name": str(metadata.get("name", "")),
        "kind": kind,
        "replicas": int(spec.get("replicas") or 0),
        "ready_replicas": int(status.get("readyReplicas") or 0),
        "labels": _string_mapping(template_metadata.get("labels")),
    }


def _service(item: dict[str, Any], *, workloads: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    metadata = _mapping(item.get("metadata"))
    spec = _mapping(item.get("spec"))
    selector = _string_mapping(spec.get("selector"))
    ports = []
    for value in spec.get("ports", []):
        if not isinstance(value, dict) or not isinstance(value.get("port"), int):
            continue
        ports.append(
            {
                "name": str(value.get("name", "")),
                "port": value["port"],
                "target_port": value.get("targetPort"),
            }
        )
    matching_workloads = [
        {"name": workload["name"], "kind": workload["kind"]}
        for workload in workloads
        if selector and all(workload["labels"].get(key) == value for key, value in selector.items())
    ]
    return {
        "name": str(metadata.get("name", "")),
        "type": str(spec.get("type", "ClusterIP")),
        "selector": selector,
        "ports": ports,
        "workloads": matching_workloads,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_mapping(value: Any) -> dict[str, str]:
    return {str(key): str(item) for key, item in _mapping(value).items()}


def _last_line(value: str) -> str:
    return next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")[:500]
