# ExecPlan: Phase 08 Expanded Fault Injection And Recovery

## Description

Broaden Ampule Chamber's live reliability experiments from baseline and simple
dependency unavailability into a controlled suite of reversible faults and
recovery checks. This phase should produce richer evidence for cascading
failure, retry amplification, resource pressure, and incomplete recovery.

## Task Checklist

- [ ] Review phase 06 live runner behavior and phase 07 analysis outputs.
- [ ] Define supported MVP-expanded fault types and safety limits.
- [ ] Implement chamber-scoped pod-kill fault injection.
- [ ] Implement at least one network fault path, such as egress deny, latency,
      or packet loss, with documented local Kubernetes requirements.
- [ ] Implement controlled downstream error or timeout simulation.
- [ ] Implement bounded CPU or memory pressure experiment support, or document
      the blocker if local tooling cannot support it safely.
- [ ] Add recovery validation checks after fault removal.
- [ ] Extend experiment timelines with setup, inject, observe, remove, recover,
      and cleanup event types.
- [ ] Extend findings for retry amplification, persistent error rate,
      unrecovered dependency failure, resource pressure, and failed recovery.
- [ ] Add unit tests and fixtures for fault planning, safety checks, cleanup,
      timeline correlation, and recovery detection.
- [ ] Add live smoke evidence or documented dry-run evidence for each supported
      fault.
- [ ] Update reports to include recovery status and fault-specific retest
      guidance.
- [ ] Record acceptance evidence and remaining unsupported fault modes.

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

- [ ] Phase directory created.

## Surprises And Discoveries

- None yet.

## Decision Log

- Chose expanded fault injection after agent-assisted analysis because the
  project design roadmap places basic agents before broader chaos capabilities.
- Chose recovery validation as a first-class acceptance requirement because the
  design treats recovery after fault removal as a core chamber workflow step.

## Outcomes And Retrospective

- Pending.
