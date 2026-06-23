from __future__ import annotations

import subprocess
import unittest

from chamber.environment.preflight import (
    KubernetesPreflightError,
    preflight_to_evidence,
    run_kubernetes_preflight,
    validate_kubernetes_preflight_target,
)


class FakeRunner:
    def __init__(
        self,
        responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        return self.responses.get(command, _completed(command, stdout="yes\n"))


class KubernetesPreflightTests(unittest.TestCase):
    def test_static_target_validation_rejects_missing_unsafe_and_non_chamber_inputs(self) -> None:
        validate_kubernetes_preflight_target("dev-cluster", "chamber-target-abc123")

        with self.assertRaisesRegex(KubernetesPreflightError, "explicit --context"):
            validate_kubernetes_preflight_target("", "chamber-target")

        with self.assertRaisesRegex(KubernetesPreflightError, "unsafe Kubernetes context"):
            validate_kubernetes_preflight_target("aks-prod-east", "chamber-target")

        with self.assertRaisesRegex(KubernetesPreflightError, "chamber-owned namespace"):
            validate_kubernetes_preflight_target("dev-cluster", "default")

    def test_preflight_runs_required_kubectl_checks(self) -> None:
        runner = FakeRunner(
            {
                ("kubectl", "config", "current-context"): _completed(
                    ("kubectl", "config", "current-context"),
                    stdout="dev-cluster\n",
                ),
                ("kubectl", "--context", "dev-cluster", "cluster-info"): _completed(
                    ("kubectl", "--context", "dev-cluster", "cluster-info"),
                    stdout="Kubernetes control plane is running\n",
                ),
                ("kubectl", "--context", "dev-cluster", "version", "-o", "json"): _completed(
                    ("kubectl", "--context", "dev-cluster", "version", "-o", "json"),
                    stdout='{"serverVersion":{"gitVersion":"v1.30.0"}}\n',
                ),
            }
        )

        result = run_kubernetes_preflight(
            context="dev-cluster",
            namespace="chamber-target-abc123",
            runner=runner,
        )

        self.assertTrue(result.ready)
        self.assertEqual(result.blockers, ())
        self.assertIn(
            (
                "kubectl",
                "--context",
                "dev-cluster",
                "auth",
                "can-i",
                "create",
                "deployment",
                "-n",
                "chamber-target-abc123",
            ),
            runner.commands,
        )
        evidence = preflight_to_evidence(result)
        self.assertTrue(evidence["ready"])
        self.assertEqual(evidence["context"], "dev-cluster")

    def test_preflight_blocks_failed_or_denied_permissions(self) -> None:
        denied = (
            "kubectl",
            "--context",
            "dev-cluster",
            "auth",
            "can-i",
            "get",
            "events",
            "-n",
            "chamber-target-abc123",
        )
        runner = FakeRunner({denied: _completed(denied, stdout="no\n")})

        result = run_kubernetes_preflight(
            context="dev-cluster",
            namespace="chamber-target-abc123",
            runner=runner,
        )

        self.assertFalse(result.ready)
        self.assertIn("can-get-events failed", result.blockers[0])

    def test_preflight_evidence_redacts_secret_like_output(self) -> None:
        command = ("kubectl", "--context", "dev-cluster", "cluster-info")
        runner = FakeRunner(
            {
                command: _completed(
                    command,
                    stdout="TOKEN=plain-secret\ncontrol plane ok\n",
                )
            }
        )

        result = run_kubernetes_preflight(
            context="dev-cluster",
            namespace="chamber-target-abc123",
            runner=runner,
        )

        evidence = preflight_to_evidence(result)
        rendered = str(evidence)
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("plain-secret", rendered)


def _completed(
    command: tuple[str, ...],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


if __name__ == "__main__":
    unittest.main()
