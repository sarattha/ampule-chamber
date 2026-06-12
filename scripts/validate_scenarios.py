#!/usr/bin/env python3
"""Validate Ampule Chamber scenario YAML files."""

from __future__ import annotations

import argparse
from pathlib import Path

from chamber.contracts.scenario import ScenarioValidationError, load_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["scenarios"],
        help="Scenario files or directories to validate. Defaults to scenarios/.",
    )
    args = parser.parse_args()

    scenario_paths = _expand_paths([Path(path) for path in args.paths])
    if not scenario_paths:
        print("No scenario YAML files found.")
        return 1

    failures: list[str] = []
    for path in scenario_paths:
        try:
            scenario = load_scenario(path)
        except ScenarioValidationError as exc:
            failures.append(str(exc))
            continue
        print(f"ok {path}: {scenario.scenario_id} ({scenario.name})")

    if failures:
        print("\nValidation failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"\nValidated {len(scenario_paths)} scenario file(s).")
    return 0


def _expand_paths(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(sorted(path.glob("*.yaml")))
            expanded.extend(sorted(path.glob("*.yml")))
        else:
            expanded.append(path)
    return expanded


if __name__ == "__main__":
    raise SystemExit(main())
