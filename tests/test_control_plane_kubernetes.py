from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from chamber.control_plane.discovery import (
    DiscoveryError,
    DiscoverySettings,
    KubernetesDiscovery,
)
from chamber.control_plane.security import load_admin_auth, safe_next_path
from chamber.control_plane.server import create_app

ADMIN_TOKEN = "op_live_ampule_test_token_123456789"


class _DiscoveryRunner:
    def __init__(self, payloads: dict[str, object], *, failure: str | None = None) -> None:
        self.payloads = payloads
        self.failure = failure
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        resource = command[-3]
        if self.failure == resource:
            return subprocess.CompletedProcess(command, 1, "", "forbidden")
        value = self.payloads.get(resource, {"items": []})
        stdout = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(command, 0, stdout, "")


class ControlPlaneAuthenticationTests(unittest.TestCase):
    def test_admin_token_login_cookie_bearer_and_logout(self) -> None:
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), admin_token=ADMIN_TOKEN)
            with TestClient(app) as client:
                protected = client.get("/runs", follow_redirects=False)
                self.assertEqual(protected.status_code, 303)
                self.assertTrue(protected.headers["location"].startswith("/login?"))
                self.assertEqual(client.get("/api/v1/capabilities").status_code, 401)
                self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
                self.assertEqual(client.get("/readyz").json(), {"status": "ready"})

                login_page = client.get("/login")
                self.assertEqual(login_page.status_code, 200)
                self.assertIn("Sign in to the chamber", login_page.text)
                csrf = str(client.cookies.get("ampule_csrf"))
                invalid = client.post(
                    "/login",
                    data={"_csrf": csrf, "admin_token": "wrong", "next": "/new"},
                )
                self.assertEqual(invalid.status_code, 401)
                self.assertIn("not valid", invalid.text)

                signed_in = client.post(
                    "/login",
                    data={"_csrf": csrf, "admin_token": ADMIN_TOKEN, "next": "/new"},
                    follow_redirects=False,
                )
                self.assertEqual(signed_in.status_code, 303)
                self.assertEqual(signed_in.headers["location"], "/new")
                self.assertNotIn(ADMIN_TOKEN, signed_in.headers["set-cookie"])
                self.assertEqual(client.get("/new").status_code, 200)

                logout = client.post("/logout", data={"_csrf": csrf}, follow_redirects=False)
                self.assertEqual(logout.status_code, 303)
                self.assertEqual(client.get("/runs", follow_redirects=False).status_code, 303)

            with TestClient(app) as api_client:
                response = api_client.get(
                    "/api/v1/capabilities",
                    headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("relayna", response.json()["traffic_adapters"])

    def test_token_validation_secure_cookie_and_safe_redirect(self) -> None:
        self.assertEqual(safe_next_path("https://evil.example"), "/runs")
        self.assertEqual(safe_next_path("//evil.example"), "/runs")
        self.assertEqual(safe_next_path("/%5cevil.example"), "/runs")
        self.assertEqual(safe_next_path("/%2f%2fevil.example"), "/runs")
        self.assertEqual(safe_next_path("/runs?id=1"), "/runs?id=1")
        with self.assertRaisesRegex(ValueError, "must start"):
            load_admin_auth("short")
        with patch.dict(os.environ, {"AMPULE_CHAMBER_SECURE_COOKIES": "true"}):
            auth = load_admin_auth(ADMIN_TOKEN)
        self.assertTrue(auth.secure_cookies)
        self.assertTrue(auth.authenticates_token(ADMIN_TOKEN))
        self.assertFalse(auth.authenticates_token("op_live_wrong_token_123456789"))


class KubernetesDiscoveryTests(unittest.TestCase):
    def test_discovers_services_ports_and_matching_workloads(self) -> None:
        runner = _DiscoveryRunner(_payloads())
        discovery = KubernetesDiscovery(
            DiscoverySettings(
                context="in-cluster",
                namespaces=("chamber-external-translation-prep",),
            ),
            runner=runner,
        )

        result = discovery.discover(
            context="in-cluster", namespace="chamber-external-translation-prep"
        )

        self.assertEqual(result["services"][0]["name"], "translation-service")
        self.assertEqual(result["services"][0]["ports"][0]["port"], 8887)
        self.assertEqual(
            result["services"][0]["workloads"],
            [{"name": "translation-service", "kind": "Deployment"}],
        )
        self.assertEqual(result["workloads"][0]["ready_replicas"], 1)
        self.assertEqual(len(runner.commands), 3)

    def test_discovery_rejects_undeclared_context_namespace_and_bad_output(self) -> None:
        settings = DiscoverySettings(context="in-cluster", namespaces=("chamber-stage",))
        discovery = KubernetesDiscovery(settings, runner=_DiscoveryRunner(_payloads()))
        with self.assertRaisesRegex(DiscoveryError, "configured discovery context"):
            discovery.discover(context="kind-other", namespace="chamber-stage")
        with self.assertRaisesRegex(DiscoveryError, "not declared"):
            discovery.discover(context="in-cluster", namespace="chamber-other")
        with self.assertRaisesRegex(DiscoveryError, "not declared"):
            KubernetesDiscovery(
                DiscoverySettings(context="in-cluster", namespaces=()),
                runner=_DiscoveryRunner(_payloads()),
            ).discover(context="in-cluster", namespace="chamber-stage")
        with self.assertRaisesRegex(DiscoveryError, "DNS label"):
            KubernetesDiscovery(
                DiscoverySettings(context="in-cluster", namespaces=()),
                runner=_DiscoveryRunner(_payloads()),
            ).discover(context="in-cluster", namespace="Invalid_Name")

        invalid = KubernetesDiscovery(
            DiscoverySettings(context="in-cluster", namespaces=("chamber-stage",)),
            runner=_DiscoveryRunner({"services": "not-json"}),
        )
        with self.assertRaisesRegex(DiscoveryError, "invalid JSON"):
            invalid.discover(context="in-cluster", namespace="chamber-stage")

        failed = KubernetesDiscovery(
            DiscoverySettings(context="in-cluster", namespaces=("chamber-stage",)),
            runner=_DiscoveryRunner(_payloads(), failure="services"),
        )
        with self.assertRaisesRegex(DiscoveryError, "forbidden"):
            failed.discover(context="in-cluster", namespace="chamber-stage")

    def test_discovery_settings_and_http_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AMPULE_CHAMBER_KUBERNETES_CONTEXT": "kind-ampule-chamber",
                "AMPULE_CHAMBER_DISCOVERY_NAMESPACES": "chamber-one,chamber-two,chamber-one",
            },
        ):
            settings = DiscoverySettings.from_environment()
        self.assertEqual(settings.context, "kind-ampule-chamber")
        self.assertEqual(settings.namespaces, ("chamber-one", "chamber-two"))

        discovery = KubernetesDiscovery(
            DiscoverySettings(context="in-cluster", namespaces=("chamber-stage",)),
            runner=_DiscoveryRunner(_payloads()),
        )
        with TemporaryDirectory() as tmp:
            app = create_app(Path(tmp), discovery=discovery)
            with TestClient(app) as client:
                response = client.get(
                    "/api/v1/kubernetes/discovery",
                    params={"context": "in-cluster", "namespace": "chamber-stage"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["services"][0]["name"], "translation-service")
                rejected = client.get(
                    "/api/v1/kubernetes/discovery",
                    params={"context": "in-cluster", "namespace": "chamber-other"},
                )
                self.assertEqual(rejected.status_code, 400)


def _payloads() -> dict[str, object]:
    return {
        "services": {
            "items": [
                {
                    "metadata": {"name": "translation-service"},
                    "spec": {
                        "type": "ClusterIP",
                        "selector": {"app": "translation-service"},
                        "ports": [{"name": "http", "port": 8887, "targetPort": 8887}],
                    },
                }
            ]
        },
        "deployments.apps": {
            "items": [
                {
                    "metadata": {"name": "translation-service"},
                    "spec": {
                        "replicas": 1,
                        "template": {"metadata": {"labels": {"app": "translation-service"}}},
                    },
                    "status": {"readyReplicas": 1},
                }
            ]
        },
        "statefulsets.apps": {"items": []},
    }


if __name__ == "__main__":
    unittest.main()
