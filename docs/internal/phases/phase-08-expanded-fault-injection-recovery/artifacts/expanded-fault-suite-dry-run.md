# Phase 08 Expanded Fault Suite Dry Run

Run id used for planner inspection: `phase07-09-dry-run`.

## Implemented Fault Plans

- `pod_kill`: deletes target pods by
  `app.kubernetes.io/name=<service>,chamber.ampule.dev/run-id=<run id>`.
- `dependency_unavailable`: applies and removes a chamber-owned egress-deny
  `NetworkPolicy`.
- `network_loss`: uses the same reversible deny-egress primitive as the local
  Kubernetes-safe MVP network fault.
- `dependency_errors`: patches the controlled dependency Deployment with
  `FAULT_STATUS=500`, then restores `FAULT_STATUS=0`.
- `dependency_rate_limit`: patches the controlled dependency Deployment with
  `FAULT_STATUS=429`, then restores `FAULT_STATUS=0`.
- `dependency_latency`: patches the controlled dependency Deployment with
  `FAULT_DELAY_MS=1500`, then restores `FAULT_DELAY_MS=0`.
- `memory_pressure`: patches target memory requests/limits to a constrained
  profile and restores the scenario resource contract.
- `cpu_pressure`: patches target CPU requests/limits to a valid constrained
  profile and restores the scenario resource contract.

## Recovery Evidence Shape

Timelines now include `environment_setup`, fault start/removal,
`observation_stop`, `recovery_validate`, and `cleanup_planned` events.

Reports include a `Recovery Status` section derived from traffic success,
findings, recovery timeline checkpoints, and cleanup status.

## Limitations

Packet loss and latency at the kernel/network layer are not implemented because
the local kind MVP does not install a privileged chaos controller. The safe MVP
network fault is reversible egress denial scoped to chamber-owned pods.
