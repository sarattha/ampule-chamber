# Package And Module Ownership

This phase keeps the implementation stack conservative: Python 3.11+ managed
with `uv`, `uv_build` for packaging, and `PyYAML` for scenario parsing. Tests
use `unittest` so the first validation path does not require a test framework
decision.

## Source Ownership

| Area | Owner Boundary |
| --- | --- |
| `chamber/contracts/` | Shared typed contracts, lifecycle states, schema parsing, and validation used by all later modules. |
| `chamber/orchestrator/` | Run coordination, state persistence, phase execution, and artifact routing. |
| `chamber/environment/` | Docker, namespace, cluster, deployment, dependency, readiness, and cleanup operations. |
| `chamber/load/` | k6, Locust, or custom traffic execution and traffic result capture. |
| `chamber/chaos/` | Controlled fault injection for pod, dependency, network, CPU, and memory scenarios. |
| `chamber/observability/` | Kubernetes events, pod status, logs, metrics, traces, and evidence collectors. |
| `chamber/analysis/` | Signal detection, correlation, root-cause hypotheses, severity, and confidence scoring. |
| `chamber/report/` | Markdown report generation and report artifact packaging. |
| `agents/` | Agent role prompts and coordination helpers. |
| `scenarios/` | User-facing scenario YAML definitions. |
| `examples/` | Sample services, manifests, and service contracts. |
| `tests/` | Unit and contract tests. |

## Public And Internal Interfaces

Future user-facing interfaces:

- Scenario YAML files in `scenarios/`.
- Sample service contract shape in `examples/sample-service/contract.yaml`.
- Validation command documented in `local-development.md`.

Internal interfaces:

- Python modules under `chamber/`.
- Phase planning files under `docs/internal/phases/`.
- Agent prompts and coordination helpers under `agents/`.

The current public contract is intentionally narrow: scenario authors should be
able to write YAML and run validation, while later phase agents can import the
same parser and lifecycle model.
