from __future__ import annotations

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from chamber.control_plane.scenarios import (
    MAX_SCENARIO_BYTES,
    ScenarioCatalog,
    ScenarioCatalogError,
    compatibility_warnings,
    normalize_document,
    parse_scenario_document,
)
from chamber.control_plane.server import _ui_journeys, create_app
from chamber.workflow import infer_config, load_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REPO = ROOT / "examples/sample-service"
BUNDLED = ROOT / "scenarios"


def _config(scenario_id: str = "saved-health") -> dict[str, Any]:
    config = infer_config(EXAMPLE_REPO)
    config["scenarioId"] = scenario_id
    config["scenario"] = {
        "id": scenario_id,
        "name": "Saved health",
        "description": "Reusable health exercise.",
        "tags": ["health", "baseline"],
        "source": "custom",
        "revision": "draft",
        "requiredSignals": ["logs", "request_latency"],
    }
    return config


class ScenarioNormalizationTests(unittest.TestCase):
    def test_bundled_scenario_normalizes_to_editable_projection(self) -> None:
        document = parse_scenario_document(
            (BUNDLED / "external-text-translation.yaml").read_text(encoding="utf-8")
        )
        normalized = normalize_document(document, source="imported", validate_journeys=_ui_journeys)
        self.assertEqual(normalized["kind"], "Scenario")
        self.assertEqual(normalized["identity"]["id"], "external-text-translation-001")
        self.assertEqual(normalized["journeys"][0]["path"], "/translations")
        self.assertEqual(normalized["journeys"][0]["requestEncoding"], "json")
        self.assertEqual(normalized["recommendedFault"], "none")
        self.assertIn("queue_depth", normalized["requiredSignals"])

    def test_scenario_projection_enforces_declared_safety_limits(self) -> None:
        excessive_vus = parse_scenario_document(
            (BUNDLED / "baseline-health.yaml").read_text(encoding="utf-8")
        )
        excessive_vus["safety"]["maxVirtualUsers"] = "4"
        with self.assertRaisesRegex(
            ScenarioCatalogError, "requires 5 VUs.*maxVirtualUsers allows 4"
        ):
            normalize_document(excessive_vus, source="imported", validate_journeys=_ui_journeys)

        excessive_duration = parse_scenario_document(
            (BUNDLED / "baseline-health.yaml").read_text(encoding="utf-8")
        )
        excessive_duration["safety"]["maxDuration"] = "2m"
        with self.assertRaisesRegex(
            ScenarioCatalogError, "duration 180s exceeds safety.maxDuration 2m"
        ):
            normalize_document(
                excessive_duration, source="imported", validate_journeys=_ui_journeys
            )

        invalid_limit = parse_scenario_document(
            (BUNDLED / "baseline-health.yaml").read_text(encoding="utf-8")
        )
        invalid_limit["safety"]["maxVirtualUsers"] = "many"
        with self.assertRaisesRegex(ScenarioCatalogError, "must be a positive integer"):
            normalize_document(invalid_limit, source="imported", validate_journeys=_ui_journeys)

    def test_config_uses_same_journey_validation_and_faults_remain_recommendations(self) -> None:
        config = _config()
        config["runtime"] = {
            "provider": "kubernetes",
            "mode": "attach",
            "kubernetesContext": "kind-ampule-chamber",
            "namespace": "qa",
            "cleanup": False,
            "trafficAccess": {
                "mode": "port-forward",
                "service": "sample-service",
                "servicePort": 8080,
            },
            "faults": [{"type": "pod_kill"}],
        }
        config["deployment"]["manifests"] = []
        config["deployment"]["services"] = [{"name": "sample-service", "port": 8080}]
        normalized = normalize_document(config, source="user", validate_journeys=_ui_journeys)
        self.assertEqual(normalized["recommendedFault"], "pod_kill")
        self.assertEqual(config["runtime"]["faults"], [{"type": "pod_kill"}])
        warnings = compatibility_warnings(normalized, service_name="another-service")
        self.assertTrue(any("remains disabled" in item for item in warnings))
        self.assertTrue(any("differs from selected service" in item for item in warnings))

    def test_invalid_contract_placeholder_mixed_adapter_and_size_are_actionable(self) -> None:
        config = _config()
        config["service"]["name"] = "${TARGET_SERVICE}"
        with self.assertRaisesRegex(ScenarioCatalogError, "resolve target-dependent placeholders"):
            normalize_document(config, source="imported", validate_journeys=_ui_journeys)

        config = _config()
        journeys = config["traffic"]["journeys"]
        journeys.append(
            {
                "name": "relayna",
                "adapter": "relayna",
                "method": "POST",
                "path": "/translations",
                "expectedStatus": 202,
                "body": {"text": "hello"},
                "iterations": 1,
                "vus": 1,
                "durationSeconds": 1,
                "relayna": {
                    "taskIdPath": "task_id",
                    "eventsPath": "/events/{task_id}",
                    "terminalStatuses": ["completed", "failed"],
                    "successStatuses": ["completed"],
                    "timeoutSeconds": 30,
                },
            }
        )
        with self.assertRaisesRegex(ValueError, "cannot mix HTTP and Relayna"):
            normalize_document(config, source="imported", validate_journeys=_ui_journeys)

        invalid = _config()
        invalid["apiVersion"] = "chamber.ampule.dev/v2"
        with self.assertRaisesRegex(ScenarioCatalogError, "apiVersion"):
            normalize_document(invalid, source="imported", validate_journeys=_ui_journeys)
        with self.assertRaisesRegex(ScenarioCatalogError, "256 KiB"):
            parse_scenario_document("x" * (MAX_SCENARIO_BYTES + 1))

    def test_parse_and_contract_error_shapes_cover_both_supported_kinds(self) -> None:
        with self.assertRaisesRegex(ScenarioCatalogError, "valid YAML or JSON"):
            parse_scenario_document("[")
        with self.assertRaisesRegex(ScenarioCatalogError, "must be a YAML or JSON mapping"):
            parse_scenario_document("[]")
        with self.assertRaisesRegex(ScenarioCatalogError, "kind must be"):
            normalize_document(
                {"apiVersion": "chamber.ampule.dev/v1alpha1", "kind": "Unknown"},
                source="imported",
                validate_journeys=_ui_journeys,
            )

        invalid_scenario = parse_scenario_document(
            (BUNDLED / "baseline-health.yaml").read_text(encoding="utf-8")
        )
        invalid_scenario.pop("safety")
        with self.assertRaisesRegex(ScenarioCatalogError, "missing required keys"):
            normalize_document(invalid_scenario, source="imported", validate_journeys=_ui_journeys)

        invalid_config = _config()
        invalid_config["deployment"] = {}
        with self.assertRaisesRegex(ScenarioCatalogError, "manifests must"):
            normalize_document(invalid_config, source="imported", validate_journeys=_ui_journeys)

        legacy_identity = _config()
        legacy_identity.pop("scenario")
        legacy_identity.pop("scenarioId")
        normalized = normalize_document(
            legacy_identity, source="imported", validate_journeys=_ui_journeys
        )
        self.assertEqual(normalized["identity"]["id"], "sample-service-assessment")

        unsupported_value = _config()
        unsupported_value["unsupported"] = {"not-json"}
        with self.assertRaisesRegex(ScenarioCatalogError, "unsupported values"):
            normalize_document(unsupported_value, source="imported", validate_journeys=_ui_journeys)

        placeholder_list = _config()
        placeholder_list["scenario"]["tags"] = ["{{ target_tag }}"]
        with self.assertRaisesRegex(ScenarioCatalogError, r"scenario.tags\[0\]"):
            normalize_document(placeholder_list, source="imported", validate_journeys=_ui_journeys)

    def test_legacy_execution_config_without_api_version_remains_supported(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-chamber.yaml"
            config = _config()
            config.pop("apiVersion")
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

            loaded = load_config(path, require_repo=False)

        self.assertEqual(loaded["kind"], "ChamberConfig")
        self.assertNotIn("apiVersion", loaded)
        with self.assertRaisesRegex(ScenarioCatalogError, "apiVersion"):
            normalize_document(loaded, source="imported", validate_journeys=_ui_journeys)


class ScenarioCatalogPersistenceTests(unittest.TestCase):
    def test_user_catalog_is_atomic_durable_and_requires_explicit_replacement(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            catalog = ScenarioCatalog(workspace, BUNDLED)
            saved = catalog.save(_config(), replace=False, validate_journeys=_ui_journeys)
            revision = saved["revision"]
            self.assertTrue((workspace / "scenarios/saved-health.yaml").is_file())
            self.assertFalse(list((workspace / "scenarios").glob("*.tmp")))
            with self.assertRaisesRegex(FileExistsError, "confirm replacement"):
                catalog.save(_config(), replace=False, validate_journeys=_ui_journeys)

            updated = _config()
            updated["scenario"]["description"] = "Updated safely."
            replaced = catalog.save(updated, replace=True, validate_journeys=_ui_journeys)
            self.assertNotEqual(replaced["revision"], revision)

            restarted = ScenarioCatalog(workspace, BUNDLED)
            loaded = restarted.read("user", "saved-health", _ui_journeys)
            self.assertEqual(loaded["identity"]["description"], "Updated safely.")
            sources = {item["source"] for item in restarted.list(_ui_journeys)}
            self.assertEqual(sources, {"bundled", "user"})
            with self.assertRaises(ScenarioCatalogError):
                restarted.read("user", "../escape", _ui_journeys)

    def test_concurrent_non_replacing_saves_publish_exactly_one_document(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            catalog = ScenarioCatalog(workspace, BUNDLED)
            first = _config("concurrent-health")
            first["scenario"]["description"] = "First contender"
            second = _config("concurrent-health")
            second["scenario"]["description"] = "Second contender"
            publish_barrier = Barrier(2)
            real_link = os.link

            def synchronized_link(source: str | Path, destination: str | Path) -> None:
                publish_barrier.wait(timeout=5)
                real_link(source, destination)

            def save(document: dict[str, Any]) -> tuple[str, str]:
                try:
                    result = catalog.save(
                        document,
                        replace=False,
                        validate_journeys=_ui_journeys,
                    )
                except FileExistsError as exc:
                    return "collision", str(exc)
                return "saved", str(result["identity"]["description"])

            with (
                patch("chamber.control_plane.scenarios.os.link", side_effect=synchronized_link),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                outcomes = list(executor.map(save, (first, second)))

            saved = [value for status, value in outcomes if status == "saved"]
            collisions = [value for status, value in outcomes if status == "collision"]
            self.assertEqual(len(saved), 1)
            self.assertEqual(len(collisions), 1)
            self.assertIn("confirm replacement explicitly", collisions[0])
            loaded = catalog.read("user", "concurrent-health", _ui_journeys)
            self.assertEqual(loaded["identity"]["description"], saved[0])
            self.assertFalse(list((workspace / "scenarios").glob("*.tmp")))

    def test_catalog_skips_invalid_documents_and_rejects_invalid_operations(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled"
            bundled.mkdir()
            (bundled / "invalid.yaml").write_text("[", encoding="utf-8")
            catalog = ScenarioCatalog(root / "workspace", bundled)
            self.assertEqual(catalog.list(_ui_journeys), ())
            with self.assertRaises(FileNotFoundError):
                catalog.read("user", "missing", _ui_journeys)
            with self.assertRaisesRegex(ScenarioCatalogError, "source must be"):
                catalog.read("external", "missing", _ui_journeys)

            scenario = parse_scenario_document(
                (BUNDLED / "baseline-health.yaml").read_text(encoding="utf-8")
            )
            with self.assertRaisesRegex(ScenarioCatalogError, "saved as a ChamberConfig"):
                catalog.save(scenario, replace=False, validate_journeys=_ui_journeys)

            missing_bundled = ScenarioCatalog(root / "another-workspace", root / "missing")
            self.assertEqual(missing_bundled.list(_ui_journeys), ())

            fixed = _config("fixed-load")
            fixed["traffic"]["journeys"] = [
                {
                    "name": "fixed",
                    "method": "GET",
                    "path": "/healthz",
                    "expectedStatus": 200,
                    "vus": 3,
                    "iterations": 6,
                    "durationSeconds": 12,
                }
            ]
            catalog.save(fixed, replace=False, validate_journeys=_ui_journeys)
            metadata = next(
                item for item in catalog.list(_ui_journeys) if item["id"] == "fixed-load"
            )
            self.assertEqual(metadata["maxVirtualUsers"], 3)
            self.assertEqual(metadata["expectedDuration"], "12s")


class ScenarioControlPlaneTests(unittest.TestCase):
    def test_api_ui_planning_persistence_and_run_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with TestClient(create_app(workspace)) as client:
                page = client.get("/new")
                self.assertIn("Select saved scenario", page.text)
                self.assertIn("Import scenario", page.text)
                self.assertIn("Scenario ID", page.text)
                script = (ROOT / "chamber/control_plane/static/app.js").read_text(encoding="utf-8")
                apply_start = script.index("const applyScenario = projection =>")
                apply_end = script.index("const filteredScenarios", apply_start)
                apply_source = script[apply_start:apply_end]
                self.assertIn('form.elements.fault_type.value = "none";', apply_source)
                self.assertLess(
                    apply_source.index('form.elements.fault_type.value = "none";'),
                    apply_source.index("projection.recommendedFault"),
                )
                csrf = str(client.cookies["ampule_csrf"])
                headers = {"X-CSRF-Token": csrf}

                listed = client.get("/api/v1/scenarios").json()["scenarios"]
                self.assertTrue(any(item["source"] == "bundled" for item in listed))
                selected = client.get(
                    "/api/v1/scenarios/bundled/baseline-health-001",
                    params={"service_name": "payments"},
                )
                self.assertEqual(selected.status_code, 200)
                self.assertTrue(selected.json()["warnings"])
                self.assertEqual(
                    client.get("/api/v1/scenarios/user/missing").status_code,
                    404,
                )
                self.assertEqual(
                    client.get("/api/v1/scenarios/external/missing").status_code,
                    400,
                )

                content = (BUNDLED / "external-text-translation.yaml").read_text(encoding="utf-8")
                imported = client.post(
                    "/api/v1/scenarios/validate",
                    json={"content": content, "service_name": "translation-service"},
                    headers=headers,
                )
                self.assertEqual(imported.status_code, 200, imported.text)
                denied = client.post(
                    "/api/v1/scenarios/validate",
                    json={"content": content},
                )
                self.assertEqual(denied.status_code, 403)
                invalid_import = client.post(
                    "/api/v1/scenarios/validate",
                    json={"content": "kind: Invalid"},
                    headers=headers,
                )
                self.assertEqual(invalid_import.status_code, 400)

                created = client.post(
                    "/api/v1/scenarios",
                    json={"document": _config()},
                    headers=headers,
                )
                self.assertEqual(created.status_code, 201, created.text)
                collision = client.post(
                    "/api/v1/scenarios",
                    json={"document": _config()},
                    headers=headers,
                )
                self.assertEqual(collision.status_code, 409)
                replaced = client.post(
                    "/api/v1/scenarios",
                    json={"document": _config(), "replace": True},
                    headers=headers,
                )
                self.assertEqual(replaced.status_code, 201)
                invalid_save = client.post(
                    "/api/v1/scenarios",
                    json={"document": {"kind": "Invalid"}},
                    headers=headers,
                )
                self.assertEqual(invalid_save.status_code, 400)

                planned = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(EXAMPLE_REPO),
                        "service_name": "sample-service",
                        "scenario_id": "ui-health",
                        "scenario_name": "UI health",
                        "scenario_description": "Saved from the journey editor.",
                        "scenario_tags": "ui, health",
                        "scenario_source": "imported",
                        "scenario_revision": "source-revision",
                        "required_signals_json": json.dumps(["logs", "request_latency"]),
                        "save_scenario": "new",
                        "journeys_json": json.dumps(
                            [
                                {
                                    "name": "health",
                                    "method": "GET",
                                    "path": "/healthz",
                                    "expectedStatus": 200,
                                    "stages": [
                                        {"duration": "10s", "targetVus": 2},
                                        {"duration": "5s", "targetVus": 0},
                                    ],
                                }
                            ]
                        ),
                    },
                    follow_redirects=False,
                )
                self.assertEqual(planned.status_code, 303, planned.text)
                run_id = planned.headers["location"].split("/")[2].split("?")[0]
                config = yaml.safe_load(
                    (workspace / "runs" / run_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                metadata = json.loads(
                    (workspace / "runs" / run_id / "run-metadata.json").read_text(encoding="utf-8")
                )
                run = json.loads(
                    (workspace / "runs" / run_id / "run.json").read_text(encoding="utf-8")
                )
                self.assertEqual(config["scenarioId"], "ui-health")
                self.assertEqual(config["scenario"]["source"], "user")
                self.assertEqual(
                    config["scenario"]["origin"],
                    {"source": "imported", "revision": "source-revision"},
                )
                self.assertEqual(metadata["scenario"]["id"], "ui-health")
                self.assertEqual(metadata["scenario"]["origin"], config["scenario"]["origin"])
                self.assertEqual(run["scenario_id"], "ui-health")
                self.assertEqual(run["scenario_source"], "user")
                self.assertEqual(run["scenario_origin"], config["scenario"]["origin"])
                self.assertEqual(
                    client.get("/api/v1/scenarios/user/ui-health").status_code,
                    200,
                )

                user_projection = client.get("/api/v1/scenarios/user/ui-health").json()
                user_plan_data = {
                    "_csrf": csrf,
                    "repo": str(EXAMPLE_REPO),
                    "service_name": user_projection["targetService"],
                    "scenario_id": user_projection["identity"]["id"],
                    "scenario_name": user_projection["identity"]["name"],
                    "scenario_description": user_projection["identity"]["description"],
                    "scenario_tags": ", ".join(user_projection["identity"]["tags"]),
                    "scenario_source": "user",
                    "scenario_revision": user_projection["revision"],
                    "required_signals_json": json.dumps(user_projection["requiredSignals"]),
                    "agents_mode": user_projection["agentMode"],
                    "journeys_json": json.dumps(user_projection["journeys"]),
                }
                direct_user = client.post(
                    "/ui/plan",
                    data=user_plan_data,
                    follow_redirects=False,
                )
                self.assertEqual(direct_user.status_code, 303, direct_user.text)
                direct_user_id = direct_user.headers["location"].split("/")[2].split("?")[0]
                direct_user_config = yaml.safe_load(
                    (workspace / "runs" / direct_user_id / "chamber.yaml").read_text(
                        encoding="utf-8"
                    )
                )
                direct_user_metadata = json.loads(
                    (workspace / "runs" / direct_user_id / "run-metadata.json").read_text(
                        encoding="utf-8"
                    )
                )
                direct_user_run = json.loads(
                    (workspace / "runs" / direct_user_id / "run.json").read_text(encoding="utf-8")
                )
                self.assertEqual(direct_user_config["scenario"]["source"], "user")
                self.assertEqual(
                    direct_user_config["scenario"].get("origin"), user_projection.get("origin")
                )
                self.assertEqual(
                    direct_user_config["scenario"]["revision"], user_projection["revision"]
                )
                self.assertEqual(direct_user_metadata["scenario"]["source"], "user")
                self.assertEqual(direct_user_run["scenario_source"], "user")

                edited_user_data = dict(user_plan_data)
                edited_user_journeys = json.loads(user_plan_data["journeys_json"])
                edited_user_journeys[0]["path"] = "/edited-healthz"
                edited_user_data["journeys_json"] = json.dumps(edited_user_journeys)
                edited_user = client.post(
                    "/ui/plan",
                    data=edited_user_data,
                    follow_redirects=False,
                )
                self.assertEqual(edited_user.status_code, 303, edited_user.text)
                edited_user_id = edited_user.headers["location"].split("/")[2].split("?")[0]
                edited_user_config = yaml.safe_load(
                    (workspace / "runs" / edited_user_id / "chamber.yaml").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(edited_user_config["scenario"]["source"], "derived")
                self.assertEqual(
                    edited_user_config["scenario"]["origin"],
                    {"source": "user", "revision": user_projection["revision"]},
                )

                no_confirmation = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(EXAMPLE_REPO),
                        "scenario_id": "ui-health",
                        "scenario_name": "UI health",
                        "save_scenario": "replace",
                    },
                )
                self.assertEqual(no_confirmation.status_code, 400)
                self.assertIn("explicit confirmation", no_confirmation.text)

                invalid_signals = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(EXAMPLE_REPO),
                        "required_signals_json": "not-json",
                    },
                )
                self.assertEqual(invalid_signals.status_code, 400)
                self.assertIn("Required signals must be valid JSON", invalid_signals.text)

                invalid_signal_shape = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(EXAMPLE_REPO),
                        "required_signals_json": "{}",
                    },
                )
                self.assertEqual(invalid_signal_shape.status_code, 400)
                self.assertIn("JSON array", invalid_signal_shape.text)

                edited = client.post(
                    "/ui/plan",
                    data={
                        "_csrf": csrf,
                        "repo": str(EXAMPLE_REPO),
                        "scenario_id": "baseline-health-001",
                        "scenario_name": "Edited baseline",
                        "scenario_source": "bundled",
                        "scenario_revision": "bundled-origin-revision",
                        "journeys_json": json.dumps(
                            [
                                {
                                    "name": "edited-health",
                                    "method": "GET",
                                    "path": "/readyz",
                                    "expectedStatus": 204,
                                    "stages": [
                                        {"duration": "7s", "targetVus": 3},
                                        {"duration": "2s", "targetVus": 0},
                                    ],
                                }
                            ]
                        ),
                    },
                    follow_redirects=False,
                )
                self.assertEqual(edited.status_code, 303, edited.text)
                edited_id = edited.headers["location"].split("/")[2].split("?")[0]
                edited_config = yaml.safe_load(
                    (workspace / "runs" / edited_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                edited_metadata = json.loads(
                    (workspace / "runs" / edited_id / "run-metadata.json").read_text(
                        encoding="utf-8"
                    )
                )
                edited_run = json.loads(
                    (workspace / "runs" / edited_id / "run.json").read_text(encoding="utf-8")
                )
                provenance = edited_config["scenario"]
                self.assertEqual(provenance["source"], "derived")
                self.assertEqual(
                    provenance["origin"],
                    {"source": "bundled", "revision": "bundled-origin-revision"},
                )
                self.assertNotEqual(provenance["revision"], "bundled-origin-revision")
                self.assertEqual(len(provenance["revision"]), 16)
                recomputed = normalize_document(
                    edited_config, source="derived", validate_journeys=_ui_journeys
                )
                self.assertEqual(provenance["revision"], recomputed["revision"])
                self.assertEqual(edited_metadata["scenario"]["source"], "derived")
                self.assertEqual(edited_metadata["scenario"]["revision"], provenance["revision"])
                self.assertEqual(edited_metadata["scenario"]["origin"], provenance["origin"])
                self.assertEqual(edited_run["scenario_source"], "derived")
                self.assertEqual(edited_run["scenario_revision"], provenance["revision"])
                self.assertEqual(edited_run["scenario_origin"], provenance["origin"])


if __name__ == "__main__":
    unittest.main()
