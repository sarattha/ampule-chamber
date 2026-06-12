# Phase 08: Expanded Fault Injection And Recovery

Expand the chamber beyond baseline traffic and simple dependency failure into a
broader controlled fault suite with explicit recovery validation. This phase
should build on the phase 06 live runner and the phase 07 analysis layer.

The goal is to exercise realistic failure modes while preserving strict safety
guardrails. Faults should be scoped to the chamber namespace, reversible, and
recorded in the experiment timeline so analysis and reports can connect
symptoms to injection and recovery windows.

## What Needs To Be Done

- Add live pod-kill fault injection for chamber-owned target or dependency pods.
- Add network latency, packet loss, or deny/allow fault paths where supported
  by local Kubernetes tooling.
- Add downstream 500/429 or timeout simulation through controlled dependency
  behavior or a faultable test dependency.
- Add CPU and memory pressure experiments with strict resource and duration
  limits.
- Add recovery validation checks after each fault is removed.
- Extend timelines to capture fault setup, injection, observation, removal,
  recovery, and cleanup events.
- Extend analysis to detect persistent error rate, restart loops, memory growth,
  retry amplification, and failed recovery after fault removal.
- Add tests and fixtures for each supported fault type.
- Record live or dry-run evidence for every new fault capability in
  `artifacts/`.

## Agent Notes

- Do not add high-impact chaos without explicit guardrails, duration limits, and
  cleanup paths.
- Keep every fault reversible and scoped to chamber-owned resources.
- Prefer one well-tested live fault over several partially wired fault types.
- Recovery behavior matters as much as the failure itself. Always record what
  happened after the fault was removed.
