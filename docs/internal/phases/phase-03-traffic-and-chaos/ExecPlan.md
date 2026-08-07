# ExecPlan: Relayna Multipart Lifecycle Journeys

## Description

Implement GitHub issue #26 as a Phase 03 traffic extension with Phase 04
evidence and Phase 05 reporting/readiness integration. A multipart Relayna
journey must submit one or more files, follow the configured top-level task ID
through SSE, and produce content-safe lifecycle evidence.

## Task Checklist

- [x] Analyze issue #26 and the existing HTTP multipart, Relayna lifecycle, UI
      upload, evidence, and readiness paths.
- [x] Define and validate explicit multipart field and file contracts.
- [x] Execute multipart Relayna submissions through the existing lifecycle
      runner.
- [x] Support multiple required/optional file rows in the Control Plane.
- [x] Persist safe file metadata and distinguish lifecycle failure stages.
- [x] Add unit, workflow, UI, persistence, security, and end-to-end fixture
      coverage.
- [x] Document single-file OCR and multi-file extraction examples.
- [x] Validate in a real environment and record acceptance evidence.
- [x] Reconcile the feature with the reusable scenario catalog and Prometheus
      evidence changes merged to `main` before final integration.
- [x] Retain bounded, content-safe Relayna event feeds under their admitted
      task IDs, including concurrent VU coverage.
- [x] Discover Relayna Job workers created during attach traffic and collect
      Prometheus run-window CPU, memory, and restart summaries for API and
      worker pods.
- [x] Present per-pod runtime metrics and per-task Relayna feeds in the
      assessment Evidence tab.

## Evaluation Metrics

- One required image can reach a successful SSE terminal state.
- A required image plus an optional second image is serialized correctly.
- String, integer, boolean, object, and array fields have deterministic wire
  representations.
- File paths cannot escape the approved workspace, including through symlinks.
- Per-file and total-request limits are enforced before submission.
- Relayna evidence contains task timing, lifecycle status, failure stage, and
  upload field/name/type/size/digest, never raw bytes.
- Existing JSON Relayna and HTTP multipart checks remain green.

## Decisions

- Extend `chamber/load/relayna.py`; do not create a second lifecycle runner.
- Use descriptor objects with `encoding: json` and `value` for multipart arrays
  and objects. Plain strings, integers, and booleans remain scalar fields.
- Keep the existing `task_id` uniqueness behavior for multipart fields.
- Use 128 MiB per-file and 256 MiB total-request hard limits, with optional
  lower positive limits in each journey.
- Preserve the scenario catalog's signed managed-upload tokens per multipart
  row so reusable scenarios can retain trusted files without weakening the
  browser-upload boundary or collapsing the multi-file model.
- Grant declared target namespaces read-only `get` and `list` access to
  `pods.metrics.k8s.io` so attach runs can collect `kubectl top` evidence
  without broadening fault, Secret, or cluster-wide target permissions.
- Treat the admission response task ID as the authoritative feed key. A task ID
  reported inside an SSE payload is diagnostic only and cannot move an event
  into another concurrent task's feed.
- Retain at most 200 safe operational events per task and exclude arbitrary
  message or OCR content from lifecycle evidence.
- Correlate Relayna worker pods exactly only when Kubernetes metadata contains
  an admitted task ID. Otherwise retain the honest run-window, Job-owner, and
  service-label correlation and show that limitation in the UI.
- Preserve the existing point-in-time Prometheus queries as the readiness gate,
  while adding bounded range queries and derived pod summaries for the UI. This
  keeps completed workers visible without making their expected absence from an
  instant query invalidate target-pod readiness evidence.

## Surprises And Blockers

- The phase tree required by `AGENTS.md` was removed from `main` in commit
  `df05094`. Only the active Phase 03 planning files are restored here; removed
  historical artifacts remain untouched.
