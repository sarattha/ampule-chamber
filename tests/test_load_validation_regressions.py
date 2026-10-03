"""Load/fault validation and evidence scoping regressions."""

from __future__ import annotations

import copy
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.request import Request

from chamber.analysis import findings
from chamber.chaos import experiments
from chamber.chaos import planning as chaos
from chamber.contracts.scenario import load_scenario
from chamber.environment import plan_environment
from chamber.load import suite
from chamber.observability import collection
from tests.test_load_suite import traffic

ROOT = Path(__file__).resolve().parents[1]


class MetricResponse(io.BytesIO):
    status = 200


class LoadValidationRegressionTests(unittest.TestCase):
    def test_suite_rejects_invalid_identity_and_execution_boundaries(self) -> None:
        cases = [
            lambda d: d.update(journeys=[]),
            lambda d: d.update(journeys=[None]),
            lambda d: d["load"].update(targetMetrics={}),
            lambda d: d["load"].update(
                targetMetrics=[
                    {"name": "memoryBytes", "query": "up", "labels": {"namespace": "private"}}
                ]
            ),
            lambda d: d["load"].update(
                targetMetrics=[{"name": "memoryBytes", "query": "up", "labels": {"pod": "orders"}}]
                * 2
            ),
            lambda d: d["journeys"][0].update(followUps=[{}]),
            lambda d: d["journeys"][0].update(adapter="relayna", steps=[{"path": "/"}]),
            lambda d: d["journeys"][0].update(textBytes=1.5),
            lambda d: d["journeys"][0].update(thresholds={"unknown": 1}),
        ]
        for mutate in cases:
            fixture = traffic()
            mutate(fixture)
            with self.subTest(fixture=fixture), self.assertRaises(ValueError):
                suite.validate_suite(fixture)

    def test_generator_balances_active_requests_when_opener_raises(self) -> None:
        generator = suite.Generator()
        with patch.object(
            suite, "build_opener", return_value=Mock(open=Mock(side_effect=OSError("offline")))
        ):
            with self.assertRaisesRegex(OSError, "offline"):
                with generator.opener(Request("http://target.test/"), deadline=1):
                    self.fail("must not open")
        self.assertEqual(generator.active, 0)
        self.assertEqual(generator.peak_active, 1)

    def test_iteration_deadline_business_assertions_and_body_limit_fail_closed(self) -> None:
        generator = suite.Generator()
        journey = {"name": "health", "path": "/", "assertJson": {"ok": True}}
        response = MetricResponse(b'{"ok":false}')
        with patch.object(generator, "opener", return_value=response):
            result = suite.execute_iteration(
                journey,
                base_url="http://target.test",
                variables={},
                iteration=1,
                timeout=1,
                workspace=None,
                generator=generator,
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "ValueError")
        response = MetricResponse(b"x" * (1024 * 1024 + 1))
        with patch.object(generator, "opener", return_value=response):
            result = suite.execute_iteration(
                {"name": "large", "path": "/"},
                base_url="http://target.test",
                variables={},
                iteration=1,
                timeout=1,
                workspace=None,
                generator=generator,
            )
        self.assertFalse(result["success"])
        with patch.object(suite.time, "monotonic", side_effect=[0, 2, 2, 2, 2]):
            result = suite.execute_iteration(
                {"name": "late", "path": "/"},
                base_url="http://target.test",
                variables={},
                iteration=1,
                timeout=1,
                workspace=None,
                generator=generator,
            )
        self.assertTrue(result["deadline_missed"])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / "invoice.txt"
            upload.write_text("safe")
            journey = {
                "name": "upload",
                "path": "/",
                "method": "POST",
                "requestEncoding": "multipart",
                "multipart": {
                    "fields": {},
                    "files": [
                        {"field": "file", "path": str(upload), "contentType": "application/pdf"}
                    ],
                },
            }
            response = MetricResponse(b"{}")
            with patch.object(generator, "opener", return_value=response) as opener:
                result = suite.execute_iteration(
                    journey,
                    base_url="http://target.test",
                    variables={},
                    iteration=1,
                    timeout=1,
                    workspace=root,
                    generator=generator,
                )
            self.assertTrue(result["success"])
            request = opener.call_args.args[0]
            self.assertIn("multipart/form-data", request.get_header("Content-type"))
            self.assertIn(b"safe", request.data)

    def test_unexercised_journey_is_missing_evidence(self) -> None:
        stage = {
            "rows": [],
            "counts": {"never": {"requested": 0, "dropped": 0}},
            "phase": "baseline",
            "durationSeconds": 1,
            "actualSeconds": 1,
        }
        result = suite._window_result(stage, [{"name": "never"}], {})
        self.assertEqual(result["state"], "inconclusive")
        self.assertTrue(
            any(
                a["name"] == "Workload exercised" and a["state"] == "missing"
                for a in result["journeys"][0]["assertions"]
            )
        )

    def test_fault_contracts_reject_malformed_faults_and_durations(self) -> None:
        scenario = load_scenario(ROOT / "scenarios/baseline-health.yaml")
        for value, reason in (
            ({}, "expected list"),
            ([None], "expected mapping"),
            ([{}], "fault type"),
            ([{"type": "pod_kill"}], "description"),
        ):
            candidate = copy.deepcopy(scenario)
            candidate.document["faults"] = value
            with self.assertRaisesRegex(chaos.FaultPlanningError, reason):
                chaos._faults(candidate)
        self.assertEqual(chaos._target_name({"target": None}, default="default"), "default")
        scenario.document["safety"] = None
        self.assertEqual(chaos._scenario_max_duration(scenario), 0)
        scenario.document["safety"] = {"maxDuration": "bad"}
        with self.assertRaisesRegex(chaos.FaultPlanningError, "maxDuration"):
            chaos._scenario_max_duration(scenario)
        with self.assertRaises(chaos.FaultPlanningError):
            chaos._duration_field({"duration": "bad"}, "duration", index=0)
        environment = plan_environment(
            load_scenario(ROOT / "scenarios/baseline-health.yaml"), run_id="fault"
        ).metadata
        resource = environment.service_resources[0]
        patch_value = json.loads(
            chaos._dependency_env_patch(resource, fault_type="dependency_latency")
        )
        self.assertIn("1500", json.dumps(patch_value))
        with self.assertRaisesRegex(chaos.FaultPlanningError, "unsupported"):
            chaos._dependency_env_patch(resource, fault_type="unknown")
        for runtime in (
            {"provider": "local"},
            {"provider": "kubernetes", "mode": "attach", "faults": [{}]},
        ):
            config = {"experiment": {"family": "memory_pressure"}, "runtime": runtime}
            with self.assertRaises(ValueError):
                experiments.validate_experiment(config)
        with self.assertRaisesRegex(ValueError, "Prometheus"):
            experiments.validate_experiment(
                {
                    "experiment": {"family": "queue_drain"},
                    "runtime": {"provider": "kubernetes", "mode": "attach"},
                }
            )

    def test_observability_never_assigns_malformed_events_to_another_pod(self) -> None:
        self.assertEqual(collection._events_for_pods({"items": [{}]}, pod_names=())["items"], [])
        self.assertEqual(collection._events_for_pods({}, pod_names=("orders",))["items"], [])
        self.assertIsNone(collection._event_object_name({}))
        self.assertEqual(
            collection._pod_names({"items": [None, {"metadata": None}, {"metadata": {}}]}), ()
        )
        self.assertEqual(collection._pod_names({}), ())
        with self.assertRaises(collection.ObservabilityCollectionError):
            collection._require_http_url("file:///metrics")
        self.assertIsNone(findings._max_prometheus_value({"result": {}}))
        self.assertIsNone(findings._max_prometheus_value({"result": {"data": {}}}))
        self.assertEqual(findings._number(None), 0)
