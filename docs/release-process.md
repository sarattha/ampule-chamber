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
- The locally built release candidate has no fixed HIGH or CRITICAL
  vulnerabilities before any GHCR tag is published

Only after the vulnerability gate passes does the workflow publish the SemVer,
minor, and `latest` image tags. It then generates package and image SBOMs,
attests provenance, and creates the GitHub Release.

If a tagged release fails before publication, fix the cause on a new patch
version; do not move or reuse the failed tag. If an older workflow published an
image before its scan failed, publish the corrected patch promptly and avoid
deploying the affected tag.

## GitHub Pages

The Docs workflow builds every documentation pull request and deploys the
strict MkDocs build after a push to `main`. GitHub Pages requires this one-time
repository configuration:

1. Open **Settings → Pages** in the GitHub repository.
2. Under **Build and deployment**, select **GitHub Actions** as the source.
3. Merge a documentation change or run the Docs workflow after it reaches
   `main`.

The deployment URL is `https://sarattha.github.io/ampule-chamber/`. The workflow
uses the `github-pages` environment and reports the deployed URL in its summary.
