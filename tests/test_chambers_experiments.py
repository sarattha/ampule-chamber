"""Admission, fault recovery, queue identity and report acceptance tests."""

from __future__ import annotations

import io
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from chamber.application.results import build_assessment_result
from chamber.chaos.experiments import (
    FAMILIES,
    ExperimentSession,
    chaos_document,
    validate_experiment,
)
from chamber.control_plane.jobs import AssessmentJobManager
from chamber.control_plane.server import create_app
from chamber.environment.chambers import ChamberProfile, ChamberStore, target_key, validate_budget
from chamber.runs import refresh_evidence_manifest
from tests.test_decision_results import _config, _metadata, _write_required_evidence


def config_for(family="dependency_delay"):
    config = _config()
    config["runtime"].update(mode="attach", prometheusUrl="http://metrics:9090")
    config["deployment"] = {"workloads": [{"name": "payments"}]}
    config["experiment"] = {
        "family": family,
        "pod": "api-0",
        "container": "api",
        "dependencyPod": "redis-0",
        "depthQuery": "queue_depth",
        "ageQuery": "queue_age",
        "queueLabels": {"queue": "tasks"},
    }
    validate_experiment(config)
    return config


class Controller:
    def __init__(self, *, inject=True, recover=True, opt_in=True, failure=""):
        self.inject = inject
        self.recover = recover
        self.opt_in = opt_in
        self.failure = failure
        self.commands = []

    def run(self, command):
        self.commands.append(command)
        payload = {}
        if "pod" in command:
            payload = {
                "metadata": {
                    "labels": {"chamber.ampule.dev/allow-faults": str(self.opt_in).lower()}
                },
                "spec": {"containers": [{"name": "api"}]},
            }
        elif "get" in command:
            payload = {
                "status": {
                    "conditions": [
                        {"type": "AllInjected", "status": str(self.inject).title()},
                        {"type": "AllRecovered", "status": str(self.recover).title()},
                    ]
                }
            }
        return subprocess.CompletedProcess(
            command, 1 if self.failure and self.failure in command else 0, json.dumps(payload), ""
        )


