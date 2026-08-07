from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from fastapi.testclient import TestClient

from chamber.control_plane.goals import goal_catalog, propose_goal
from chamber.control_plane.server import create_app

ROOT = Path(__file__).parents[1]


class ReliabilityGoalProposalTests(unittest.TestCase):
    def test_all_presets_are_bounded_visible_and_fault_safe(self) -> None:
        expected_ids = {
            "baseline_readiness",
            "pod_recovery",
            "dependency_degradation",
            "backpressure",
            "memory_oom",
            "latency_error_regression",
        }
        self.assertEqual({item["id"] for item in goal_catalog()}, expected_ids)

        for goal_id in expected_ids:
            with self.subTest(goal=goal_id):
                proposal = propose_goal(
                    goal_id,
                    service_name="payments-api",
                    workload_name="payments-api",
                    service_port=8080,
                    repository_available=True,
                    attach_mode=True,
                    discovery_complete=True,
                    dependency_names=("postgres",),
                    telemetry_available=("prometheus",),
                )
                journey = proposal["journeys"][0]
                stage_vus = [stage["targetVus"] for stage in journey["stages"]]
                duration = sum(int(stage["duration"][:-1]) for stage in journey["stages"])

                self.assertEqual(proposal["selectedFault"], "none")
                self.assertTrue(proposal["safety"]["faultsRequireExplicitSelection"])
                self.assertEqual(proposal["safety"]["maxVirtualUsers"], max(stage_vus))
                self.assertEqual(proposal["safety"]["maxDurationSeconds"], duration)
                self.assertLessEqual(max(stage_vus), 25)
                self.assertLessEqual(duration, 300)
                self.assertTrue(proposal["expectedOutcomes"])
                self.assertTrue(proposal["requiredEvidence"])
                self.assertTrue(proposal["faultSummary"])
                self.assertEqual(proposal["requestPreview"]["service"], "payments-api")
                self.assertEqual(proposal["requestPreview"]["port"], 8080)

    def test_partial_discovery_and_attach_without_repository_are_explicit(self) -> None:
        partial = propose_goal(
            "pod_recovery",
            service_name="payments-api",
            service_port=8080,
            attach_mode=True,
            discovery_complete=False,
        )

        self.assertEqual(partial["recommendedFault"], "pod_kill")
        self.assertEqual(partial["selectedFault"], "none")
        self.assertIn("Select or discover the backing workload.", partial["missingInputs"])
        self.assertTrue(
            any("attached without a repository" in item for item in partial["assumptions"])
        )
        self.assertTrue(any("not fully discovered" in item for item in partial["assumptions"]))

        dependency = propose_goal(
            "dependency_degradation",
            service_name="payments-api",
            workload_name="payments-api",
            service_port=8080,
            attach_mode=True,
            discovery_complete=True,
            dependency_names=("postgres", "redis"),
        )
        self.assertFalse(
            any("Choose the dependency" in item for item in dependency["missingInputs"])
        )
        self.assertTrue(any("postgres, redis" in item for item in dependency["assumptions"]))

    def test_goal_specific_gaps_do_not_invent_unsupported_faults(self) -> None:
        memory = propose_goal(
            "memory_oom",
            service_name="worker",
            workload_name="worker",
            service_port=8080,
            attach_mode=True,
            discovery_complete=True,
        )
        backpressure = propose_goal(
            "backpressure",
            service_name="worker",
            workload_name="worker",
            service_port=8080,
            attach_mode=True,
            discovery_complete=True,
        )

        self.assertEqual(memory["recommendedFault"], "none")
        self.assertTrue(any("memory-pressure" in item for item in memory["missingInputs"]))
        self.assertTrue(any("Prometheus" in item for item in memory["missingInputs"]))
        self.assertEqual(backpressure["journeys"][0]["path"], "/tasks")
        self.assertEqual(backpressure["journeys"][0]["expectedStatus"], 202)
        self.assertTrue(any("request body" in item for item in backpressure["missingInputs"]))
        with self.assertRaisesRegex(ValueError, "unknown reliability goal"):
            propose_goal("unknown")


