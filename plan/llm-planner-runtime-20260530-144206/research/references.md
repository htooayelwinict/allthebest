# References: LLM Planner Runtime

## Repo Sources

- `app/schemas.py`: shared `Envelope`, `Plan`, `PlanStep`, `Task`, `Result`, and `RuntimeState` schemas.
- `app/decompressor/contracts.py`: descriptive envelope contract and anti-planner-leak boundary.
- `app/decompressor/runtime.py`: runtime wrapper that produces enriched envelopes and metrics.
- `app/planner/runtime.py`: current planner runtime entrypoint.
- `app/planner/selector.py`: current deterministic planner selector.
- `app/planner/planners/code.py`: current observe/patch/verify pattern.
- `app/planner/planners/fallback.py`: current observe-only fallback pattern.
- `app/worker_kernel/runtime.py`: linear worker execution and artifact store behavior.
- `app/worker_kernel/compiler.py`: step-to-task compilation and input artifact passing.
- `app/worker_kernel/budget.py`: deterministic budget enforcement.
- `app/worker_kernel/registry.py`: worker type registry and default catalog.
- `tests/test_planner.py`: current planner behavior coverage.
- `tests/test_worker_kernel.py`: worker kernel behavior coverage.
- `tests/test_graph.py`: topology and graph integration coverage.

## Existing Plan Context

- `plan/llm-heavy-promptchain-decompressor-20260529-011624/`: decompressor implementation context.
- `plan/langgraph-runtime-architecture-20260528-233454/`: original runtime architecture context.

## External Sources

No external sources were used. The planner design is based on internal contracts and observed runtime behavior.

## Important Existing Constraint

The decompressor boundary is descriptive only. The planner must not ask the decompressor to produce planner steps, worker names, permissions, budgets, or execution graph fields.
