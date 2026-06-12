# Phase 02 Kubernetes Permissions

Phase 02 acceptance is a deterministic dry-run plan, so the implemented path
does not require live Kubernetes credentials.

When live `kind` execution is added, the environment provider should require
only namespace-scoped permissions after namespace creation:

- create, get, list, watch, patch, and delete `namespaces` for chamber-owned
  namespaces.
- create, get, list, watch, patch, and delete `deployments.apps`.
- create, get, list, watch, patch, and delete `services`.
- get, list, and watch `pods`, `events`, and `endpoints`.
- get pod logs for startup evidence collection.

Cleanup should target resources by both:

- `app.kubernetes.io/managed-by=ampule-chamber`
- `chamber.ampule.dev/run-id=<run id>`

The MVP dry-run plan records these selectors in `EnvironmentMetadata` and
`CleanupPlan` so future live execution can delete only chamber-owned resources.
