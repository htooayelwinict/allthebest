# Phase 4: LangGraph Assembly and Integration Tests

## Status

- Completed: 2026-05-29
- Notes:
  - Implemented `app/graph.py` with exactly three top-level node functions (`decompressor_node`, `planner_node`, `worker_kernel_node`) and exact registered node keys.
  - Implemented strict linear flow: `decompressor_node -> planner_node -> worker_kernel_node -> END` with no conditional branches.
  - Follow-up correction pass kept graph nodes as thin wrappers and ensured serialized state handoff uses corrected schema contracts while preserving `errors` in node returns.
  - Added graph integration tests for compiled invocation and required node key visibility (when exposed by LangGraph object internals).
  - Verification passed:
    - `uv run pytest tests/test_graph.py -q`
    - `uv run pytest -q`
    - `uv run python -c "from app.graph import build_graph; g = build_graph(); print(g.invoke({'user_input': 'what is docker', 'errors': []})['result']['status'])"`
  - No blockers.

## Objective

Wire the three runtime classes into a simple LangGraph `StateGraph` with thin node wrappers and full integration tests.

## Files

- `app/graph.py`
- `tests/test_graph.py`

## Steps

1. Import `StateGraph` and `END` from `langgraph.graph`.
2. Instantiate `DecompressorRuntime`, `PlannerRuntime`, and `WorkerKernelRuntime`.
3. Implement `decompressor_node(state)`, `planner_node(state)`, and `worker_kernel_node(state)` as thin wrappers.
4. Implement `build_graph()` with exactly three nodes and linear edges to `END`; registered node keys must be exactly `decompressor_node`, `planner_node`, and `worker_kernel_node`.
5. Add test invoking `build_graph().invoke({"user_input": "what is docker", "errors": []})`.
6. Run the full test suite.

## Verification

```bash
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

## Risks

- LangGraph may require minor API adjustments depending on installed version. Keep changes localized to `app/graph.py` and avoid changing architecture.
- Tests should verify observable flow and final state, not overfit to compiled graph internals.
- If LangGraph exposes node names safely, tests should also assert the exact registered node keys required by the architecture.

## Final report checklist

- Changed/created file tree.
- Final LangGraph flow with exact node keys.
- Four core schemas plus nested `PlanStep`.
- Planner selection behavior.
- Worker-kernel budget behavior, including pre-dispatch and post-result checks.
- Test commands and results.
- Known limitations.

## Rollback

Revert `app/graph.py` and `tests/test_graph.py` while keeping runtimes intact.
