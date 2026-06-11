# Project Draft: Zero Trust Testing Chamber

## 1. Project Summary

**Zero Trust Testing Chamber** is an agent-driven reliability testing platform for validating services in a production-like environment before deployment. It goes beyond unit tests, mocked integration tests, and standard staging validation by creating an isolated but realistic runtime chamber where a service is repeatedly deployed, stressed, observed, attacked, degraded, and inspected under high-intensity conditions.

The goal is to discover failures that usually appear only in production, such as Kubernetes OOMKilled events, cascading dependency failures, misconfigured resource limits, retry storms, service mesh/network instability, queue backlog explosions, slow memory leaks, database connection exhaustion, noisy-neighbor effects, and failure amplification across microservices.

The system uses LLM agents not primarily to write application code, but to inspect behavior, design adversarial test scenarios, run experiments, analyze telemetry, and produce actionable reliability reports.

## 2. Core Philosophy

The project is based on a zero-trust view of software readiness:

- Do not trust that unit tests prove runtime safety.
- Do not trust that staging behaves like production.
- Do not trust mocked services to expose real dependency failures.
- Do not trust Kubernetes manifests until tested under realistic pressure.
- Do not trust resource limits until the workload proves them safe.
- Do not trust a service simply because it starts successfully.
- Do not trust isolated service health if dependency failure can drag it down.
- Do not trust human intuition alone when telemetry can provide evidence.

A service is considered safer only after it survives a controlled chamber that simulates production-like deployment, load, failure, observability, and dependency behavior.

## 3. Problem Statement

Many critical bugs do not appear during local testing or conventional CI. They emerge only when the service runs inside a real Kubernetes environment with production-like constraints and interactions.

Examples include:

- The service works in test AKS but gets OOMKilled in production AKS.
- CPU throttling causes latency spikes, which causes upstream retry storms.
- One dependency becomes unavailable and triggers cascading failure.
- One service leaks memory slowly and destabilizes the whole namespace.
- A downstream service dies and causes connection pools, queues, or workers to accumulate.
- Resource requests and limits are too low for actual traffic patterns.
- The service passes health checks but fails real user journeys.
- Logs show symptoms, but the root cause is hidden across metrics, traces, Kubernetes events, and dependent services.

The missing capability is an automated system that can run the service in a realistic chamber, apply production-like stress, observe the full environment, and reason across code, manifests, runtime telemetry, and dependency behavior.

## 4. Project Goal

Build an agent-driven testing chamber that can answer:

> “What can make this service fail in a real production-like environment, and what evidence supports that conclusion?”

The output should not be just pass/fail. The output should be a reliability investigation report containing:

- failure scenarios discovered,
- reproduction steps,
- telemetry evidence,
- suspected root causes,
- affected Kubernetes resources,
- dependency chain analysis,
- resource sizing recommendations,
- configuration risks,
- mitigation options,
- severity and confidence score.

## 5. Target Users

Primary users:

- Platform engineers
- AI/ML engineers deploying services to AKS
- DevOps/SRE teams
- Backend engineers
- QA engineers responsible for integration and reliability testing

Secondary users:

- Engineering managers who need deployment confidence
- Security/reliability reviewers
- Architecture governance teams

## 6. Key Use Cases

### Use Case 1: Pre-production Reliability Validation

Before a service is promoted to production, the chamber deploys it into an isolated namespace with production-like manifests, limits, secrets, dependencies, and observability. The agent runs stress, failure, and recovery scenarios and generates a readiness report.

### Use Case 2: OOMKilled Investigation

The chamber repeatedly runs the service under increasing load, large payloads, long-running requests, and dependency slowdown. It watches memory usage, garbage collection behavior, pod events, restart count, container exit reason, and Kubernetes OOMKilled events.

### Use Case 3: Cascading Failure Simulation

The chamber intentionally degrades or kills connected services. The agent observes whether the target service handles dependency failure gracefully or amplifies the failure through retries, queue buildup, thread exhaustion, connection exhaustion, or memory growth.

### Use Case 4: Resource Limit Recommendation

