from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleasePipelineTest(unittest.TestCase):
    def test_container_builds_embedded_tools_from_source(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ARG GO_VERSION=1.26.5", dockerfile)
        self.assertIn("COPY tools/kubectl ./", dockerfile)
        self.assertIn('go install -trimpath "go.k6.io/k6/v2@${K6_VERSION}"', dockerfile)
        self.assertNotIn("dl.k8s.io/release", dockerfile)
        self.assertNotIn("github.com/grafana/k6/releases/download", dockerfile)

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
