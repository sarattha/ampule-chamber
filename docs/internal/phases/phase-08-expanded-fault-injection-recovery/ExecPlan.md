# ExecPlan: Phase 08 Expanded Fault Injection And Recovery

## Description

Broaden Ampule Chamber's live reliability experiments from baseline and simple
dependency unavailability into a controlled suite of reversible faults and
recovery checks. This phase should produce richer evidence for cascading
failure, retry amplification, resource pressure, and incomplete recovery.

## Task Checklist

- [x] Review phase 06 live runner behavior and phase 07 analysis outputs.
- [x] Define supported MVP-expanded fault types and safety limits.
- [x] Implement chamber-scoped pod-kill fault injection.
- [x] Implement at least one network fault path, such as egress deny, latency,
      or packet loss, with documented local Kubernetes requirements.
- [x] Implement controlled downstream error or timeout simulation.
- [x] Implement bounded CPU or memory pressure experiment support, or document
      the blocker if local tooling cannot support it safely.
- [x] Add recovery validation checks after fault removal.
- [x] Extend experiment timelines with setup, inject, observe, remove, recover,
      and cleanup event types.
- [x] Extend findings for retry amplification, persistent error rate,
      unrecovered dependency failure, resource pressure, and failed recovery.
- [x] Add unit tests and fixtures for fault planning, safety checks, cleanup,
      timeline correlation, and recovery detection.
- [x] Add live smoke evidence or documented dry-run evidence for each supported
      fault.
- [x] Update reports to include recovery status and fault-specific retest
      guidance.
- [x] Record acceptance evidence and remaining unsupported fault modes.

## Evaluation Metrics

- Each supported fault has explicit scope, duration, rollback, and cleanup
  metadata.
- Fault commands refuse to run outside chamber-owned namespaces or labels.
- Recovery checks verify health, readiness, error rate, restart count, and
  selected resource signals after fault removal where evidence is available.
- Timeline correlation links findings to fault injection and recovery windows.
- At least one expanded fault runs live against the sample service or produces
  a Kubernetes-valid dry-run artifact with commands and manifests.
- Reports distinguish transient fault impact from failed recovery.
- `make check` passes after implementation.

## Acceptance Criteria

- A phase 08 scenario can run or dry-run at least one expanded fault beyond the
  phase 03 dependency-unavailable NetworkPolicy path.
- Fault injection, observation, removal, recovery validation, and cleanup are
  represented in the timeline and report.
- The chamber can identify whether the target recovered after fault removal or
  remained degraded.
- Unsupported high-impact faults are documented as non-goals or blockers rather
  than silently omitted.

## Progress

- [x] Phase directory created.
- [x] Added pod-kill, dependency response, network-deny, CPU pressure, and
      memory pressure planning paths with chamber-scoped commands.
- [x] Added reversible dependency error, rate-limit, and latency faults by
      patching controlled dependency environment variables.
- [x] Extended timelines with setup, observation stop, recovery validation, and
      cleanup-planned events.
- [x] Extended report input/rendering with recovery status.
- [x] Added phase 08 tests in
      `tests/test_phase08_09_live_faults_multiservice.py`.
- [x] Added `artifacts/expanded-fault-suite-dry-run.md`.

## Surprises And Discoveries

- CPU pressure patches must keep Kubernetes resource requests less than or
  equal to limits; the implemented constrained profile uses `50m` request and
  `100m` limit.
- Packet loss is not safe to implement directly in the local kind MVP without
  privileged tooling. The MVP network-loss path is documented as reversible
  egress denial.
- A live multi-service run exposed that `k6` can remain active longer than the
  nominal staged duration after dependency faults. The live runner now enforces
  a traffic duration plus 60 second grace deadline so cleanup remains bounded.

## Decision Log

- Chose expanded fault injection after agent-assisted analysis because the
  project design roadmap places basic agents before broader chaos capabilities.
- Chose recovery validation as a first-class acceptance requirement because the
  design treats recovery after fault removal as a core chamber workflow step.
- Chose Deployment env patches for controlled downstream 500, 429, and latency
  behavior because the sample service can now run as a controlled downstream
  fixture.
- Chose to keep dependency response faults blocked only when no controlled
  dependency workload is declared.
- Chose strategic merge patches for Deployment container updates so fault
  patches preserve image, ports, probes, and other container fields.

## Outcomes And Retrospective

- Expanded fault planning and recovery report sections are implemented.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase08_09_live_faults_multiservice.py`
    passes.
  - Dry-run evidence is recorded in
    `artifacts/expanded-fault-suite-dry-run.md`.
  - The phase 09 live run exercised the dependency error fault and cleanup path
    against `kind-ampule-chamber`.
