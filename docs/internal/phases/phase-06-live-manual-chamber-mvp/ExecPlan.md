# ExecPlan: Phase 06 Live Manual Chamber MVP

## Description

Create the first live, manual end-to-end MVP workflow for Ampule Chamber. This
phase should convert the completed fixture-backed modules into a runnable local
Kubernetes chamber that validates the sample service, captures real evidence,
and emits an evidence-backed markdown report.

## Task Checklist

- [x] Review `README.md`, `docs/internal/PROJECT_DESIGN.md`, and phase 01-05
      outcomes.
- [x] Define the live run contract and command-line interface.
- [x] Add Kubernetes context safety checks and production-context denylist
      behavior.
- [x] Implement local `kind` chamber run orchestration for one scenario.
- [x] Deploy the sample service into an isolated chamber namespace.
- [x] Run readiness and baseline health checks against the Kubernetes service.
- [x] Run k6 traffic through deterministic local port-forwarding.
- [x] Collect Kubernetes pod status, events, and logs from the live namespace.
- [x] Query Prometheus metrics when configured and record actionable diagnostics
      when unavailable.
- [x] Convert collected live evidence into analysis findings.
- [x] Render a markdown report from live run metadata, timeline, evidence, and
      findings.
- [x] Add cleanup behavior with a retain-on-failure or retain-for-debug option.
- [x] Add unit tests for runner planning, safety checks, report wiring, and
      cleanup decisions.
- [x] Add an optional live smoke test document or script for maintainers with
      `kind`, `kubectl`, and `k6` installed.
- [x] Record live acceptance evidence and known limitations in this plan.

## Evaluation Metrics

- A single command can run the sample service through the local chamber path.
- The runner refuses unsafe Kubernetes contexts before applying resources.
- Namespace naming, labels, timeline IDs, and report metadata are traceable to a
  chamber run id.
- The deployed service reaches readiness through Kubernetes before traffic runs.
- k6 results include request count, HTTP failure rate, p95 latency, and exit
  status.
- Kubernetes evidence includes pod status, events, logs, collection timestamps,
  resource identifiers, and run identifiers.
- Missing or unreachable Prometheus fails before live Kubernetes actions.
- The generated report is based on live run metadata and evidence, not only a
  static fixture.
- Cleanup removes chamber-owned resources in the normal success path.
- `make check` passes after implementation.

## Acceptance Criteria

- `uv run ampule-chamber run --scenario scenarios/baseline-health.yaml
  --output docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md`
  or an equivalent documented command performs a local chamber run against the
  sample service.
- The command creates an isolated namespace, deploys the target, waits for
  readiness, runs k6 traffic, collects Kubernetes evidence, analyzes findings,
  renders a markdown report, and cleans up.
- The live report includes service metadata, scenario metadata, run id,
  namespace, timeline, readiness score, findings or no-finding summary,
  evidence references, diagnostics, cleanup status, limitations, and retest
  guidance.
- At least one live artifact captures command output or structured run metadata
  proving that the report was generated from a live Kubernetes run.
- The phase documents any gap that prevents full design-MVP parity instead of
  implying completion.

## Progress

- [x] Phase directory created.
- [x] Reviewed phase 01-05 plans and artifacts before implementation.
- [x] Defined the live runner CLI as `uv run ampule-chamber run`.
- [x] Added required `kind-ampule-chamber` context safety checks.
- [x] Added preloaded sample-service image verification for kind nodes.
- [x] Implemented live kind orchestration for the current MVP scenarios.
- [x] Reused the phase 02 environment plan for Namespace, Deployment, Service,
      readiness, and cleanup metadata.
- [x] Reused the phase 03 k6 traffic plan with exact scenario durations and VUs.
- [x] Added phase 06 Kubernetes-primitive fault mappings for memory pressure
      and dependency-unavailable faults.
- [x] Gated dependency error and rate-limit scenarios from live runs until a
      downstream dependency workload or fault proxy exists.
- [x] Required live Prometheus reachability before live actions.
- [x] Wired Kubernetes, Prometheus, and k6 evidence into phase 04 analysis.
- [x] Rendered live reports through the phase 05 markdown report contracts.
- [x] Added default cleanup plus retain and retain-on-failure options.
- [x] Added focused unit tests for safety checks, image preflight, cleanup
      decisions, report wiring, and current MVP fault mappings.
- [x] Added a live workflow artifact describing prerequisites, command usage,
      outputs, and limitations.

## Surprises And Discoveries

- The initial phase 06 README proposed a narrow baseline path, while the
  implementation request explored all current MVP scenarios with exact
  durations and VUs. Review showed dependency response fault scenarios need
  dependency/proxy provisioning before live execution can be evidence-backed.
- Existing fault planning treated `memory_pressure`, `dependency_errors`, and
  `dependency_rate_limit` as reserved. Phase 06 maps `memory_pressure` to a
  Kubernetes resource patch but keeps dependency response faults gated.
- `memory_pressure` has no explicit timing fields in `oom-stress.yaml`, so the
  live mapping starts at offset 0 and restores after `safety.maxDuration`.
- Dependency 500/429 and rate-limit behavior cannot be represented with
  Kubernetes primitives alone because no downstream dependency workload or
  fault proxy exists yet.
- Exporting the live runner from `chamber.orchestrator.__init__` created a
  circular import through analysis contracts, so the console script points
  directly at `chamber.orchestrator.live`.
- `docker exec ampule-chamber-control-plane crictl images -q
  ampule/sample-service:local` returned unrelated image ids before the sample
  image was actually loaded. The reliable verification command is `docker exec
  ampule-chamber-control-plane crictl images | rg 'ampule/sample-service|IMAGE'`.

