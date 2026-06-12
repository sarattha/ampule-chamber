# ExecPlan: Phase 05 Reporting And MVP Hardening

## Description

Complete the MVP by generating evidence-backed reliability reports and
hardening the first end-to-end chamber workflow. This phase should leave the
project ready for a first service validation run.

## Task Checklist

- [x] Define the markdown report schema and required sections.
- [x] Implement report rendering from run metadata, experiment timeline,
      evidence artifacts, and analysis findings.
- [x] Define readiness score calculation and status labels.
- [x] Add a complete sample report fixture.
- [x] Add tests for required report sections and finding formatting.
- [x] Document MVP setup, run command, required tools, and limitations.
- [x] Run a fixture or dry-run end-to-end workflow.
- [x] Review all phase acceptance criteria and record remaining gaps.

## Evaluation Metrics

- Generated reports include service, repository, commit, namespace, test date,
  duration, tested scenarios, readiness score, key findings, evidence,
  recommendations, and retest plan.
- Every finding includes severity, confidence, evidence references, suspected
  root cause, and recommended actions.
- Report rendering is deterministic for the same input artifacts.
- Required-section tests fail when a mandatory section is missing.
- MVP limitations are documented clearly.

## Acceptance Criteria

- A sample chamber run or fixture can generate a complete markdown reliability
  report.
- The report is useful to a platform, SRE, backend, or QA engineer deciding
  whether a service is ready to promote.
- The MVP workflow has documented prerequisites, commands, expected outputs, and
  known non-goals.

## Progress

- [x] Added `chamber/report/` APIs for typed report input, readiness scoring,
      deterministic markdown rendering, and JSON fixture loading.
- [x] Added the `ampule-chamber-report` console script with `--fixture` and
      optional `--output` support.
- [x] Added `artifacts/sample-report-input.json` using phase 03 live k6
      evidence, phase 03 timeline artifacts, and phase 04 fixture evidence.
- [x] Generated `artifacts/sample-reliability-report.md` through the CLI.
- [x] Added `artifacts/mvp-workflow.md` documenting prerequisites, command
      usage, expected outputs, validation commands, and known non-goals.
- [x] Added focused phase 05 tests for scoring, required sections, finding
      formatting, invalid fixtures, CLI output, and sample fixture rendering.

## Surprises And Discoveries

- The repository already had typed inputs for phase 05 from earlier work:
  `EnvironmentMetadata`, `ExperimentTimeline`, `EvidenceArtifact`, and
  `Finding`. Phase 05 only needed a report-specific DTO so fixture rendering
  stays stable and does not require live orchestration.
- `pyproject.toml` did not yet define console scripts. Adding a single
  report-specific command avoided introducing an umbrella CLI before the
  chamber runner exists.
- The sample report intentionally mixes dependency-failure k6 evidence with
  phase 04 fixture evidence. This proves the report shape against prior phase
  artifacts while clearly documenting that it is not a new live Kubernetes run.

## Decision Log

- Chose `ampule-chamber-report` as the phase 05 CLI name so the command is
  user-runnable without implying a full chamber orchestrator.
- Chose JSON as the sample report fixture format because it is deterministic,
  tool-friendly, and easy to render in tests without adding dependencies.
- Chose conservative readiness scoring: start at 100, subtract 40 for
  critical, 25 for high, 10 for medium, and 5 for low findings, floor at 0,
  force `not_ready` for critical findings, use `ready` only for scores at or
  above 90 with no high or critical findings, and use `conditional` for scores
  at or above 70 with no critical findings.
- Chose fixture-backed acceptance for phase 05. Live Kubernetes and Prometheus
  runs remain documented non-goals for this phase because earlier phases already
  define those boundaries and phase 05 is focused on reporting.

## Outcomes And Retrospective

- Generated reports include service, repository, commit, namespace, test date,
  duration, tested scenario, readiness score, key findings, evidence,
  reproduction details, recommendations, retest plan, cleanup notes, and known
  limitations.
- Every finding in the sample report includes severity, confidence, evidence
  references, observed facts, suspected cause, related timeline events, and
  recommended actions.
- The sample report is deterministic for the same JSON fixture and can be
  regenerated through the console script.
- MVP gaps that remain explicit:
  - The report CLI renders supplied fixture data; it does not yet orchestrate
    environment, traffic, chaos, or observability phases.
  - The sample report is fixture-backed and does not prove a new live
    Kubernetes execution.
  - Live Prometheus collection is still required for real service metric
    evidence.
- Acceptance evidence:
  - `uv run python -m unittest tests/test_phase05_reporting.py` passes 7
    focused phase 05 tests.
  - `uv run ampule-chamber-report --fixture docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-report-input.json --output docs/internal/phases/phase-05-reporting-mvp-hardening/artifacts/sample-reliability-report.md`
    regenerates the sample markdown report.
  - `uv run mypy chamber/report tests/test_phase05_reporting.py` passes with no
    issues.
  - `make check` passes format, lint, typecheck, 58 tests, 91% coverage,
    scenario validation, and package build.