The chamber tests different CPU and memory limits, then recommends safer requests/limits based on observed peak usage, throttling, latency, queue depth, restart behavior, and safety margin.

### Use Case 5: Production Incident Reproduction

Given production logs, Kubernetes events, traces, or incident notes, the agent attempts to reproduce a similar failure pattern inside the chamber and produces a root-cause hypothesis.

## 7. System Concept

The chamber is an isolated, production-like Kubernetes environment where agents can deploy, test, observe, and reason.

The system should contain:

1. **Target Service**
   - The service under test.
   - Deployed using the same or similar Helm chart, Kustomize, manifests, or deployment pipeline as production.

2. **Dependency Layer**
   - Real or controlled versions of dependencies.
   - Examples: Redis, RabbitMQ, PostgreSQL, Qdrant, internal APIs, object storage, message brokers, authentication service, Apigee-like gateway, mock external services with realistic latency/error behavior.

3. **Load and Traffic Layer**
   - k6, Locust, Vegeta, or custom traffic generators.
   - Supports normal traffic, peak traffic, burst traffic, long-running traffic, malformed payloads, large payloads, and replayed production-like traffic.

4. **Fault Injection Layer**
   - Pod kill
   - Network delay
   - Network packet loss
   - DNS failure
   - Dependency timeout
   - 429/500 injection
   - Slow downstream responses
   - Disk pressure
   - CPU pressure
   - Memory pressure
   - Queue backlog growth

5. **Observability Layer**
   - Metrics
   - Logs
   - Traces
   - Kubernetes events
   - Pod lifecycle events
   - Container restart reasons
   - Resource usage
   - Queue depth
   - Request latency
   - Error rate
   - Dependency health

6. **Agent Runtime**
   - LLM agents with access to repository documentation, manifests, test plans, telemetry, and chamber control tools.
   - Agents do not rely on screenshots or manual human observation.
   - Runtime state must be queryable by tools.

7. **Report Generator**
   - Produces a structured reliability report with evidence and recommendations.

## 8. Agent Roles

### 8.1 Test Planner Agent

Reads the repository, deployment manifests, API contracts, architecture docs, and known service dependencies. It creates an experiment plan.

Responsibilities:

- Identify service entry points.
- Identify dependencies.
- Identify risky code paths.
- Identify Kubernetes resource limits.
- Identify health checks and readiness checks.
- Design test scenarios.
- Define success and failure criteria.

### 8.2 Environment Builder Agent

Prepares the chamber environment.

Responsibilities:

- Create isolated namespace.
- Deploy target service.
- Deploy required dependencies.
- Apply production-like resource limits.
- Configure secrets and config maps.
- Register observability endpoints.
- Confirm service readiness.

### 8.3 Load Agent

Runs load and traffic scenarios.

Responsibilities:

- Generate normal traffic.
- Generate peak traffic.
- Generate burst traffic.
- Generate long-duration soak traffic.
- Generate large payload traffic.
- Generate concurrent request pressure.
- Record traffic profile and results.

### 8.4 Chaos Agent

Injects controlled failures.

Responsibilities:

- Kill dependency pods.
- Introduce latency.
- Trigger downstream 500/429 errors.
- Simulate network loss.
- Simulate queue backlog.
- Simulate CPU/memory pressure.
- Degrade service mesh or DNS behavior where safe.

### 8.5 Observability Analyst Agent

Queries runtime evidence.

Responsibilities:

- Read pod status and Kubernetes events.
- Query Prometheus metrics.
- Query Loki logs.
- Query traces.
- Correlate service behavior with dependency behavior.
- Detect restart loops, OOMKilled events, throttling, latency spikes, error bursts, queue buildup, and memory leaks.

### 8.6 Root Cause Agent

Interprets failures.

Responsibilities:

- Map symptoms to likely causes.
- Connect telemetry to source code and manifests.
- Identify whether the failure is caused by code, resource limits, dependency behavior, retry policy, timeout policy, concurrency, queue handling, or configuration.
- Estimate severity and confidence.
- Propose remediation.

### 8.7 Report Writer Agent

Produces the final chamber report.

