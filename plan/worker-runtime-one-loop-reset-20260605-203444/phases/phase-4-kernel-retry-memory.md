# Phase 4 - Kernel Retry Memory

## Goal

Retry the same step with compact memory injected by the kernel.

## Steps

- Build memory from the artifact log after each failed attempt.
- Inject memory as `ArtifactPayload(kind="kernel_memory")`.
- Mirror compact memory into `task.metadata.kernel_memory`.
- On retry, worker must avoid replaying successful tool operations and must repair failed ones.

## Verification

- Runtime-owned failures retry same step.
- Retry prompt includes previous failure cause and successful writes.
- Planner replan is not called for tool/model/budget failures.

