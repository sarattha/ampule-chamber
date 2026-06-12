# Phase 05 MVP Reporting Workflow

## Prerequisites

- Run `uv sync --group dev`.
- Keep the phase 01-04 scenario, traffic, fault, and evidence artifacts present.
- Use fixture-backed reporting for repository checks; live Kubernetes and
  Prometheus are not required for the sample report.

## Generate The Sample Report

```bash
uv run ampule-chamber-report \
  --fixture docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-report-input.json \
  --output docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-reliability-report.md
```

Expected output:

- `sample-reliability-report.md` is rewritten deterministically.
- The report includes service metadata, run metadata, scenario, readiness
  score, findings, evidence references, reproduction details, recommendations,
  retest plan, cleanup notes, and limitations.

## Validate

```bash
uv run python -m unittest tests/test_phase05_reporting.py
make check
```

## Known Non-Goals

- The CLI renders a supplied report fixture; it does not run the chamber.
- The sample report is fixture-backed and does not represent a new live
  Kubernetes execution.
- Live Prometheus remains required for real metric evidence collection.
- Readiness scoring is intentionally conservative until more real service runs
  provide calibration data.
