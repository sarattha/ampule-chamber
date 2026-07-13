# Phase 03: Traffic And Chaos

Phase 03 owns traffic execution and controlled failure injection. The current
extension tracked by GitHub issue #26 adds multipart file and image submissions
to the existing Relayna task-lifecycle executor while preserving the HTTP/k6
multipart path.

The implementation must keep traffic bounded, validate upload safety before a
plan is created, and emit evidence that is useful to later observability and
reporting phases without exposing uploaded document contents.

## Active Feature Boundary

- Reuse the existing Relayna submit, task-ID extraction, SSE, and terminal-state
  implementation.
- Add explicit multipart scalar and JSON field serialization.
- Support multiple required or optional workspace-contained files with hard
  per-file and total-request limits.
- Persist only safe upload metadata and digests in lifecycle evidence.
- Keep existing JSON Relayna and HTTP multipart journeys compatible.
