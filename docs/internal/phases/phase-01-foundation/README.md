# Phase 01: Foundation

Define the project contracts that every later phase depends on. This phase
should turn the design draft into concrete MVP shapes: scenario files, chamber
run metadata, component boundaries, local development expectations, and the
first test fixtures.

The output of this phase is not a full chamber. The output is a stable
foundation that lets agents and engineers implement orchestration, load, chaos,
observability, analysis, and reporting without redefining the core vocabulary.

## What Needs To Be Done

- Define the chamber run lifecycle and state model.
- Define the MVP scenario schema for baseline, load, fault, observability,
  failure conditions, and success conditions.
- Decide initial language, package layout, dependency management, and test
  command conventions.
- Add sample scenario fixtures and a minimal sample service contract.
- Document which interfaces are internal and which are intended for future
  users.
- Create focused tests or schema validation checks for scenario parsing.

## Agent Notes

- Keep this phase conservative. Later phases should be able to extend the
  contracts without breaking early examples.
- Prefer explicit structured fields over free-form text for scenario and run
  metadata.
- Store exploratory sketches, schema examples, and notes in `artifacts/`.

## Phase Outputs

- Run lifecycle states and allowed transitions:
  `artifacts/run-lifecycle.md` and `chamber/contracts/lifecycle.py`.
- MVP scenario schema and validation rules:
  `artifacts/scenario-schema.md` and `chamber/contracts/scenario.py`.
- Package and module ownership:
  `artifacts/package-ownership.md`.
- Local setup, validation, test, and Docker sample-service commands:
  `artifacts/local-development.md`.
- Root quality commands for formatting, linting, type checking, tests,
  coverage, scenario validation, and package build: `Makefile`.
- Example scenarios:
  `scenarios/baseline-health.yaml`, `scenarios/oom-stress.yaml`,
  `scenarios/dependency-failure.yaml`, `scenarios/retry-storm.yaml`, and
  `scenarios/soak-test.yaml`.
- Docker-first sample service contract:
  `examples/sample-service/`.
