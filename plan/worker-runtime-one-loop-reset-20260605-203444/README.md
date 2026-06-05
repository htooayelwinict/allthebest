# Worker Runtime One-Loop Reset

Created: 2026-06-05 20:34:44 Asia/Yangon

## Decision

Stop extending the split V1/V2 worker runtime. Collapse the worker kernel into one runtime with one agent loop, one artifact stream, one retry/memory policy, and one decision classifier.

## Target Shape

```text
Envelope + Plan
    |
    v
WorkerKernelRuntime
    |
    v
TaskCompiler -> Task
    |
    v
KernelStepController
    |
    +--> AgentRunLoop(worker_type templates)
    |       |
    |       +--> model turn
    |       +--> tool turn
    |       +--> artifact validation
    |       +--> local repair turn
    |
    +--> ArtifactLog[ArtifactPayload]
    |
    +--> DecisionPolicy: continue | retry same step | replan | block | fail
    |
    v
Result
```

## Key Rule

No separate artifact systems. Tool events, worker outputs, validation failures, retry memory, and final artifacts should all be represented as `ArtifactPayload` records with `kind`, `trust_level`, and metadata. Completed, partial, failed, and carryover artifacts are views over one append-only artifact log.

## Recommended First Move

Freeze V2 as a learning branch, then migrate the useful V2 pieces into the existing V1 public runtime path:

- Keep public `WorkerKernelRuntime.run(plan, envelope=...)`.
- Keep planner-facing worker names.
- Keep `Task`, `Result`, `ArtifactPayload`, and `ReplanRequest` stable.
- Replace duplicate V1/V2 internals with one agent loop implementation.

