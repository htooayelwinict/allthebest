# Requirements Research

## User-specified architecture

The runtime must use LangGraph as graph orchestration with exactly three top-level runtime nodes:

1. `decompressor_node`
2. `planner_node`
3. `worker_kernel_node`

The graph flow must remain:

```text
decompressor_node → planner_node → worker_kernel_node → END
```

Business logic belongs in runtime classes, not graph node functions:

- `DecompressorRuntime` owns input classification and envelope creation.
- `PlannerRuntime` owns planner strategy selection and plan creation.
- `WorkerKernelRuntime` owns plan validation, budget enforcement, task compilation, dispatch, and result aggregation.

## Core schemas

Only these Phase 1 runtime objects should be introduced:

- `Envelope`
- `Plan`
- `Task`
- `Result`

`PlanStep` is a nested shape needed by `Plan`, not an additional top-level runtime object.

## Explicit exclusions for Phase 1

Do not add:

- `ApprovedPlanContract`
- `VerificationContract`
- `FinalResponseContract`
- separate `BudgetPolicy`/`BudgetLedger`
- complex artifact permission graph
- complex DAG executor
- recursive replanning
- worker-to-worker messaging
- many top-level worker nodes
- many top-level planner nodes

## Required tests

Tests must cover:

1. Direct question: `what is docker`
2. Code fix request: `fix network_sniffer.py`
3. Vague request: `fix the app`
4. Budget rejection before dispatch
5. Compiled LangGraph invocation returns `envelope`, `plan`, and completed `result`
