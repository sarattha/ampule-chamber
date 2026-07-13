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

## Surprises And Blockers

- The phase tree required by `AGENTS.md` was removed from `main` in commit
  `df05094`. Only the active Phase 03 planning files are restored here; removed
  historical artifacts remain untouched.

## Acceptance Evidence

- `make check` passed on 2026-07-13: format, lint, type checking, 181 unit
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
