# Control Plane UI

The control plane is a local, server-rendered interface over the same
application services and safety checks used by the CLI. It does not introduce a
second execution engine.

## Start locally

```bash
uv sync --extra ui
uv run ampule-chamber ui
```

The default address is `http://127.0.0.1:8765`. Use `--no-open` to suppress
browser launch or `--workspace <path>` to select another run store. Binding to a
non-loopback host is rejected unless `--allow-remote` is explicitly supplied.
Ampule Chamber does not add authentication for remote use; operators must place
an authenticated, TLS-enabled boundary in front of any remote binding.

## Core journey

1. **Target:** choose a local repository and optionally override the inferred
   service name.
2. **Environment:** choose a local artifact assessment, isolated Kubernetes
   deploy, or attach to an existing non-production namespace. Live runs require
   an explicit context.
3. **Exercise:** choose a smoke, baseline, or stress traffic template; set the
   journey path; and optionally choose an attach-only, allow-listed fault.
4. **Review:** inspect the safety receipt and generated plan before any live
   action.
5. **Run:** observe state and evidence updates. Cancellation sends an interrupt
   to the workflow so rollback, cleanup, result persistence, and reporting can
   complete.
6. **Results:** review readiness, coverage, findings, timeline, registered
   evidence, resolved configuration, and agent output. Compare compatible runs
   or export HTML, Markdown, or JSON.

## Evidence and readiness

Every new run has a canonical `run.json`, append-only `events.jsonl`,
`result.json`, and `evidence/manifest.json`. Each evidence entry is bound to the
run and SHA-256 digest. Evidence download rejects unknown, cross-run, missing,
or modified entries.

Readiness is scored only after required live Kubernetes, traffic, and
mode-specific evidence is present. Local-only, partial, cancelled, and failed
runs remain explicit and do not receive a misleading readiness score.

## HTTP boundary

The local API is versioned under `/api/v1` and covers capability discovery,
repository inspection, plan creation, asynchronous run control, SSE run events,
result retrieval, evidence access, reports, and comparison. Mutating requests
require a same-site CSRF token. Responses include a strict content security
policy, anti-framing, no-sniff, and no-referrer headers.

## Real kind example

See `examples/sample-service/chamber-kind.yaml` for isolated deploy mode and
`examples/sample-service/chamber-attach.yaml` for attach mode. The sample README
contains the image build and kind load commands.
