# Release Process

Ampule Chamber uses SemVer tags and GitHub Actions release automation.

## Prepare A Release

1. Bump `project.version` in `pyproject.toml`.
2. Update `README.md` and `docs/index.md` with the current version.
3. Add a matching `CHANGELOG.md` section.
4. Run:

   ```bash
   make check
   ```

5. Create and push a tag:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

## CI Validation

The release workflow validates:

- SemVer tag matches `pyproject.toml`
- README and docs show the current version
- Changelog contains release notes
- `make check` passes
- Built distributions pass `twine check`
- Release notes can be extracted from `CHANGELOG.md`
