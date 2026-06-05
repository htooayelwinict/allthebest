# Existing Code Findings

## Current Split

The worker runtime currently has two overlapping designs:

- V1 path:
  - `app/worker_kernel/runtime.py`
  - `app/worker_kernel/agentic.py`
  - `app/worker_kernel/agent_loop.py`
  - `app/worker_kernel/control.py`
  - `app/worker_kernel/memory.py`

- V2 path:
  - `app/worker_kernel/v2/runtime.py`
  - `app/worker_kernel/v2/agent_controller.py`
  - `app/worker_kernel/v2/evidence_store.py`
  - `app/worker_kernel/v2/artifact_deriver.py`
  - `app/worker_kernel/v2/result_reconciler.py`
  - `app/worker_kernel/v2/issue_classifier.py`
  - `app/worker_kernel/v2/task_frame.py`
  - `app/worker_kernel/v2/tool_router.py`

## Main Flaws

1. There are two agent loops:
   - `AgentRunLoop` in V1.
   - `AgentRunController` in V2.

2. There are two runtime orchestration paths:
   - `WorkerKernelRuntime`.
   - `WorkerKernelRuntimeV2`.

3. There are two memory/evidence concepts:
   - `KernelMemoryController` in V1.
   - `EvidenceStore` in V2.

4. There are too many artifact lifecycle stores:
   - completed artifacts
   - partial artifacts
   - failed-step artifacts
   - evidence records
   - tool events
   - kernel memory artifacts

5. Artifact validation and status decisions are scattered:
   - V1 group runner validates artifacts.
   - V2 reconciler validates artifacts.
   - V1/V2 classifiers both decide retry/replan/final behavior.

## Useful Pieces To Keep

- `TaskCompiler`: already injects envelope summary, literal contracts, write policy, and scoped artifacts.
- `AgentRunLoop`: good home for one model/tool/final loop.
- `WorkerToolbox`: correct permission-gated tool boundary.
- `artifact_contracts.py`: keep as single artifact quality boundary.
- `control.py`: useful retry/replan decision logic, but should become the one `DecisionPolicy`.
- `memory.py`: closest to the desired kernel memory controller.
- Worker templates under `app/worker_kernel/workers/`: keep planner-facing worker types stable.