## Decision Log

- Chose live local `kind` as the phase boundary because the project design
  allows AKS or local Kubernetes for the first MVP, and local Kubernetes keeps
  acceptance reproducible for agents and maintainers.
- Chose a manual runner before agent autonomy because the design roadmap places
  the manual chamber MVP before agent-assisted analysis.
- Chose the repository sample service as the first live target so phase 06 can
  validate existing contracts without requiring an external application.
- Chose `codex/phase-06-live-manual-chamber-mvp` as the implementation branch.
- Chose `kind-ampule-chamber` as the required safe live context. Other
  contexts are refused by default.
- Chose required externally provided Prometheus through `PROMETHEUS_URL`; the
  runner does not install or configure Prometheus.
- Chose preloaded image verification for `ampule/sample-service:local`; the
  runner does not build or load images into kind.
- Chose exact scenario durations and VUs, including long soak and high-VU
  scenarios, instead of a smoke scaling override.
- Chose Kubernetes-primitives-only live fault support for phase 06.
- Chose to refuse dependency error and rate-limit live scenarios until the
  chamber provisions a downstream dependency workload or fault proxy.
- Chose automatic cleanup by default, with `--retain` and `--retain-on-failure`
  for debugging.

## Outcomes And Retrospective

- Completed live execution with `kind-ampule-chamber`, preloaded
  `ampule/sample-service:local`, `kubectl`, `kind`, `docker`, `k6`, and
  reachable Prometheus at `http://127.0.0.1:9090`.
- Live prerequisite check on this machine:
  - `kubectl config current-context` returns `kind-ampule-chamber`.
  - `kind get clusters` includes `ampule-chamber`.
  - `kubectl`, `kind`, `docker`, and `k6` are installed on `PATH`.
  - `docker build -t ampule/sample-service:local examples/sample-service`
    builds image id
    `sha256:8da98f990bc6511a0b250a3075e063c429363a188f5f3c6390fd13416219cd06`.
  - `kind load docker-image ampule/sample-service:local --name ampule-chamber`
    loads the image into `ampule-chamber-control-plane`.
  - `docker exec ampule-chamber-control-plane crictl images | rg
    'ampule/sample-service|IMAGE'` shows `docker.io/ampule/sample-service`
    with tag `local`.
  - A minimal Prometheus deployment is running in the `monitoring` namespace.
  - `screen -ls` shows detached session `ampule-prometheus` holding the
    Prometheus port-forward.
  - `http://127.0.0.1:9090/api/v1/query?query=up` returns Prometheus
    `status: success`.
  - `uv run python` prerequisite check covering tools, context, Prometheus, and
    the preloaded image prints `phase 06 prerequisites OK`.
  - `env -u PROMETHEUS_URL uv run ampule-chamber run --scenario
    scenarios/baseline-health.yaml --output
    docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md`
    fails cleanly with `error: PROMETHEUS_URL is required for phase 06 live
    runs` before live Kubernetes actions.
- First live baseline attempt:
  - Command:
    `uv run ampule-chamber run --scenario scenarios/baseline-health.yaml
    --output
    docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md
    --prometheus-url http://127.0.0.1:9090`.
  - Result: failed during readiness with `error: failed to verify target
    readiness: error: timed out waiting for the condition`.
  - Kubernetes evidence during failure showed pod
    `sample-service-deployment-39d4937a-65f5675548-6qct4` in
    `ImagePullBackOff`; kubelet tried to pull
    `docker.io/ampule/sample-service:local` and received `pull access denied`.
  - Cleanup completed after the failed run; `kubectl get ns -l
    app.kubernetes.io/part-of=ampule-chamber` returned `No resources found`.
- Successful live baseline run:
  - Command:
    `uv run ampule-chamber run --scenario scenarios/baseline-health.yaml
    --output
    docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md
    --prometheus-url http://127.0.0.1:9090`.
  - Output: `baseline-health-001: report
    docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md`.
  - Run id: `phase06-baseline-health-001-20260613063240`.
  - Namespace: `chamber-local-baseline-health-001-2cfb47c6`.
  - Report:
    `docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/live-baseline-report.md`.
  - Metadata:
    `docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/run-metadata.json`.
  - Evidence:
    `docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/evidence.json`.
  - k6 summary:
    `docs/internal/phases/phase-06-live-manual-chamber-mvp/artifacts/phase06-baseline-health-001-20260613063240/baseline-health-001-k6-summary.json`.
  - k6 metrics: `http_reqs` count `2188`, `http_req_failed` rate
    `0.0031992687385740404`, and `http_req_duration` p95 `4.1332` ms.
  - Runtime evidence includes 8 artifacts from Kubernetes and Prometheus:
    pod status, Kubernetes events, logs, memory usage, CPU usage, CPU
    throttling, request latency, and error rate.
  - Report readiness score is `100/100`, lifecycle state is `completed`, and
    no reliability findings were detected.
  - Run metadata records `traffic_success: true`, `traffic_exit_status: 0`,
    `cleanup_performed: true`, 8 recorded commands, and no phase-specific
    limitations.
  - Cleanup verified after success; `kubectl get ns -l
    app.kubernetes.io/part-of=ampule-chamber` returned `No resources found`.
- Acceptance evidence recorded so far:
  - `uv run python -m unittest tests/test_phase03_traffic_and_chaos.py
    tests/test_phase06_live_runner.py` passes 18 focused tests.
  - `make format` passes.
  - `make lint` passes.
  - `make typecheck` passes.
  - `make check` passes format, lint, typecheck, 67 tests, 90% coverage,
    scenario validation, and package build.
