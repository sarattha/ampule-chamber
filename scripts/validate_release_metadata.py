"""Validate release metadata before CI, tags, and GitHub releases."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    version = _project_version()
    _validate_semver(version)
    if args:
        tag = args[0]
        expected = f"v{version}"
        if tag != expected:
            raise SystemExit(f"release tag {tag!r} does not match project version {expected!r}")
    _require_contains(ROOT / "README.md", f"Version: {version}")
    _require_contains(ROOT / "README.md", "Production-ready release")
    _require_contains(ROOT / "CHANGELOG.md", f"## {version} -")
    _require_contains(ROOT / "mkdocs.yml", "site_name: Ampule Chamber")
    _require_contains(ROOT / "docs/index.md", f"Version: {version}")
    print(f"release metadata ok: {version}")
    return 0


def _project_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = payload["project"]["version"]
    if not isinstance(version, str):
        raise SystemExit("project.version must be a string")
    return version


def _validate_semver(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"project.version must be MAJOR.MINOR.PATCH SemVer, got {version!r}")


def _require_contains(path: Path, needle: str) -> None:
    if needle not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"{path.relative_to(ROOT)} must contain {needle!r}")


if __name__ == "__main__":
    raise SystemExit(main())
