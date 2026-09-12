# Studio integration foundation — 2026-09-12

Branch: `codex/studio-integration-hardening`. Version: 1.10.0.

This implements the first foundation slice of the Studio integration assessment. Named
chambers, additional experiment families, shared Studio identity, distributed workers,
agent tool execution, and comparison environment fingerprints remain follow-up work.

## Integration contract

1. `POST /api/v1/plans` with a validated configuration returns `run_id` for a reviewed plan.
2. `POST /api/v1/runs` accepts `{"plan_id":"<run_id>","mode":"local"}` or the legacy
   `config_path` field, exclusively. Kubernetes starts retain explicit context and safety gates.
3. Send `Idempotency-Key` on start. Matching configuration bytes and runtime options return
   the same job; reusing a key with different inputs returns 409. Keys persist with jobs.
4. A start reserves `run_id` immediately and returns `job_id`. Poll `/api/v1/jobs/{job_id}`
   or subscribe to its events. Run SSE events include numeric IDs; reconnect using
   `Last-Event-ID` to skip previously delivered lines.
5. Use the configured operator bearer token for server-to-server API calls. Valid bearer
   credentials do not require CSRF cookies; cookie sessions retain CSRF validation.
   This remains a single-operator API, not Studio tenant authorization or browser SSO.

Jobs and snapshots persist under `workspace/jobs`; logs retain their last 64 KiB. Local
POSIX file leases coordinate supervisors on one host. Lost supervisors are marked failed
on startup, with Kubernetes cleanup requiring attention. This is not a distributed queue
or an automatic cleanup controller. Cancellation signals the process group, allows 10
seconds for interrupt handling, then 5 seconds for termination before killing it.

## Evidence and reporting

Readiness requires valid digests and usable core collector content. Unknown required
signals stay inconclusive; queue depth, traces, dependency health, and CPU throttling
are not falsely treated as implemented collectors. Requested pod/event/log signals need
matching successful observations. These are structural evidence gates, not proof of every
causal claim or workload-wide telemetry completeness.

Commit and capture date are stored with run metadata. Missing git records `unknown`.
Report export reads the recorded assessment result rather than silently reanalysing it;
changing evidence requires explicit reanalysis or a new assessment. Artifact downloads
continue validating digests. Legacy runs without captured provenance show `unknown`.

Agent advice remains advisory. Supplied evidence requires at least one valid citation;
this verifies citation presence and IDs, not semantic support for every sentence. Previous
agent stages and provenance are retained under `agent-history`. Offline output is labeled
as deterministic guidance.

## Validation

`make check` passed on 2026-09-12: formatting, lint, typecheck, **266 tests**, **90%
coverage**, 10 scenario validations, deployment/release metadata, strict MkDocs, and
source/wheel builds for 1.10.0. The installed uv version and Starlette/httpx emitted
non-failing compatibility/deprecation warnings.

Docker served both the backend and server-rendered frontend from current repository
source mounted read-only in the existing `ampule-chamber:release-candidate` image,
on loopback port 18765. No host Docker socket, kubeconfig, or live agent keys were
mounted. This validates local execution, not live Kubernetes fault injection.

- Browser-created plan: `chamber-sample-service-20260912115142274-f28784ae`.
- Browser-created execution: `chamber-assessment-20260912115153478-a5c2bc9d` completed,
  local-only, no readiness score, execution coverage 0%, cleanup/rollback not applicable.
- Final-source API execution: `chamber-assessment-20260912115541172-db74d6eb` completed;
  retry returned the same job `5bb0fd25ae9b4f5583431425cf5fee8a`.
- Markdown, JSON, and HTML exports each returned 200 despite git being absent in the image.
- Invalid test configuration produced a failed job and report-unavailable 409, not a
  misleading run-not-found 404. Chrome retained the failure page and inspection link.
- Completed jobs survived Docker restart with the same job/run IDs.
- Chrome verified inferred `/readyz`, collapsed request/config details, local-only review,
  run outcome, agent mode/stage and summaries, and report navigation.
- At 390×844, the report's document width and scroll width were both 390 px.
- Regression tests exercise concurrent job isolation, 200 KB child output with a 64 KiB
  retained tail, cross-manager cancellation escalating to kill, orphan recovery, sticky
  interruption outcomes, unsupported/empty/non-finite evidence, API auth/idempotency,
  event replay, and agent history/citation presence.

Screenshots: [local review](studio-hardening-screenshots/local-review.png),
[agent summaries](studio-hardening-screenshots/agents.png),
[mobile report](studio-hardening-screenshots/mobile-report.png),
[retained failure](studio-hardening-screenshots/failed-job.png).