Responsibilities:

- Summarize tested scenarios.
- Show pass/fail results.
- Include evidence.
- Explain root-cause hypotheses.
- Recommend fixes.
- Produce deployment readiness score.

## 9. Chamber Workflow

### Step 1: Intake

Input:

- Git repository URL or local repository path
- Kubernetes manifests / Helm chart / Kustomize overlay
- Target service name
- Expected APIs or traffic profiles
- Dependency list
- Production-like resource limits
- Known incident symptoms, if available

Output:

- Chamber test plan

### Step 2: Environment Provisioning

The system creates an isolated namespace or ephemeral cluster environment.

Example environment properties:

- Same Kubernetes version class as production
- Same ingress pattern where possible
- Same resource requests and limits
- Same service account and identity pattern where safe
- Same config map and secret shape
- Same dependency topology
- Same observability instrumentation

### Step 3: Baseline Validation

The chamber first checks whether the service can start and handle basic traffic.

Baseline checks:

- Pod starts successfully
- Readiness probe passes
- Liveness probe is stable
- Service responds to simple requests
- Logs have no fatal startup errors
- Traces are emitted
- Metrics are scraped
- No immediate restart loop
- Memory and CPU idle baseline are recorded

### Step 4: Load Escalation

The chamber increases intensity gradually.

Example stages:

1. 1x normal traffic
2. 5x normal traffic
3. 10x normal traffic
4. 25x normal traffic
5. 50x normal traffic
6. 100x normal traffic
7. burst traffic
8. long-duration soak test

The goal is not only to find the maximum throughput. The goal is to detect when behavior becomes unsafe.

Signals:

- memory growth
- CPU throttling
- latency p95/p99 spikes
- queue backlog growth
- request timeouts
- retry amplification
- pod restarts
- connection pool exhaustion
- dependency saturation

### Step 5: Fault Injection

The chamber introduces controlled failures while traffic is running.

Example scenarios:

- Redis unavailable for 30 seconds
- RabbitMQ queue becomes slow
- Downstream API returns 500
- Downstream API returns 429
- Downstream API latency increases to 2 seconds
- DNS resolution fails temporarily
- Target pod is killed during active requests
- One replica is OOMKilled
- Database connection limit is reached
- Message broker accumulates unacked messages

### Step 6: Recovery Validation

The chamber checks whether the system recovers after the fault is removed.

Recovery checks:

- Service returns to healthy state
- Queue backlog drains
- Memory returns to safe range
- Error rate returns to baseline
- No permanent stuck workers
- No unbounded retry loop
- No restart loop
- No dependency remains overloaded
- No cascading failure continues after the original fault is gone

### Step 7: Analysis and Report

The agent analyzes all evidence and generates the final report.

The report should include:

- Executive summary
- Deployment readiness score
- Failure scenarios found
- Timeline of events
- Root-cause hypotheses
- Evidence from logs, metrics, traces, and Kubernetes events
- Resource sizing recommendation
- Dependency risk analysis
- Configuration risk analysis
- Suggested remediations
- Retest plan

## 10. Example Output Report Structure

# Zero Trust Chamber Report

## Service

- Service name:
- Repository:
- Commit:
- Container image:
- Namespace:
- Test date:
- Test duration:

## Readiness Score

Overall score: 72 / 100

Status: Conditional pass

## Key Findings

1. Service is stable under normal load.
2. Service memory usage grows continuously during 50x and 100x traffic.
3. Target pod was OOMKilled after 18 minutes under 100x load.
4. Downstream API timeout caused retry amplification.
5. Queue backlog did not recover automatically after downstream service returned.

## Critical Failure

### Finding: OOMKilled under sustained high traffic

Severity: High  
Confidence: High

Evidence:

- Pod restart count increased from 0 to 3.
- Kubernetes event showed container terminated with OOMKilled.
- Memory usage increased steadily from 400Mi to 1.9Gi.
- Memory limit was configured at 2Gi.
- p99 latency increased before restart.
- Error rate spiked after restart.

Likely root cause:

