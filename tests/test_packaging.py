from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SCENARIOS = ROOT / "scenarios"
PACKAGED_SCENARIOS = ROOT / "chamber/control_plane/bundled_scenarios"


class PackagingTests(unittest.TestCase):
    def test_packaged_scenarios_match_canonical_scenarios(self) -> None:
        canonical = {
            path.name: path.read_bytes() for path in sorted(CANONICAL_SCENARIOS.glob("*.yaml"))
        }
        packaged = {
            path.name: path.read_bytes() for path in sorted(PACKAGED_SCENARIOS.glob("*.yaml"))
        }

        self.assertTrue(canonical)
        self.assertEqual(packaged, canonical)

    def test_installed_wheel_lists_bundled_scenarios(self) -> None:
        expected_ids = {
            yaml.safe_load(path.read_text(encoding="utf-8"))["metadata"]["id"]
            for path in CANONICAL_SCENARIOS.glob("*.yaml")
        }
        expected_files = {path.name for path in CANONICAL_SCENARIOS.glob("*.yaml")}
        with TemporaryDirectory() as tmp:
            temporary = Path(tmp)
            dist = temporary / "dist"
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(dist)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheel = next(dist.glob("*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                prefix = "chamber/control_plane/bundled_scenarios/"
                bundled_files = {
                    name.removeprefix(prefix)
                    for name in archive.namelist()
                    if name.startswith(prefix) and name.endswith(".yaml")
                }
            self.assertEqual(bundled_files, expected_files)

            installed = temporary / "installed"
            subprocess.run(
                ["uv", "pip", "install", "--target", str(installed), "--no-deps", str(wheel)],
                cwd=temporary,
                check=True,
                capture_output=True,
                text=True,
            )
            script = """
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from chamber.control_plane.server import PACKAGE_DIR, create_app

with TemporaryDirectory() as workspace:
    with TestClient(create_app(Path(workspace))) as client:
        response = client.get('/api/v1/scenarios')
        response.raise_for_status()
        print(json.dumps({
            'package_dir': str(PACKAGE_DIR),
            'scenarios': response.json()['scenarios'],
        }))
"""
            environment = dict(os.environ, PYTHONPATH=str(installed))
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

        self.assertTrue(Path(payload["package_dir"]).is_relative_to(installed.resolve()))
        self.assertEqual(
            {item["id"] for item in payload["scenarios"] if item["source"] == "bundled"},
            expected_ids,
        )


if __name__ == "__main__":
    unittest.main()