- The first real AKS OCR run completed successfully and collected Prometheus
  metrics, but its optional `kubectl top` command exposed a missing
  `pods.metrics.k8s.io` permission for the Ampule service account. The Helm and
  raw-manifest target-reader Roles now include the namespace-scoped read rule,
  and deployment validation rejects a regression.

## Acceptance Evidence

- `make check` passed on 2026-07-13: format, lint, type checking, 217 unit
  and workflow tests, 90% combined branch/line coverage, 10 scenario files,
  deployment and release metadata validation at `1.6.0`, strict MkDocs build,
  and wheel/source-distribution builds.
- A real loopback TCP fixture accepted a generated multipart request, returned
  a parent and child task ID, streamed `processing` then `completed` over SSE,
  and verified that Chamber followed the parent task to success.
- Computer Use exercised the running control plane at `127.0.0.1:8765` against
  `examples/sample-service`: the operator added required `file` and optional
  `roi` rows, selected two PNGs, and saw both filenames, MIME types, and exact
  byte sizes in Review before creating the plan.
- The created plan stored `wizard-desktop.png` (50,626 bytes, SHA-256
  `70c0367340c11ca23ee2e3424a4908e58230cfbe5c35f432af38fe2533d95632`) and
  `environment-desktop.png` (69,707 bytes, SHA-256
  `35ed81320e174043c00b48782d64986b65bd1e46cf439e49529233ccca3931b5`)
  below `/tmp/ampule-issue26-ui`; neither raw payload appeared in the generated
  `chamber.yaml`.
- Compatibility coverage kept the JSON Relayna lifecycle and HTTP multipart
  request paths green while adding success, optional-file, serialization,
  containment, symlink, empty-file, content-type, request-size, timeout, and
  all lifecycle failure-stage cases for multipart Relayna.
- The first PR review identified two cross-path edge cases. HTTP/k6 generation
  now omits pathless optional multipart rows, and timeout-like admission POST
  failures now use the same `timeout` evidence stage as SSE timeouts. Focused
  regression tests and the complete `make check` gate passed after both fixes.
- The final `main` integration retained scenario catalog selection/import,
  signed reusable file paths, Prometheus report sections, and the complete
  required/optional multipart row lifecycle. The combined focused suite passed
  91 control-plane, catalog, Relayna, and workflow tests.
- A real AKS attach run against `ocr-service-api` in `common`, initiated from
  Ampule Chamber in `ampule-system`, completed as run
  `chamber-ocr-service-api-20260713162721087-aa89ea48`. The service accepted the
  multipart request with HTTP 202 in 103 ms, emitted `planning`, `running`, and
  `completed` SSE statuses, and finished one parent task successfully in 99.5
  seconds with no failed tasks.
- The AKS run used a 50,626-byte JPEG with SHA-256
  `70c0367340c11ca23ee2e3424a4908e58230cfbe5c35f432af38fe2533d95632`;
  lifecycle evidence retained only filename, field, content type, size, and
  digest. The generated report scored readiness 100/100 with seven registered
  evidence artifacts and container CPU, memory, and restart series for the
  selected OCR API pod.
- After applying the namespace-scoped metrics RBAC rule, the Ampule service
  account returned `yes` for `get pods.metrics.k8s.io`, and the exact previously
  failing `kubectl top pods ocr-service-api-59f5b54bc6-6zvln --containers`
  command returned CPU and memory values successfully.
- The `1.7.0` implementation test fixture rendered an API pod and a completed
  Relayna worker with 64.0/114.0 MiB memory and 25.0/89.0m peak CPU cards, plus
  two independent `task-a` and `task-b` event feeds whose projected events all
  retained the correct admission task ID even when the fixture payload supplied
  an untrusted different ID.
- `make check` passed on 2026-07-13 with 222 tests and 90% combined branch/line
  coverage, followed by scenario, deployment, `1.7.0` release metadata, strict
  documentation, source-distribution, and wheel validation.
