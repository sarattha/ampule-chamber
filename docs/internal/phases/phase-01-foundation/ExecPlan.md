# ExecPlan: Phase 01 Foundation

## Description

Build the MVP foundation for Ampule Chamber by defining the run lifecycle,
scenario schema, repository conventions, and first validation path. This phase
converts the product design into concrete interfaces that later phases can
implement.

## Task Checklist

- [x] Review `README.md` and `docs/internal/PROJECT_DESIGN.md`.
- [x] Define the chamber run lifecycle states.
- [x] Define MVP scenario schema fields and validation rules.
- [x] Add example scenarios for baseline health, OOM stress, dependency
      failure, retry storm, and soak testing.
- [x] Choose initial implementation stack and dependency conventions.
- [x] Establish package/module ownership for orchestrator, environment, load,
      chaos, observability, analysis, and reporting.
- [x] Add schema parsing or validation tests.
- [x] Document local development commands.
- [x] Record unresolved design decisions in this plan.

## Evaluation Metrics

- Scenario schema covers at least baseline, traffic, observability, failure
  conditions, and success conditions.
- At least three scenario examples validate successfully.
- Run lifecycle states are documented with allowed transitions.
- Package ownership is documented clearly enough for another agent to add code
  without asking for architecture clarification.
- Validation can be run from a documented command.

## Acceptance Criteria

- A new agent can read this phase folder and implement a scenario parser without
  needing hidden context.
- Example scenarios are present and structurally valid.
- The chosen development/test commands are documented.
- Open questions are recorded in the decision log instead of left implicit.

## Progress

- [x] Reviewed phase README, repository README, and project design.
- [x] Created the `chamber/contracts/` package with run lifecycle and scenario
      validation contracts.
- [x] Added five example scenario fixtures in `scenarios/`.
- [x] Added a Docker-first sample service contract in `examples/sample-service/`.
- [x] Added scenario validation command and contract tests.
- [x] Added root Makefile targets for format, lint, typecheck, test, coverage,
      scenario validation, and package build.
- [x] Added phase artifacts for lifecycle, schema, package ownership, and local
      development commands.

## Surprises And Discoveries

- The repository started with planning directories and `.gitkeep` files only,
  so phase 01 needed to introduce the first executable contract layer.
- `PyYAML` was not installed in the base interpreter, so dependency setup is
  documented through `uv sync`.
- Docker-first sample service execution is enough for the phase 01 target
  contract; Kubernetes provisioning remains phase 02 work.
- Docker Hub resolution for `python:3.14-slim` timed out during verification,
  while `python:3.13-slim` was already available locally. The sample service
  Dockerfile now uses `python:3.13-slim` to keep the Docker-first path runnable.

## Decision Log

- Chose Python 3.11+ with `uv` as the initial implementation stack because the
  project design describes Python-style agents and the first MVP needs
  lightweight schema validation before orchestration.
- Chose `uv_build` as the package build backend so build tooling is aligned
  with `uv` from the start.
- Chose `PyYAML` as the only runtime dependency for scenario parsing.
- Chose `unittest` for the initial test command to keep dev dependencies empty.
- Chose a Relayna-style root `Makefile` as the local quality entry point, with
  Python targets backed by `uv run`.
- Introduced `chamber/contracts/` as the shared contract package used by later
  orchestrator, environment, load, chaos, observability, analysis, and report
  modules.
- Treat scenario YAML files as the first future user-facing interface; Python
  modules under `chamber/` remain internal for now.
- Use Docker Compose for the first sample service contract and defer Kubernetes
  namespace/cluster provisioning to phase 02.
- Open question: exact report scoring weights are still directional and should
  be finalized when phase 04 analysis and phase 05 reporting have evidence.
- Open question: whether scenarios should later support JSON Schema export or
  Pydantic models should be decided when external users begin authoring
  scenarios.
- Open question: the production-context denylist shape should be finalized in
  phase 02 when Kubernetes context detection is implemented.

## Outcomes And Retrospective

- Scenario schema covers baseline checks, traffic stages, observability
  signals, failure conditions, success conditions, target runtime, environment,
  dependencies, faults, and safety guardrails.
- Five scenario examples are present: baseline health, OOM stress, dependency
  failure, retry storm, and soak testing.
- Run lifecycle states and allowed transitions are documented in
  `artifacts/run-lifecycle.md` and implemented in
  `chamber/contracts/lifecycle.py`.
- Package ownership is documented in `artifacts/package-ownership.md`.
- Local validation is documented in `artifacts/local-development.md`.
- Acceptance evidence:
  - `uv sync` builds and installs the local package through `uv_build`.
  - `uv run python scripts/validate_scenarios.py` validates all five scenario
    files.
  - `uv run python -m unittest discover -s tests` passes the scenario contract
    and lifecycle tests.
  - `uv build` produces the source distribution and wheel through the
    `uv_build` backend.
  - `make check` runs format, lint, typecheck, tests, coverage, scenario
    validation, and package build successfully.
  - `docker compose -f examples/sample-service/docker-compose.yml up --build -d`
    starts the Docker-first sample service.
  - `curl http://localhost:8080/healthz`, `curl http://localhost:8080/readyz`,
    and `curl http://localhost:8080/dependency` return successful JSON
    responses.
  - `docker compose -f examples/sample-service/docker-compose.yml down` removes
    the sample service containers and network.
