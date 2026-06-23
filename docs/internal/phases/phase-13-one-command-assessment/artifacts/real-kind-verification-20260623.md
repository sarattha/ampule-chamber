# Real Kind Verification - 2026-06-23

Branch: `codex/generic-kubernetes-assessment`

PR: `https://github.com/sarattha/ampule-chamber/pull/15`

## Environment Availability

- `kind`: `/opt/homebrew/bin/kind`, `kind v0.32.0 go1.26.3 darwin/arm64`
- `kubectl`: `/opt/homebrew/bin/kubectl`, client `v1.36.0`
- `docker`: `/usr/local/bin/docker`, client/server `29.5.3`
- `k6`: `/opt/homebrew/bin/k6`, `k6 v2.0.0`
- Existing kind cluster: `ampule-chamber`
- Current context before live run: `kind-ampule-chamber`
- Kind server version recorded by preflight: `v1.36.1`, platform `linux/arm64`

## Automated Suite

Command:

```bash
make check
```

Result before fixes: passed.

Key output:

```text
41 files already formatted
All checks passed!
All checks passed!
Ran 119 tests in 0.921s
OK
Ran 119 tests in 1.254s
OK
TOTAL ... 91%
Validated 7 scenario file(s).
release metadata ok: 1.1.0
Documentation built in 0.46 seconds
Successfully built dist/ampule_chamber-1.1.0.tar.gz
Successfully built dist/ampule_chamber-1.1.0-py3-none-any.whl
```

## Blockers Found And Fixed

Generated onboarding config for the Docker-first sample service included
`contract.yaml` and `docker-compose.yml` as Kubernetes manifests. Planning then
failed before live execution:

```bash
uv run ampule-chamber plan \
  --config docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-chamber.yaml \
  --run-dir .chamber/runs/kind-generic-sample-plan
```

Output:

```text
KeyError: 'kind'
```

Fix: onboarding inference now filters manifest candidates to Kubernetes API
groups before adding them to `deployment.manifests`.

The first successful live run also showed `runtime.namespaceBase` was recorded
but not passed to the onboarding planner. Fix: `config_to_onboarding_spec`
now honors `runtime.namespaceBase`.

Focused verification after fixes:

```bash
uv run python -m unittest tests/test_phase11_12_13_workflow.py
uv run ruff format --check chamber/workflow.py tests/test_phase11_12_13_workflow.py
uv run ruff check chamber/workflow.py tests/test_phase11_12_13_workflow.py
uv run ty check chamber/workflow.py tests/test_phase11_12_13_workflow.py
```

Output:

```text
Ran 30 tests in 0.413s
OK
2 files already formatted
All checks passed!
All checks passed!
```

## Live Kind Assessment

Image preparation:

```bash
docker build -t ampule/sample-service:local examples/sample-service
kind load docker-image ampule/sample-service:local --name ampule-chamber
```

Key output:

```text
naming to docker.io/ampule/sample-service:local done
Image: "ampule/sample-service:local" with ID "sha256:85fffc69fc08996f64fc6d2266459ce3647c7884c9027b348070921130560cc4" not yet present on node "ampule-chamber-control-plane", loading...
```

Reviewed config and manifest artifacts:

- `docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-kubernetes-config.yaml`
- `docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-manifests.yaml`

Commands:

```bash
uv run ampule-chamber plan \
  --config docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-kubernetes-config.yaml \
  --run-dir .chamber/runs/kind-generic-sample-plan

uv run ampule-chamber assess \
  --config docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-kubernetes-config.yaml \
  --mode kubernetes \
  --context kind-ampule-chamber
```

Output:

```text
planned .chamber/runs/kind-generic-sample-plan
report .chamber/runs/chamber-sample-service-20260623162533/report.md
```

Run evidence directory:

```text
.chamber/runs/chamber-sample-service-20260623162533/
```

Key recorded results:

```text
preflight.ready: true
preflight.blockers: []
namespace: chamber-kind-sample-3e07568c
traffic_result.success: true
k6 checks_succeeded: 100.00% 1 out of 1
http_req_failed: 0.00% 0 out of 1
cleanup_performed: true
```

Recorded Kubernetes command output includes:

```text
namespace/chamber-kind-sample-3e07568c created
deployment.apps/sample-service created
service/sample-service created
deployment "sample-service" successfully rolled out
pod "sample-service-7dc455fdcd-25k9d" deleted from chamber-kind-sample-3e07568c namespace
service "sample-service" deleted from chamber-kind-sample-3e07568c namespace
deployment.apps "sample-service" deleted from chamber-kind-sample-3e07568c namespace
namespace "chamber-kind-sample-3e07568c" deleted
```

Cleanup verification:

```bash
kubectl --context kind-ampule-chamber get ns chamber-kind-sample-3e07568c
kubectl --context kind-ampule-chamber get ns | rg 'chamber-kind-sample|chamber-sample-service' || true
```

Output:

```text
Error from server (NotFound): namespaces "chamber-kind-sample-3e07568c" not found
```

No `chamber-kind-sample` or `chamber-sample-service` namespace remained.

## Security-Hardened Manifest Rerun

After GitHub Actions Semgrep flagged the sample verification manifest for
missing Kubernetes security context, the manifest was hardened with
`runAsNonRoot`, numeric user/group IDs, `allowPrivilegeEscalation: false`, and
capability drops.

The live kind assessment was rerun:

```bash
uv run ampule-chamber assess \
  --config docs/internal/phases/phase-13-one-command-assessment/artifacts/kind-sample-kubernetes-config.yaml \
  --mode kubernetes \
  --context kind-ampule-chamber
```

Output:

```text
report .chamber/runs/chamber-sample-service-20260623163054/report.md
```

Recorded metadata:

```text
stage: assessed
mode: kubernetes
namespace: chamber-kind-sample-2f6d3359
cleanup_performed: true
success: true
```

Cleanup verification:

```bash
kubectl --context kind-ampule-chamber get ns chamber-kind-sample-2f6d3359
```

Output:

```text
Error from server (NotFound): namespaces "chamber-kind-sample-2f6d3359" not found
```

Final local gate after the hardened manifest:

```bash
make check
```

Result: passed with 120 tests, 91% coverage, scenario validation, release
metadata validation, MkDocs strict build, and package build.
