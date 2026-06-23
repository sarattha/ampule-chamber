"""Generic Kubernetes preflight checks for live chamber assessments."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

PRODUCTION_CONTEXT_FRAGMENTS = ("prod", "production", "aks-prod", "prd", "live")
CHAMBER_NAMESPACE_PREFIX = "chamber-"


class KubernetesPreflightError(RuntimeError):
    """Raised when Kubernetes preflight input is unsafe."""


class CommandRunner(Protocol):
    """Subprocess boundary used by Kubernetes preflight checks."""

    def run(
        self, command: tuple[str, ...], *, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and return the completed process."""


@dataclass(frozen=True)
class KubernetesPreflightCheck:
    """One kubectl preflight command result."""

    name: str
    command: tuple[str, ...]
    exit_status: int
    stdout: str
    stderr: str
    passed: bool


@dataclass(frozen=True)
class KubernetesPreflightResult:
    """Reportable preflight result for a Kubernetes assessment."""

    context: str
    namespace: str
    ready: bool
    blockers: tuple[str, ...]
    checks: tuple[KubernetesPreflightCheck, ...]


def validate_kubernetes_preflight_target(context: str, namespace: str) -> None:
    """Validate static Kubernetes safety inputs before running kubectl."""

    if not context.strip():
        raise KubernetesPreflightError("Kubernetes mode requires an explicit --context")
    lowered = context.lower()
    if any(fragment in lowered for fragment in PRODUCTION_CONTEXT_FRAGMENTS):
        raise KubernetesPreflightError(f"refusing unsafe Kubernetes context {context!r}")
    if not namespace.startswith(CHAMBER_NAMESPACE_PREFIX):
        raise KubernetesPreflightError(
            f"Kubernetes mode requires a chamber-owned namespace starting with "
            f"{CHAMBER_NAMESPACE_PREFIX!r}; got {namespace!r}"
        )


def run_kubernetes_preflight(
    *,
    context: str,
    namespace: str,
    runner: CommandRunner,
) -> KubernetesPreflightResult:
    """Run kubectl preflight checks and return redacted reportable evidence."""

    validate_kubernetes_preflight_target(context, namespace)
    checks = tuple(
        _run_check(name, command, runner) for name, command in _commands(context, namespace)
    )
    blockers = tuple(
        f"{check.name} failed with exit status {check.exit_status}"
        for check in checks
        if not check.passed
    )
    return KubernetesPreflightResult(
        context=context,
        namespace=namespace,
        ready=not blockers,
        blockers=blockers,
        checks=checks,
    )


def preflight_to_evidence(result: KubernetesPreflightResult) -> dict[str, object]:
    """Convert a preflight result into JSON-safe evidence."""

    return {
        "context": result.context,
        "namespace": result.namespace,
        "ready": result.ready,
        "blockers": list(result.blockers),
        "checks": [
            {
                "name": check.name,
                "command": list(check.command),
                "exit_status": check.exit_status,
                "stdout": check.stdout,
                "stderr": check.stderr,
                "passed": check.passed,
            }
            for check in result.checks
        ],
    }


def _commands(context: str, namespace: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    base = ("kubectl", "--context", context)
    return (
        ("current-context", ("kubectl", "config", "current-context")),
        ("cluster-info", (*base, "cluster-info")),
        ("can-create-namespace", (*base, "auth", "can-i", "create", "namespace")),
        (
            "can-create-deployment",
            (*base, "auth", "can-i", "create", "deployment", "-n", namespace),
        ),
        (
            "can-create-service",
            (*base, "auth", "can-i", "create", "service", "-n", namespace),
        ),
        (
            "can-create-configmap",
            (*base, "auth", "can-i", "create", "configmap", "-n", namespace),
        ),
        ("can-get-pods", (*base, "auth", "can-i", "get", "pods", "-n", namespace)),
        ("can-get-events", (*base, "auth", "can-i", "get", "events", "-n", namespace)),
        ("can-get-pod-logs", (*base, "auth", "can-i", "get", "pods/log", "-n", namespace)),
        ("can-delete-namespace", (*base, "auth", "can-i", "delete", "namespace")),
        ("server-version", (*base, "version", "-o", "json")),
    )


def _run_check(
    name: str,
    command: tuple[str, ...],
    runner: CommandRunner,
) -> KubernetesPreflightCheck:
    completed = runner.run(command)
    stdout = _redact(completed.stdout)
    stderr = _redact(completed.stderr)
    passed = completed.returncode == 0 and _auth_allowed(command, stdout)
    return KubernetesPreflightCheck(
        name=name,
        command=command,
        exit_status=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        passed=passed,
    )


def _auth_allowed(command: tuple[str, ...], stdout: str) -> bool:
    if "can-i" not in command:
        return True
    return stdout.strip().lower() == "yes"


def _redact(value: str) -> str:
    lines = []
    for line in value.splitlines():
        if any(marker in line.upper() for marker in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")):
            lines.append("<redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)
