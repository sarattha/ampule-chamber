# Development

## Setup

```bash
uv sync --group dev
```

## Common Commands

```bash
make format
make lint
make typecheck
make test
make coverage
make validate-scenarios
make validate-release
make docs
make build
make check
```

## Documentation

Build docs locally with:

```bash
uv run mkdocs build --strict
```

Serve docs locally with:

```bash
uv run mkdocs serve
```
