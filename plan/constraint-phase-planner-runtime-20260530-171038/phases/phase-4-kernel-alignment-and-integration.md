# Phase 4: Kernel Alignment and Integration

## Goal

Ensure worker kernel can execute phase-tagged plans without behavior regressions.

## Scope

- Optional pass-through of `phase/mode/task_id` into `Task.metadata`.
- No scheduler changes.
- Validate end-to-end graph with decompressor + planner + kernel.

## Files

- `app/worker_kernel/compiler.py`
- `app/worker_kernel/runtime.py` (only if needed)
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Verification

```bash
uv run pytest tests/test_worker_kernel.py tests/test_graph.py -q
```

## Exit Criteria

- End-to-end flow remains stable.
- Phase metadata is preserved where needed.
