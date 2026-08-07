# Phase 03: Traffic And Chaos

Phase 03 owns traffic execution and controlled failure injection. Its active
MVP boundary now includes the integrated P0 workflow from a reliability goal,
through bounded execution, to an evidence-backed decision and correlated
investigation surface. Multipart Relayna lifecycle support remains part of this
boundary and must retain its upload-safety and content-safety guarantees.

## Active Feature Boundary

- Propose bounded, editable scenarios from explicit reliability goals without
  introducing a second configuration model or implicitly selecting faults.
- Preserve Advanced journey, multipart, lifecycle, agent, and imported scenario
  fields across Basic/Advanced mode changes.
- Keep operational verdicts evidence-gated and distinguish incomplete,
  cancelled, local-only, and preflight-failed outcomes from success.
- Recover setup by creating a new reviewable plan that preserves the tested
  target, scenario, journeys, safety bounds, and explicit fault selection.
- Correlate only digest-valid traffic, fault, Kubernetes, metric, bounded-log,
  and Relayna lifecycle evidence with honest exact/run-window/inferred labels.
- Keep projections bounded and content-safe while retaining verified raw
  artifact downloads for expert inspection.
- Preserve released `result/v1`, scenario, JSON Relayna, HTTP multipart, and
  multipart Relayna artifact compatibility.
