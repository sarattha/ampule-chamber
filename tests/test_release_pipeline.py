from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleasePipelineTest(unittest.TestCase):
    def test_container_builds_embedded_tools_from_source(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ARG GO_VERSION=1.26.7", dockerfile)
        self.assertIn("COPY tools/kubectl ./", dockerfile)
        self.assertIn("COPY tools/k6 ./", dockerfile)
        self.assertIn(
            'RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/k6 .', dockerfile
        )
        self.assertNotIn("dl.k8s.io/release", dockerfile)
        self.assertNotIn("github.com/grafana/k6/releases/download", dockerfile)

    def test_embedded_tool_builds_pin_fixed_security_dependencies(self) -> None:
        kubectl_module = (ROOT / "tools/kubectl/go.mod").read_text(encoding="utf-8")
        k6_module = (ROOT / "tools/k6/go.mod").read_text(encoding="utf-8")

        self.assertIn("golang.org/x/net v0.56.0", kubectl_module)
        self.assertIn("golang.org/x/crypto v0.55.0", k6_module)
        self.assertIn("golang.org/x/text v0.39.0", kubectl_module)
        self.assertIn("golang.org/x/text v0.41.0", k6_module)
        self.assertIn("google.golang.org/grpc v1.83.2", k6_module)

    def test_release_scan_gates_registry_publication(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

        build = workflow.index("- name: Build release candidate image")
        scan = workflow.index("- name: Scan release candidate image")
        login = workflow.index("- name: Log in to GitHub Container Registry")
        publish = workflow.index("- name: Publish verified control-plane image")

        self.assertLess(build, scan)
        self.assertLess(scan, login)
        self.assertLess(login, publish)

    def test_pull_request_ci_scans_built_image(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        build = workflow.index("- name: Build control-plane image")
        scan = workflow.index("- name: Scan control-plane image")
        smoke = workflow.index("- name: Smoke test control-plane image")

        self.assertLess(build, scan)
        self.assertLess(scan, smoke)

    def test_docs_workflow_configures_pages_before_upload(self) -> None:
        workflow = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")

        configure = workflow.index("uses: actions/configure-pages@v5")
        upload = workflow.index("uses: actions/upload-pages-artifact@v3")

        self.assertLess(configure, upload)


if __name__ == "__main__":
    unittest.main()