The service accumulates in-memory request or task state under high concurrency. The configured memory limit is too low for the actual workload, or the service does not release memory correctly after requests complete.

Recommended actions:

1. Add memory profiling under high concurrency.
2. Review request buffering and batch processing logic.
3. Reduce unbounded in-memory queues.
4. Add backpressure.
5. Increase memory limit only after confirming there is no leak.
6. Add alerting for memory growth rate, not only absolute memory usage.

## Cascading Failure Analysis

Scenario:

Downstream service returned 500 for 60 seconds while target service was receiving 25x traffic.

Observed behavior:

- Target service retried aggressively.
- Request latency increased.
- Worker pool saturated.
- Queue depth increased.
- Error rate remained high even after downstream service recovered.

Likely root cause:

Timeout, retry, and circuit breaker policies are not configured to prevent failure amplification.

Recommended actions:

1. Add bounded retries with exponential backoff.
2. Add circuit breaker for downstream dependency.
3. Add queue length limit.
4. Add bulkhead isolation for dependency calls.
5. Add fallback behavior where business logic allows.

## 11. MVP Scope

The first MVP should focus on one service deployed into AKS or local Kubernetes.

### MVP Capabilities

- Deploy target service into isolated namespace.
- Run baseline health checks.
- Run load test using k6 or Locust.
- Collect Kubernetes events, pod status, logs, and Prometheus metrics.
- Detect OOMKilled, restart loops, high memory, high CPU, throttling, and high latency.
- Inject simple dependency failure.
- Generate markdown reliability report.

### MVP Non-goals

- Fully automated production deployment approval.
- Full multi-service chaos engineering platform.
- Automatic code fixing.
- Perfect production simulation.
- Security penetration testing.
- Compliance certification.

## 12. Suggested Technical Stack

### Kubernetes Environment

- AKS for realistic cloud testing
- Kind or k3d for local development
- Helm or Kustomize for deployment
- Dedicated namespace per chamber run

### Load Testing

- k6 for HTTP/API load
- Locust for Python-based custom workflows
- Custom workers for message queue workloads

### Fault Injection

- LitmusChaos
- Chaos Mesh
- Toxiproxy
- Kubernetes pod deletion
- Network policy manipulation
- Custom dependency simulators

### Observability

- Prometheus for metrics
- Grafana for dashboards
- Loki for logs
- Tempo or Jaeger for traces
- OpenTelemetry Collector
- Kubernetes events collector

### Agent Runtime

- Planner agent
- Load scenario agent
- Chaos scenario agent
- Observability analyst agent
- Root cause agent
- Report writer agent

### Storage

- PostgreSQL or SQLite for chamber run metadata
- Object storage for reports and raw artifacts
- Time-series backend for metrics
- Log backend for runtime logs

## 13. Repository Structure

```text
zero-trust-chamber/
├── AGENTS.md
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CHAMBER_CONCEPT.md
│   ├── RELIABILITY_RULES.md
│   ├── TEST_SCENARIOS.md
│   ├── REPORT_FORMAT.md
│   └── RUNBOOKS.md
├── chamber/
│   ├── orchestrator/
│   ├── environment/
│   ├── load/
│   ├── chaos/
│   ├── observability/
│   ├── analysis/
│   └── report/
├── agents/
│   ├── planner_agent.py
│   ├── environment_agent.py
│   ├── load_agent.py
│   ├── chaos_agent.py
│   ├── observability_agent.py
│   ├── root_cause_agent.py
│   └── report_agent.py
├── scenarios/
│   ├── baseline.yaml
│   ├── oom_stress.yaml
│   ├── dependency_failure.yaml
│   ├── retry_storm.yaml
│   └── soak_test.yaml
├── examples/
│   └── sample-service/
├── reports/
├── scripts/
└── tests/
```

## 14. Scenario Definition Example