class ReliabilityGoalControlPlaneTests(unittest.TestCase):
    def test_goal_endpoint_ui_and_server_authoritative_plan_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with TestClient(create_app(workspace)) as client:
                page = client.get("/new")
                self.assertEqual(page.status_code, 200)
                for label in (
                    "Baseline readiness",
                    "Pod recovery",
                    "Dependency degradation",
                    "Queue or task backpressure",
                    "Memory and OOM recovery",
                    "Latency and error regression",
                ):
                    self.assertIn(label, page.text)
                self.assertIn("Basic · goal first", page.text)
                self.assertIn("Advanced · full config", page.text)
                self.assertIn("Generated request preview", page.text)
                self.assertIn("Traffic, fault, recovery", page.text)

                payload = {
                    "goal": "pod_recovery",
                    "service_name": "payments-api",
                    "workload_name": "payments-api",
                    "service_port": 8080,
                    "attach_mode": True,
                    "discovery_complete": True,
                }
                denied = client.post("/api/v1/scenarios/propose", json=payload)
                self.assertEqual(denied.status_code, 403)
                csrf = str(client.cookies["ampule_csrf"])
                headers = {"X-CSRF-Token": csrf}
                proposed = client.post("/api/v1/scenarios/propose", json=payload, headers=headers)
                self.assertEqual(proposed.status_code, 200, proposed.text)
                projection = proposed.json()
                self.assertEqual(projection["recommendedFault"], "pod_kill")
                self.assertEqual(projection["selectedFault"], "none")

                plan_data = {
                    "_csrf": csrf,
                    "service_name": "payments-api",
                    "workload_name": "payments-api",
                    "workload_kind": "Deployment",
                    "execution_mode": "kubernetes",
                    "runtime_mode": "attach",
                    "kubernetes_context": "kind-ampule-chamber",
                    "namespace": "payments-stage",
                    "service_port": "8080",
                    "scenario_id": "payments-pod-recovery",
                    "scenario_name": "Payments pod recovery",
                    "required_signals_json": json.dumps(projection["requiredEvidence"]),
                    "journeys_json": json.dumps(projection["journeys"]),
                    "fault_type": "none",
                    "agents_mode": "offline",
                    "agents_exclude_json": json.dumps(["report-writer-agent"]),
                }
                planned = client.post("/ui/plan", data=plan_data, follow_redirects=False)
                self.assertEqual(planned.status_code, 303, planned.text)
                run_id = planned.headers["location"].split("/")[2].split("?")[0]
                config = yaml.safe_load(
                    (workspace / "runs" / run_id / "chamber.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(config["runtime"]["faults"], [])
                self.assertEqual(config["traffic"]["journeys"], projection["journeys"])
                self.assertEqual(config["agents"]["exclude"], ["report-writer-agent"])
                self.assertTrue(config["service"]["repo"].startswith("kubernetes://"))

                invalid_data = dict(plan_data)
                invalid_journeys = json.loads(plan_data["journeys_json"])
                invalid_journeys[0]["path"] = "health-without-leading-slash"
                invalid_data["journeys_json"] = json.dumps(invalid_journeys)
                invalid = client.post("/ui/plan", data=invalid_data)
                self.assertEqual(invalid.status_code, 400)
                self.assertIn("path must start with /", invalid.text)

                unknown = client.post(
                    "/api/v1/scenarios/propose",
                    json={"goal": "unknown"},
                    headers=headers,
                )
                self.assertEqual(unknown.status_code, 400)

    def test_basic_advanced_switch_and_journey_serialization_are_lossless(self) -> None:
        script = (ROOT / "chamber/control_plane/static/app.js").read_text(encoding="utf-8")
        template = (ROOT / "chamber/control_plane/templates/new.html").read_text(encoding="utf-8")
        switch_start = script.index("const setEditorMode = mode =>")
        switch_end = script.index("const ensureScenarioIdentity", switch_start)
        switch_source = script[switch_start:switch_end]

        self.assertIn('basicBuilder.hidden = mode !== "basic";', switch_source)
        self.assertIn('node.hidden = mode !== "advanced"', switch_source)
        self.assertNotIn("replaceChildren", switch_source)
        self.assertNotIn("populateJourney", switch_source)
        self.assertIn("card.dataset.journeyBase = JSON.stringify(journey);", script)
        self.assertIn("...baseJourney", script)
        self.assertIn("...baseMultipart", script)
        self.assertIn("...baseLifecycle", script)
        self.assertIn('form.elements.fault_type.value = "none";', script)
        self.assertIn("form.elements.agents_exclude_json.value = JSON.stringify", script)

        for advanced_field in (
            "HTTP / k6",
            "Relayna task lifecycle",
            "Multipart form + file",
            "Custom stages",
            "Follow-up checks JSON",
            "Attach fault template",
            "Excluded agents JSON",
        ):
            self.assertIn(advanced_field, template)

    def test_repository_target_attach_proposals_follow_runtime_selection(self) -> None:
        script = (ROOT / "chamber/control_plane/static/app.js").read_text(encoding="utf-8")
        context_start = script.index("const proposalContext = async goal =>")
        context_end = script.index("const renderList", context_start)
        context_source = script[context_start:context_end]

        self.assertIn(
            'const kubernetesMode = form.querySelector(\'input[name="execution_mode"]'
            '[value="kubernetes"]\').checked;',
            context_source,
        )
        self.assertIn(
            'const attached = kubernetesMode && form.elements.runtime_mode.value === "attach";',
            context_source,
        )
        self.assertNotIn(
            'const attached = form.querySelector(\'input[name="target_source"]',
            context_source,
        )


if __name__ == "__main__":
    unittest.main()