- Chrome rendered the completed Evidence tab at desktop width with the API and
  Relayna worker cards side by side, correct 64.0/114.0 MiB and 25.0/89.0m
  values, the explicit run-level worker-correlation limitation, and separate
  three-event `task-a` and `task-b` feeds. The DOM snapshot and full-page visual
  inspection agreed with the projection assertions.
- The first PR review identified that the bounded 100-series raw Prometheus
  preview was also feeding derived summaries. Range queries now derive compact
  per-pod peaks and sample counts from the complete response before truncating
  raw series for persistence. A 101-pod regression fixture verifies the last
  worker remains summarized, and the complete `make check` gate passed again.

## Issue #32: Decision-Oriented Results And Evidence-Gap Recovery

### Progress

- [x] Add one evidence-gated result projection that explains what happened,
      why the status was selected, and what the operator should do next.
- [x] Project required, present, and missing signals with human-readable names,
      operational impact, likely cause, resolution, and configuration links.
- [x] Keep ready, inconclusive, failed, cancelled, local-only, and
      preflight-failed results distinct without assigning incomplete runs a
      readiness score.
- [x] Link findings to registered supporting evidence and missing signals to
      the relevant resolved-configuration context.
- [x] Add prioritized setup, telemetry, remediation, safety, review, and retest
      actions according to the result state.
- [x] Add review-before-execution UI and API rerun paths that preserve the
      target, scenario revision, traffic journeys, safety bounds, and explicitly
      selected faults while allowing only Kubernetes context and Prometheus URL
      setup overrides.
- [x] Keep the Overview, Markdown/HTML/JSON report exports, and
      `/api/v1/runs/{run_id}` on the same persisted result projection.
- [x] Add focused six-state, evidence-gap, link, surface-consistency, safe-rerun,
      Prometheus-gate, and legacy `result/v1` compatibility coverage.
- [x] Run the complete `make check` acceptance gate and record final evidence.

### Decisions

- Preserve `chamber.ampule.dev/result/v1` and its existing evidence ID, score,
  and coverage fields. The decision-oriented fields are additive so released
  run artifacts remain readable without migration.
- Use `local_only` and `preflight_failed` result statuses instead of collapsing
  them into `inconclusive` or `failed`. Both remain non-conclusive and always
  retain a null readiness score.
- Treat a registered but invalid Prometheus artifact as missing required
  evidence while linking both its retained artifact and its query limitations.
- Never turn a local config into a Kubernetes config through recovery. The
  operator must select a Kubernetes target explicitly in the normal intake
  flow.
- A blank rerun setup field preserves the previous value. In particular, an
  operator cannot remove an existing Prometheus evidence requirement by
  submitting a blank recovery form.
- The fix path creates a new plan for review and never starts execution. Only
  `runtime.kubernetesContext` and `runtime.prometheusUrl` may change; all tested
  traffic, safety, scenario, target, and fault data is deep-copied unchanged.

### Acceptance Evidence

- The focused decision-result suite passed 8 tests covering the six explicit
  result states, structured required/present/missing evidence, Prometheus
  limitations, finding-to-evidence links, UI/API/JSON report consistency,
  Markdown decision sections, safe rerun preservation, local-only refusal, and
  released `result/v1` compatibility.
- The combined Prometheus evidence, decision-result, and existing assessment
  projection suite passed 37 tests.
- Existing Phase 05 reporting, complete Control Plane, Phase 11-13 workflow,
  and scenario catalog suites passed 85 tests after the projection and rerun
  changes.
- `make check` passed on 2026-08-08: 62 files were formatted, lint and type
  checking passed, 230 tests passed, combined branch/line coverage remained at
  90%, 10 scenario files passed validation, deployment and release metadata
  remained at the intentionally unchanged `1.7.0`, strict MkDocs completed,
  and the source distribution and wheel built successfully.
- The first sandboxed `make check` attempt reached the test target but could not
  bind the existing Relayna loopback HTTP fixture. Re-running the identical gate
  outside the socket-restricted sandbox passed; no product failure or test
  assertion was involved.