class ChamberExperimentTests(unittest.TestCase):
    def test_profile_persistence_clone_and_rejected_target_budget(self):
        with TemporaryDirectory() as tmp:
            store = ChamberStore(Path(tmp))
            profile = ChamberProfile(
                name="Payments staging",
                context="kind-ampule",
                namespace="payments-test",
                service="payments",
                workload="payments",
                allow_faults=True,
                chaos_mesh=True,
            )
            first = store.create(profile)
            second = store.create(profile.model_copy(update={"name": "Payments recovery"}))
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(len(ChamberStore(Path(tmp)).list()), 2)
            config = config_for()
            store.bind(config, first["id"])
            config["traffic"]["journeys"][0]["vus"] = 101
            with self.assertRaisesRegex(ValueError, "VU budget"):
                validate_budget(config)
            config["runtime"]["namespace"] = "wrong"
            with self.assertRaisesRegex(ValueError, "namespace"):
                store.bind(config, first["id"])
            with self.assertRaises(ValueError):
                store.get("../../escape")
            config = config_for()
            store.bind(config, first["id"])
            config["traffic"]["journeys"][0]["iterations"] = 99
            with self.assertRaisesRegex(ValueError, "duration budget"):
                validate_budget(config)
            config["chamber"]["allow_faults"] = False
            with self.assertRaisesRegex(ValueError, "does not allow"):
                validate_budget(config)
            config["chamber"].update(allow_faults=True, chaos_mesh=False)
            with self.assertRaisesRegex(ValueError, "Chaos Mesh"):
                validate_budget(config)

    def test_admission_serializes_namespace_and_preserves_idempotency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.yaml"
            path.write_text(json.dumps(config_for()))
            manager = AssessmentJobManager(root)
            blocker = threading.Event()
            with patch.object(manager, "_execute", side_effect=lambda _: blocker.wait(5)):
                first = manager.start(path, mode="kubernetes", idempotency_key="retry")
                self.assertEqual(
                    manager.start(path, mode="kubernetes", idempotency_key="retry")["job_id"],
                    first["job_id"],
                )
                with self.assertRaisesRegex(ValueError, "occupied"):
                    AssessmentJobManager(root).start(path, mode="kubernetes")
                blocker.set()
            self.assertEqual(target_key(config_for()), first["target_key"])
            self.assertIsNone(target_key({}))

    def test_all_fault_families_have_exact_scope_injection_and_recovery(self):
        for family in set(FAMILIES) - {"queue_drain"}:
            with self.subTest(family=family), TemporaryDirectory() as tmp:
                config = config_for(family)
                controller = Controller()
                session = ExperimentSession(config, Path(tmp), controller, "kind-ampule")
                session.start()
                session.finish()
                self.assertTrue(session.evidence["restored"])
                self.assertEqual(
                    [a["state"] for a in session.evidence["assertions"]], ["pass", "pass"]
                )
                document = chaos_document(config["experiment"], "payments-test", "test")
                self.assertEqual(document["spec"]["selector"]["pods"], {"payments-test": ["api-0"]})
                self.assertEqual(document["spec"]["duration"], "30s")
                self.assertTrue(any("delete" in c for c in controller.commands))

    def test_fault_opt_in_missing_injection_and_failed_recovery(self):
        for controller in (
            Controller(opt_in=False),
            Controller(inject=False),
            Controller(failure="create"),
        ):
            with TemporaryDirectory() as tmp:
                session = ExperimentSession(config_for(), Path(tmp), controller, "test")
                with self.assertRaises((ValueError, RuntimeError)):
                    session.start()
                session.finish()
                if not controller.opt_in:
                    self.assertFalse(any("create" in c for c in controller.commands))
                else:
                    self.assertTrue(any("annotate" in c for c in controller.commands))
        with TemporaryDirectory() as tmp:
            session = ExperimentSession(
                config_for(), Path(tmp), Controller(failure="annotate"), "test"
            )
            session.start()
            session.finish()
            self.assertFalse(session.evidence["restored"])
            self.assertIn("manual_remediation", session.evidence)

    def test_queue_pass_failure_and_missing_evidence(self):
        for peak, final, expected in (
            (10, 0, "pass"),
            (101, 0, "fail"),
            (10, 3, "fail"),
            (0, 0, "missing"),
            (None, 0, "missing"),
        ):
            with self.subTest(peak=peak, final=final), TemporaryDirectory() as tmp:
                session = ExperimentSession(
                    config_for("queue_drain"), Path(tmp), Controller(), "test"
                )
                session.evidence["samples"] = [
                    {"phase": phase, "depth": depth, "age": 0, "timestamp": time.time()}
                    for phase, depth in (
                        ("baseline", 0),
                        ("load", peak),
                        ("load", peak),
                        ("recovery", final),
                        ("recovery", final),
                    )
                ]
                session.evaluate_queue()
                states = [a["state"] for a in session.evidence["assertions"]]
                self.assertIn(expected, states)
                if expected == "pass":
                    self.assertEqual(set(states), {"pass"})

    def test_queue_collector_rejects_wrong_scope_stale_and_nonfinite(self):
        for labels, timestamp, value, valid in (
            ({"queue": "tasks", "namespace": "payments-test"}, time.time(), "3", True),
            ({"queue": "other", "namespace": "payments-test"}, time.time(), "3", False),
            ({"queue": "tasks", "namespace": "payments-test"}, 0, "3", False),
            ({"queue": "tasks", "namespace": "payments-test"}, time.time(), "NaN", False),
        ):
            with TemporaryDirectory() as tmp:
                session = ExperimentSession(
                    config_for("queue_drain"), Path(tmp), Controller(), "test"
                )
                payload = {
                    "status": "success",
                    "data": {"result": [{"metric": labels, "value": [timestamp, value]}]},
                }
                with patch(
                    "chamber.chaos.experiments.urlopen",
                    side_effect=lambda *a, payload=payload, **k: io.BytesIO(
                        json.dumps(payload).encode()
                    ),
                ):
                    session.sample("load")
                self.assertEqual(session.evidence["samples"][0]["depth"] is not None, valid)

    def test_invalid_experiments_rejected_before_execution(self):
        for changes in (
            {"family": "invalid"},
            {"durationSeconds": 301},
            {"recoverySeconds": False},
            {"pod": "../bad"},
            {"container": ""},
            {"cpuLoad": 101},
            {"dependencyPod": "api-0"},
        ):
            config = config_for()
            config["experiment"].update(changes)
            with self.assertRaises(ValueError):
                validate_experiment(config)
        for changes in ({"depthQuery": ""}, {"queueLabels": {}}, {"maxDepth": float("inf")}):
            config = config_for("queue_drain")
            config["experiment"].update(changes)
            with self.assertRaises(ValueError):
                validate_experiment(config)

    def test_experiment_result_is_evidence_gated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_required_evidence(root)
            config = _config()
            config["experiment"] = {"family": "queue_drain"}
            for state, expected in (
                ("pass", "ready"),
                ("fail", "not_ready"),
                ("missing", "inconclusive"),
            ):
                payload = {
                    "family": "queue_drain",
                    "assertions": [
                        {"name": name, "state": state, "expected": 0, "observed": 0}
                        for name in (
                            "Backpressure exercised",
                            "Queue depth stays within budget",
                            "Oldest task age stays within budget",
                            "Queue drains after admissions stop",
                        )
                    ],
                }
                (root / "evidence/experiment.json").write_text(json.dumps(payload))
                refresh_evidence_manifest(root)
                result = build_assessment_result(
                    root, config=config, metadata=_metadata(root.name), findings=()
                )
                self.assertEqual(result["status"], expected)
            (root / "evidence/experiment.json").write_text("{}")
            result = build_assessment_result(
                root, config=config, metadata=_metadata(root.name), findings=()
            )
            self.assertEqual(result["status"], "inconclusive")

    def test_full_attach_workflow_executes_and_reports_each_fault(self):
        from chamber import workflow
        from tests.evidence_fixtures import evidence_payload
        from tests.test_phase11_12_13_workflow import (
            _attach_kubernetes_config,
            _attach_runner,
            _fixture_repo,
        )

        for family in set(FAMILIES) - {"queue_drain"}:
            with self.subTest(family=family), TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = _attach_kubernetes_config(_fixture_repo(root))
                config["experiment"] = config_for(family)["experiment"]
                path = root / "config.yaml"
                workflow.save_config(config, path)
                base_runner = _attach_runner()
                controller = Controller()

                class Combined:
                    def run(
                        self,
                        command,
                        input_text=None,
                        controller=controller,
                        base_runner=base_runner,
                    ):
                        if (
                            any("chaos-mesh.org/" in arg for arg in command)
                            or "create" in command
                            or (
                                "pod" in command
                                and any(pod in command for pod in ("api-0", "redis-0"))
                            )
                        ):
                            return controller.run(command)
                        return base_runner.run(command)

                def traffic(**kwargs):
                    run_dir = kwargs["run_dir"]
                    (run_dir / "evidence/k6-summary.json").write_text(
                        json.dumps(evidence_payload("k6-summary"))
                    )
                    return {"success": True, "evidence_id": "k6-summary", "exit_status": 0}

                with (
                    patch("chamber.workflow.Path.cwd", return_value=root),
                    patch("chamber.workflow._execute_kubernetes_traffic", side_effect=traffic),
                ):
                    run_dir = workflow._assess_kubernetes_config(
                        path,
                        agents_mode="off",
                        context="dev-cluster",
                        prometheus_url=None,
                        runner=Combined(),
                    )
                result = json.loads((run_dir / "result.json").read_text())
                self.assertEqual(len(result["experiment"]["assertions"]), 4)
                self.assertEqual({a["state"] for a in result["experiment"]["assertions"]}, {"pass"})
                self.assertIn("Experiment Assertions", (run_dir / "report.md").read_text())
                self.assertTrue((run_dir / "evidence/recovery-k6-summary.json").exists())

    def test_form_plan_snapshots_profile_and_cleanup_release_is_explicit(self):
        from chamber.control_plane.jobs import AssessmentJob
        from chamber.runs import write_json_atomic

        with TemporaryDirectory() as tmp, TestClient(create_app(Path(tmp))) as client:
            client.get("/chambers")
            csrf = client.cookies["ampule_csrf"]
            fields = {
                "_csrf": csrf,
                "name": "Payments",
                "context": "kind-ampule",
                "namespace": "payments-test",
                "service": "payments",
                "workload": "payments",
                "allow_faults": "true",
                "chaos_mesh": "true",
            }
            self.assertEqual(client.post("/ui/chambers", data=fields).status_code, 200)
            self.assertEqual(
                client.post("/ui/chambers", data={**fields, "max_vus": "0"}).status_code, 400
            )
            profile = client.get("/api/v1/chambers").json()["chambers"][0]
            response = client.post(
                "/ui/plan",
                data={
                    "_csrf": csrf,
                    "chamber_id": profile["id"],
                    "experiment_json": json.dumps(config_for()["experiment"]),
                    "execution_mode": "kubernetes",
                    "runtime_mode": "attach",
                    "kubernetes_context": "kind-ampule",
                    "namespace": "payments-test",
                    "service_name": "payments",
                    "workload_name": "payments",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            plan_dir = next((Path(tmp) / "runs").iterdir())
            import yaml

            config = yaml.safe_load((plan_dir / "chamber.yaml").read_text())
            self.assertEqual(config["chamber"]["name"], "Payments")
            self.assertEqual(config["experiment"]["family"], "dependency_delay")
            manager = client.app.state.jobs
            with self.assertRaisesRegex(ValueError, "Context override"):
                manager.start(plan_dir / "chamber.yaml", mode="kubernetes", context="other")
            with self.assertRaisesRegex(ValueError, "Kubernetes execution"):
                manager.start(plan_dir / "chamber.yaml", mode="local")
            job = AssessmentJob(
                "a" * 32,
                plan_dir / "chamber.yaml",
                "kubernetes",
                None,
                None,
                "2026-09-14",
                state="failed",
                cleanup_required=True,
                target_key=target_key(config),
                chamber_id=profile["id"],
            )
            manager._jobs[job.job_id] = job
            write_json_atomic(manager.directory / f"{job.job_id}.json", job.summary())
            self.assertEqual(
                client.get("/api/v1/chambers").json()["chambers"][0]["occupancy"], "attention"
            )
            url = f"/ui/jobs/{job.job_id}/cleanup-verified"
            self.assertEqual(client.post(url, data={"_csrf": csrf}).status_code, 400)
            self.assertEqual(
                client.post(url, data={"_csrf": csrf, "confirmed": "verified"}).status_code, 200
            )
            self.assertFalse(manager.get(job.job_id)["cleanup_required"])
            self.assertEqual(manager.get(job.job_id)["state"], "failed")
            job.state = "running"
            manager._persist(job)
            self.assertEqual(
                client.post(url, data={"_csrf": csrf, "confirmed": "verified"}).status_code, 409
            )
            self.assertEqual(client.get("/chambers?clone=bad").status_code, 404)

    def test_chamber_api_ui_and_csrf(self):
        with TemporaryDirectory() as tmp, TestClient(create_app(Path(tmp))) as client:
            page = client.get("/chambers")
            self.assertIn("Create chamber", page.text)
            profile = {
                "name": "Payments",
                "context": "kind",
                "namespace": "test",
                "service": "api",
                "workload": "api",
            }
            self.assertEqual(client.post("/api/v1/chambers", json=profile).status_code, 403)
            response = client.post(
                "/api/v1/chambers",
                json=profile,
                headers={"X-CSRF-Token": client.cookies["ampule_csrf"]},
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(
                client.get("/api/v1/chambers").json()["chambers"][0]["readiness"], "not checked"
            )
            self.assertIn(
                "Clone chamber", client.get("/chambers?clone=" + response.json()["id"]).text
            )
            self.assertIn("Payments", client.get("/new").text)
