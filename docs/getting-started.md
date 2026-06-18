# Getting Started

## Requirements

- Python 3.11 or newer
- `uv`
- `kubectl`, `kind`, Docker, and `k6` for live chamber runs
- Prometheus endpoint access for live metric collection
- `OPENAI_API_KEY` only when `agents.mode: live` is enabled

## Install For Development

```bash
uv sync --group dev
```

## Run The Local Quality Gate

```bash
make check
```

The gate checks formatting, linting, static types, tests, coverage, scenario
validation, release metadata, documentation, and package build output.

## First Assessment

```bash
uv run ampule-chamber assess --repo ../target-service
```

The command creates a `.chamber/runs/<run-id>/` directory and writes a report at
`.chamber/runs/<run-id>/report.md`.
