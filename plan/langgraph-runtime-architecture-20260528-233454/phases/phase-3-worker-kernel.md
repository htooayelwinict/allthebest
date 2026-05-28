# Phase 3: Worker-Kernel Runtime

## Status

- Completed: 2026-05-29
- Notes:
  - Implemented `BudgetGate` with pre-plan, pre-task, and post-result budget enforcement, including `BudgetExceeded`.
  - Implemented `TaskCompiler`, worker protocol, default worker registry, dispatcher, deterministic worker stubs, and `WorkerKernelRuntime`.
  - Follow-up correction pass aligned kernel internals to `Plan -> Task -> Result` contract fields (`budget`, `max_tool_calls`, `max_model_calls`, `usage`, `errors`, `warnings`, `run_id`, `producer`).
  - Follow-up correction pass aligned pre-dispatch and post-result budget outcomes to explicit `budget_exceeded` result status.
  - Added tests for direct/code execution, budget rejection before dispatch, budget rejection after over-budget worker result, invalid plan handling, and unknown worker handling.
  - Verification passed: `uv run pytest tests/test_worker_kernel.py -q`.
  - No blockers.

## Objective

Implement the worker-kernel execution boundary: validate plan, enforce budget, compile tasks, dispatch workers, collect artifacts/results, and return a final `Result`.

## Files

- `app/worker_kernel/__init__.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/dispatcher.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/runtime.py`
- `app/worker_kernel/workers/__init__.py`
- `app/worker_kernel/workers/base.py`
- `app/worker_kernel/workers/direct_worker.py`
- `app/worker_kernel/workers/repo_worker.py`
- `app/worker_kernel/workers/code_worker.py`
- `app/worker_kernel/workers/research_worker.py`
- `app/worker_kernel/workers/infra_worker.py`
- `app/worker_kernel/workers/verify_worker.py`
- `tests/test_worker_kernel.py`

## Steps

1. Implement `BudgetExceeded` and `BudgetGate`.
2. Implement `TaskCompiler` to map `PlanStep` to `Task` and resolve named input artifacts from previous worker results.
3. Implement `BaseWorker` protocol and `WorkerRegistry`.
4. Implement simple deterministic workers. They should return artifacts with IDs matching expected outputs and usage counts within task maxima.
5. Implement `WorkerDispatcher` as a thin registry lookup plus `worker.run(task)` call.
6. Implement `WorkerKernelRuntime.run(plan: Plan) -> Result` using the provided pseudo-code shape.
7. Add tests for normal direct/code execution, budget rejection before dispatch, budget rejection after an over-budget worker result, invalid plan rejection, and unknown worker rejection.

## Verification

```bash
uv run pytest tests/test_worker_kernel.py -q
```

## Risks

- Unknown worker types must fail loudly; do not silently skip steps.
- Result aggregation must preserve worker results in metadata without adding new top-level contract objects.
- Budget rejection should happen before dispatch for invalid plans; tests can use a counting/failing worker to ensure no dispatch occurred.
- Budget rejection must also happen after a worker result if reported usage exceeds the allowed budget; tests should use a custom worker that over-reports usage.
- Empty plans, malformed budgets, and unknown worker types must fail loudly rather than producing a misleading completed result.

## Rollback

Remove or revert worker-kernel package and tests.
