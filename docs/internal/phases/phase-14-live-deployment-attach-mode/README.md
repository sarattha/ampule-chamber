# Phase 14: Live Deployment Attach Mode

Add a single-phase operator workflow for assessing an already-live service in a
non-production Kubernetes namespace without redeploying it into a chamber-owned
namespace.

The core question for this phase is:

> Can Ampule Chamber collect bounded reliability evidence from an existing
> non-production deployment while avoiding unintended mutations or cleanup of
> externally owned resources?

## What Needs To Be Done

- Add `runtime.mode: deploy | attach` for Kubernetes assessments while keeping
  deploy mode backward compatible.
- In attach mode, require explicit Kubernetes context and namespace.
- Refuse production-like context and namespace names.
- Verify the target namespace, configured workloads, and configured services
  already exist before traffic.
- Discover workload selectors, services, endpoints, pods, rollout state, and
  ownership metadata from the existing deployment.
- Record a pre-test state snapshot before traffic or mutations.
- Run configured k6 traffic against the existing service through port-forward
  support.
- Collect Kubernetes evidence only for discovered target pods.
- Collect optional Prometheus evidence scoped to the namespace and discovered
  pods.
- Render reports that identify attach mode, external resources, namespace,
  evidence, limitations, cleanup status, and rollback status.
- Keep attach-mode fault injection observe-only by default and require an
  explicit allow-list label or annotation before mutations.
- Ensure attach cleanup never deletes the target namespace or external
  resources.

## Agent Notes

- Treat attach mode as externally owned: do not adapt, apply, or delete target
  manifests.
- Do not infer broad selectors. Use selectors from configured workloads and
  services only.
- Fault support should remain a first safe slice: pod restart and Deployment
  scale are enough if rollback evidence is recorded.
- Any failed rollback must mark the run failed and include manual remediation
  commands in evidence.
