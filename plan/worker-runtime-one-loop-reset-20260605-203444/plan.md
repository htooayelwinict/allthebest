# Worker Runtime One-Loop Reset Plan

## Goal

Refactor the worker runtime into one production-grade control plane:

- one kernel runtime
- one agent loop
- one artifact type
- one append-only artifact log
- one retry/memory policy
- one retry/replan/final decision policy

Do not change decompressor or planner behavior in this pass.

## Acceptance Criteria

- `WorkerKernelRuntime.run(plan, envelope=...)` remains the only public worker runtime entry point.
- `WORKER_RUNTIME_VERSION` no longer selects a competing runtime path after migration.
- Worker templates remain planner-facing groups, but all templates execute through one `AgentRunLoop`.
- Every runtime record is an `ArtifactPayload`:
  - tool observations
  - worker outputs
  - validation failures
  - retry memory
  - final report artifacts
- Kernel retry injects memory back into the same step as:
  - a `kernel_memory_<step_id>` `ArtifactPayload`
  - `task.metadata.kernel_memory` as a convenience mirror
- Replan is only called from kernel decision policy for approved plan-owned failures.
- Runtime/tool/model/budget failures retry the same step with memory, not planner replan.

## Target Architecture

```text
WorkerKernelRuntime
  owns:
    - plan validation
    - budget envelope
    - step retry loop
    - artifact log
    - memory injection
    - replan request
    - final Result

TaskCompiler
  creates:
    - Task
    - input_artifacts view from ArtifactLog
    - write_policy
    - envelope summary metadata
    - kernel_memory artifact on retry

AgentRunLoop
  owns:
    - prompt assembly
    - model decision normalization
    - tool call validation
    - tool execution
    - observations as ArtifactPayload
    - local repair turns
    - final_result candidate
    - artifact contract validation

DecisionPolicy
  decides:
    - continue step
    - retry same step with memory
    - request planner replan
    - blocked
    - failed
    - budget_exceeded
```

## Artifact Model

Use one append-only `ArtifactLog`:

```text
ArtifactPayload(
  id="...",
  kind="tool_observation | worker_output | kernel_memory | issue | derived_artifact",
  trust_level="unknown | worker_reported | verified",
  content={...},
  producer="worker_kernel | worker_type | tool:<name>",
  step_id="...",
  attempt_id="...",
  metadata={
    "lifecycle": "completed | partial | failed | retry_memory",
    "promotable": true | false,
    "issue_type": "...",
    "source_event": "..."
  }
)
```

Completed artifacts, partial artifacts, failed-step artifacts, and replan carryover artifacts become filtered views:

```text
completed = artifacts where metadata.lifecycle == "completed" and metadata.promotable == true
partial = artifacts where metadata.lifecycle == "partial"
failed = artifacts where metadata.lifecycle == "failed"
memory = artifacts where kind == "kernel_memory"
```

## Phases

1. Freeze V2 and choose V1 public path as the consolidation target.
2. Introduce `ArtifactLog` using `ArtifactPayload` only.
3. Move V2 tool-event/evidence learnings into the single artifact log.
4. Move one prompt/model/tool/final loop into `AgentRunLoop`.
5. Replace `AgenticWorkerGroupRunner` validation/fallback with the single loop.
6. Replace V2 `ResultReconciler` and `IssueClassifier` with one `DecisionPolicy`.
7. Inject retry memory from the artifact log into same-step respawns.
8. Remove `WorkerKernelRuntimeV2` selection and retire `app/worker_kernel/v2` after parity tests pass.

## Risks

- Removing V2 too early could lose useful evidence/tool repair behavior.
- Keeping V2 too long keeps multiplying fixes and confusing tests.
- Artifact log must not treat failed tool observations as completed truth.
- Memory injection must be compact, or retries will become token-heavy.

## Verification

Run in this order:

```bash
uv run pytest tests/test_worker_agentic.py tests/test_worker_control.py tests/test_worker_kernel.py -q
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py -q
uv run pytest tests -q
uv run python scripts/live_worker_runtime_probe.py --scenario file_policy_archive_reorg --worker-model xiaomi/mimo-v2.5 --matrix-poll-interval 5
```

## Do Not Do

- Do not keep improving V2 as a separate runtime.
- Do not add a third loop/controller.
- Do not add another artifact/event schema beside `ArtifactPayload`.
- Do not route runtime/tool/model failures to planner replan.
- Do not let worker final_result claim success without artifact/tool evidence.

