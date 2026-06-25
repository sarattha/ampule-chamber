from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from chamber.contracts.scenario import load_scenario
from chamber.environment import EnvironmentMetadata
from chamber.load import plan_traffic
from chamber.onboarding import (
    ExternalDependencyPolicy,
    FollowUpCheck,
    ImageBuildSpec,
    ImageReplacement,
    OnboardingSpec,
    TrafficJourney,
    WorkloadSpec,
    build_external_translation_onboarding_plan,
    build_onboarding_plan,
    preflight_live_onboarding,
    validate_external_translation_environment,
)
from chamber.report import (
    ReportInput,
    ReproductionDetails,
    RunMetadata,
    ServiceMetadata,
    TestedScenario,
    render_markdown_report,
)

ROOT = Path(__file__).resolve().parents[1]


class Phase10OnboardingTests(unittest.TestCase):
    def test_generic_onboarding_plan_adapts_arbitrary_raw_kubernetes_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _generic_service_repo(Path(tmp))
            spec = OnboardingSpec(
                service_name="payments-api",
                repo_path=repo,
                scenario_id="payments-api-001",
                namespace_base="chamber-payments",
                manifest_paths=("manifests/app.yaml", "manifests/secret.yaml"),
                workload_roles=(
                    WorkloadSpec("payments-api", "target", readiness=("http",)),
                    WorkloadSpec("payments-worker", "worker", readiness=("logs",)),
                    WorkloadSpec("redis", "dependency", readiness=("ping",)),
                ),
                image_builds=(
                    ImageBuildSpec("api", "Dockerfile.api", "ampule/payments-api:local"),
                    ImageBuildSpec("worker", "Dockerfile.worker", "ampule/payments-worker:local"),
                ),
                image_replacements=(
                    ImageReplacement(
                        "registry.example/payments-api:prod", "ampule/payments-api:local"
                    ),
                    ImageReplacement(
                        "registry.example/payments-worker:prod",
                        "ampule/payments-worker:local",
                    ),
                ),
                required_env=("PAYMENTS_API_KEY",),
                secret_env=("PAYMENTS_API_KEY",),
                external_dependencies=(
                    ExternalDependencyPolicy(
                        name="payments-provider",
                        provider="http",
                        endpoint="https://payments.example.invalid",
                        required_env=("PAYMENTS_API_KEY",),
                        timeout_seconds=30,
                        redaction="Only key presence is recorded.",
                    ),
                ),
                traffic=TrafficJourney(
                    tool="k6",
                    method="POST",
                    entrypoint="/payments",
                    expected_status=202,
                    body={"amount": 100},
                ),
                follow_up_checks=(
                    FollowUpCheck(
                        name="payment-status",
                        check_type="http_status",
                        target="/payments/fixture",
                        expected="completed",
                        service_name="payments-api",
                    ),
                ),
            )

            plan = build_onboarding_plan(
                spec,
                run_id="phase10-generic",
                env={"PAYMENTS_API_KEY": "secret-value"},
            )

        rendered = str(plan.manifests) + str(plan.redacted_config)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("plain-secret", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertIn("ampule/payments-api:local", rendered)
        self.assertIn("ampule/payments-worker:local", rendered)
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.traffic_journey["entrypoint"], "/payments")
        self.assertEqual(plan.follow_up_checks[0].name, "payment-status")
        self.assertTrue(
            any(item.service_name == "payments-api" for item in plan.evidence_attribution)
        )
        self.assertTrue(
            any(item.signal_type == "external_dependency" for item in plan.evidence_attribution)
        )
        self.assertNotIn("Helm", " ".join(plan.limitations))
        self.assertNotIn("Kustomize", " ".join(plan.limitations))

    def test_generic_onboarding_records_overlay_limitation_only_when_path_requires_it(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            repo = _generic_service_repo(Path(tmp))
            (repo / "helm").mkdir(parents=True, exist_ok=True)
            (repo / "helm/rendered.yaml").write_text(
                _generic_app_manifest(),
                encoding="utf-8",
            )
            spec = OnboardingSpec(
                service_name="payments-api",
                repo_path=repo,
                scenario_id="payments-api-001",
                namespace_base="chamber-payments",
                manifest_paths=("helm/rendered.yaml",),
                workload_roles=(WorkloadSpec("payments-api", "target", readiness=("http",)),),
                image_builds=(
                    ImageBuildSpec("api", "Dockerfile.api", "ampule/payments-api:local"),
                ),
                image_replacements=(
                    ImageReplacement(
                        "registry.example/payments-api:prod", "ampule/payments-api:local"
                    ),
                ),
                traffic=TrafficJourney(
                    tool="k6",
                    method="POST",
                    entrypoint="/payments",
                    expected_status=202,
                ),
            )

            plan = build_onboarding_plan(
                spec,
                run_id="phase10-generic",
                env={},
            )

        limitations = " ".join(plan.limitations)
        self.assertIn("already-rendered Kubernetes YAML", limitations)
        self.assertIn("Helm/Kustomize rendering was not performed", limitations)

    def test_live_preflight_records_missing_prerequisites_as_blockers(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _generic_service_repo(Path(tmp))
            spec = OnboardingSpec(
                service_name="payments-api",
                repo_path=repo,
                manifest_paths=("manifests/app.yaml",),
                workload_roles=(WorkloadSpec("payments-api", "target"),),
                image_builds=(
                    ImageBuildSpec("api", "Dockerfile.api", "ampule/payments-api:local"),
                ),
                traffic=TrafficJourney("k6", "GET", "/health", 200),
                required_env=("PAYMENTS_API_KEY",),
                secret_env=("PAYMENTS_API_KEY",),
            )
            plan = build_onboarding_plan(spec, run_id="phase10-preflight", env={})

        preflight = preflight_live_onboarding(
            plan,
            env={},
            required_env=("PAYMENTS_API_KEY",),
            prometheus_url=None,
        )

        self.assertFalse(preflight.ready)
        self.assertIn(
            "PAYMENTS_API_KEY is required for live onboarding evidence", preflight.blockers
        )
        self.assertIn("PROMETHEUS_URL is required for live Prometheus evidence", preflight.blockers)

    def test_external_onboarding_plan_adapts_repo_without_secret_values(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _external_translation_repo(Path(tmp))

            plan = build_external_translation_onboarding_plan(
                repo_path=repo,
                run_id="phase10-test",
                env={
                    "LLM_API_KEY": "sk-secret",
                    "LLM_BACKEND": "openai",
                    "LLM_ENDPOINT": "https://api.openai.com/v1",
                    "MODEL_NAME": "gpt-4.1-mini",
                },
            )

        self.assertEqual(plan.service_name, "external-translation-service")
        self.assertEqual(plan.repository_ref, "external-working-tree")
        self.assertFalse(plan.blockers)
        self.assertEqual(
            [item.name for item in plan.image_builds],
            ["translation-api", "translation-worker"],
        )
        self.assertIn("docker/Dockerfile.api", plan.image_builds[0].dockerfile)
        self.assertIn("docker/Dockerfile.worker", plan.image_builds[1].dockerfile)
        self.assertIn("Namespace", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("ConfigMap", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("Deployment", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("Service", [manifest["kind"] for manifest in plan.manifests])
        self.assertIn("ScaledJob", [manifest["kind"] for manifest in plan.manifests])
        rendered = str(plan.manifests) + str(plan.redacted_config)
        self.assertNotIn("sk-secret", rendered)
        self.assertNotIn("placeholder", rendered)
        self.assertIn("<redacted", rendered)
        self.assertIn("ampule/external-translation-worker:local", rendered)
        self.assertTrue(
            any(item.name == "LLM_API_KEY" and item.secret for item in plan.redacted_config)
        )
        self.assertTrue(any(check.name == "redis-master-ping" for check in plan.readiness_checks))
        self.assertTrue(any(check.name == "rabbitmq-endpoints" for check in plan.readiness_checks))
        self.assertEqual(plan.traffic_journey["method"], "POST")
        self.assertEqual(plan.traffic_journey["expectedStatus"], 202)
        self.assertTrue(plan.follow_up_checks)
        self.assertTrue(
            any(item.signal_type == "queue_depth" for item in plan.evidence_attribution)
        )

    def test_missing_openai_key_is_reported_as_live_blocker(self) -> None:
        self.assertEqual(validate_external_translation_environment({}), ("LLM_API_KEY",))

        with TemporaryDirectory() as tmp:
            repo = _external_translation_repo(Path(tmp))
            with patch.dict("os.environ", {"LLM_API_KEY": "sk-shell-key"}, clear=True):
                plan = build_external_translation_onboarding_plan(
                    repo_path=repo,
                    run_id="phase10-missing-key",
                    env={},
                )
                missing = validate_external_translation_environment({})

        self.assertEqual(missing, ("LLM_API_KEY",))
        self.assertEqual(
            plan.blockers,
            ("LLM_API_KEY is required for live onboarding evidence",),
        )
        key_entry = next(item for item in plan.redacted_config if item.name == "LLM_API_KEY")
        self.assertEqual(key_entry.value, "<missing>")

    def test_external_scenario_generates_post_k6_script(self) -> None:
        scenario = load_scenario(ROOT / "scenarios/external-text-translation.yaml")
        environment = EnvironmentMetadata(
            run_id="phase10-traffic",
            scenario_id=scenario.scenario_id,
            provider="kind",
            source_provider="docker",
            namespace="chamber-external-translation",
            labels={"chamber.ampule.dev/run-id": "phase10-traffic"},
            resource_names={"service": "translation-service"},
            readiness_checks=(),
            cleanup_selectors={"chamber.ampule.dev/run-id": "phase10-traffic"},
            service_resources=(),
        )

        with TemporaryDirectory() as artifact_dir:
            plan = plan_traffic(scenario, environment=environment, artifact_dir=artifact_dir)

        self.assertEqual(plan.method, "POST")
        self.assertEqual(plan.expected_status, 202)
        self.assertIsNotNone(plan.request_body)
        assert plan.request_body is not None
        self.assertEqual(plan.request_body["task_id"], "ampule-phase10-text")
        self.assertIn("http.post", plan.script)
        self.assertIn("JSON.stringify(payload)", plan.script)
        self.assertIn("status matches expected", plan.script)
        self.assertIn("/translations", plan.target_url)

    def test_report_renders_onboarding_sections(self) -> None:
        report = ReportInput(
            title="Ampule Chamber Reliability Report",
            service=ServiceMetadata(
                name="external-translation-service",
                owner="platform-reliability",
                repository="/path/to/external-translation-service",
                commit="external-working-tree",
            ),
            run=RunMetadata(
                run_id="phase10-report",
                test_date="2026-06-15",
                duration_seconds=120,
                namespace="chamber-external-translation",
                provider="kind",
                lifecycle_state="completed",
            ),
            scenario=TestedScenario(
                scenario_id="external-text-translation-001",
                name="External text translation through OpenAI",
                path="scenarios/external-text-translation.yaml",
                traffic_tool="k6",
                max_virtual_users=1,
                fault_summary="none",
            ),
            findings=(),
            evidence=(),
            reproduction=ReproductionDetails(commands=(), artifacts=()),
            retest_plan=("Rerun with the same text payload and OpenAI env vars.",),
            cleanup_notes=("Delete chamber-owned external-service namespace after the run.",),
            limitations=("Document-service path is intentionally ignored.",),
            onboarding_summary=("External working tree used read-only.",),
            adapted_workloads=(
                "translation-service API, translation-worker, rabbitmq, redis-master",
            ),
            redacted_config=("LLM_API_KEY=<redacted-present>",),
            external_dependencies=("openai -> https://api.openai.com/v1",),
        )

        markdown = render_markdown_report(report)

        self.assertIn("## Onboarding Summary", markdown)
        self.assertIn("## Adapted Workloads", markdown)
        self.assertIn("## Redacted Configuration", markdown)
        self.assertIn("## External Dependencies", markdown)
        self.assertNotIn("sk-", markdown)


def _external_translation_repo(root: Path) -> Path:
    repo = root / "external_translation_service"
    for path in (
        "docker",
        "deployment",
        "k8s/keda",
    ):
        (repo / path).mkdir(parents=True, exist_ok=True)
    (repo / "docker/Dockerfile.api").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "docker/Dockerfile.worker").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "deployment/deployment.yaml").write_text(_api_manifest(), encoding="utf-8")
    (repo / "k8s/keda/scaledjob.yaml").write_text(_worker_manifest(), encoding="utf-8")
    (repo / "k8s/translation-configmap.yaml").write_text(_configmap(), encoding="utf-8")
    (repo / "deployment/rabbitmq-deployment.yaml").write_text(_rabbitmq(), encoding="utf-8")
    (repo / "deployment/redis-deployment.yaml").write_text(_redis(), encoding="utf-8")
    return repo


def _generic_service_repo(root: Path) -> Path:
    repo = root / "generic_service"
    (repo / "manifests").mkdir(parents=True, exist_ok=True)
    (repo / "Dockerfile.api").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "Dockerfile.worker").write_text("FROM scratch\n", encoding="utf-8")
    (repo / "manifests/app.yaml").write_text(_generic_app_manifest(), encoding="utf-8")
    (repo / "manifests/secret.yaml").write_text(_generic_secret_manifest(), encoding="utf-8")
    return repo


def _generic_app_manifest() -> str:
    return """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments-api
spec:
  selector:
    matchLabels:
      app: payments-api
  template:
    metadata:
      labels:
        app: payments-api
    spec:
      containers:
        - name: api
          image: registry.example/payments-api:prod
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 8080
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: payments-worker
spec:
  selector:
    matchLabels:
      app: payments-worker
  template:
    metadata:
      labels:
        app: payments-worker
    spec:
      containers:
        - name: worker
          image: registry.example/payments-worker:prod
          imagePullPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: payments-api
spec:
  selector:
    app: payments-api
  ports:
    - name: http
      port: 8080
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
spec:
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7
          ports:
            - name: redis
              containerPort: 6379
"""


def _generic_secret_manifest() -> str:
    return """\
apiVersion: v1
kind: Secret
metadata:
  name: payments-secret
type: Opaque
stringData:
  apiKey: plain-secret
"""


def _api_manifest() -> str:
    return """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: translation-service
spec:
  selector:
    matchLabels:
      app: translation-service
  template:
    metadata:
      labels:
        app: translation-service
    spec:
      containers:
        - name: translation-api
          image: registry.example/translation-api:latest
          imagePullPolicy: Always
          ports:
            - name: http
              containerPort: 8887
---
apiVersion: v1
kind: Service
metadata:
  name: translation-service
spec:
  selector:
    app: translation-service
  ports:
    - name: http
      port: 8887
      targetPort: http
"""


def _worker_manifest() -> str:
    return """\
apiVersion: keda.sh/v1alpha1
kind: ScaledJob
metadata:
  name: translation-worker
spec:
  jobTargetRef:
    template:
      metadata:
        labels:
          app: translation-worker
      spec:
        restartPolicy: Never
        containers:
          - name: translation-worker
            image: registry.example/translation-worker:latest
            imagePullPolicy: Always
            ports:
              - name: metrics
                containerPort: 8001
            env:
              - name: LLM_API_KEY
                value: placeholder
"""


def _configmap() -> str:
    return """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: translation-service-config
data:
  REDIS_HOST: redis-master.default.svc.cluster.local
  REDIS_PORT: "6379"
  RABBITMQ_MANAGEMENT_URL: http://rabbitmq.default.svc.cluster.local:15672/
  DOCUMENT_SERVICE_BASE_URL: http://document-service.default.svc.cluster.local
"""


def _rabbitmq() -> str:
    return """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
spec:
  selector:
    matchLabels:
      app: rabbitmq
  template:
    metadata:
      labels:
        app: rabbitmq
    spec:
      containers:
        - name: rabbitmq
          image: rabbitmq:4.2.0-management
          ports:
            - name: amqp
              containerPort: 5672
            - name: management
              containerPort: 15672
---
apiVersion: v1
kind: Service
metadata:
  name: rabbitmq
spec:
  selector:
    app: rabbitmq
  ports:
    - name: amqp
      port: 5672
    - name: management
      port: 15672
"""


def _redis() -> str:
    return """\
apiVersion: v1
kind: Service
metadata:
  name: redis-master
spec:
  selector:
    app: redis
  ports:
    - name: redis
      port: 6379
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis-master
spec:
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:8.0.2-alpine
          ports:
            - name: redis
              containerPort: 6379
"""


if __name__ == "__main__":
    unittest.main()