```yaml
scenario_id: oom-stress-001
name: OOM stress under high concurrency
target_service: document-handler
duration: 30m

environment:
  namespace: chamber-document-handler
  replicas: 2
  memory_limit: 2Gi
  cpu_limit: "1"

traffic:
  tool: k6
  profile:
    stages:
      - duration: 5m
        target_vus: 50
      - duration: 10m
        target_vus: 200
      - duration: 10m
        target_vus: 500
      - duration: 5m
        target_vus: 0

observability:
  collect:
    - pod_status
    - kubernetes_events
    - container_restarts
    - memory_usage
    - cpu_usage
    - request_latency
    - error_rate
    - logs
    - traces

failure_conditions:
  - pod_oom_killed
  - restart_count_increase
  - p99_latency_above_ms: 5000
  - error_rate_above_percent: 5
  - memory_growth_continuous_for: 10m

success_conditions:
  - no_oom_killed
  - no_restart_loop
  - p99_latency_below_ms: 2000
  - error_rate_below_percent: 1
```

## 15. Reliability Rules

The chamber should flag a service as risky when it observes:

- OOMKilled event
- CrashLoopBackOff
- restart count increase under load
- memory usage continuously increasing
- CPU throttling under normal traffic
- p99 latency exceeding threshold
- error rate exceeding threshold
- queue backlog not draining after recovery
- unacked messages continuously increasing
- downstream timeout causing retry amplification
- readiness probe passing while real requests fail
- liveness probe causing unnecessary restarts
- dependency failure causing target service failure
- target service causing dependency saturation
- recovery time exceeding threshold

## 16. Scoring Model

The system can produce a readiness score from 0 to 100.

Example scoring dimensions:

- Startup stability: 10 points
- Baseline traffic stability: 15 points
- High-load stability: 20 points
- Memory safety: 15 points
- CPU safety: 10 points
- Dependency failure handling: 15 points
- Recovery behavior: 10 points
- Observability completeness: 5 points

Example status:

- 90–100: Production ready
- 75–89: Ready with minor risks
- 60–74: Conditional pass
- 40–59: High risk
- 0–39: Not ready

## 17. Safety Guardrails

The chamber must never run destructive tests against production.

Required guardrails:

- Dedicated test namespace or cluster only.
- Explicit denylist for production contexts.
- Kubernetes context verification before each run.
- Resource quota per chamber run.
- Maximum load budget.
- Maximum test duration.
- Automatic cleanup.
- Kill switch.
- Audit log for every action.
- Human approval for high-impact chaos scenarios.
- No access to production secrets unless explicitly approved and sanitized.

## 18. Roadmap

### Phase 1: Manual Chamber MVP

- Deploy one target service into isolated namespace.
- Run baseline and load tests.
- Collect Kubernetes events, logs, and metrics.
- Detect OOMKilled and restart loops.
- Generate basic markdown report.

### Phase 2: Agent-assisted Analysis

- Add planner agent.
- Add observability analyst agent.
- Add report writer agent.
- Let the agent inspect manifests and telemetry.
- Generate root-cause hypotheses.

### Phase 3: Fault Injection

- Add dependency failure scenarios.
- Add pod kill scenarios.
- Add latency and error injection.
- Add recovery validation.

### Phase 4: Multi-service Chamber

- Model dependency graph.
- Simulate cascading failures.
- Detect failure amplification.
- Test service-to-service resilience.

### Phase 5: CI/CD Integration

- Run chamber tests on release candidates.
- Compare current run with previous baseline.
- Block deployment only on critical reliability regressions.
- Publish report to PR, GitHub Actions, Azure DevOps, or internal dashboard.

### Phase 6: Self-improving Reliability Knowledge Base

- Store known failure patterns.
- Store previous reports.
- Track recurring root causes.
- Generate reliability rules from incidents.
- Recommend new chamber scenarios after each production incident.

## 19. Success Metrics

The project is successful if it can:

- Reproduce production-like failures before production deployment.
- Detect OOMKilled and restart-loop risks early.
- Identify unsafe resource limits.
- Identify dependency cascade risks.
- Produce actionable reports with telemetry evidence.
- Reduce production incidents caused by configuration, resource, and dependency failures.
- Help teams understand why a service failed, not only that it failed.

## 20. One-sentence Vision

Zero Trust Testing Chamber turns production failure modes into repeatable, observable, agent-driven experiments before the service reaches production.
