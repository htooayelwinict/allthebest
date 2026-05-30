# Phase 3 — Minimal Execution Alignment and Integration Tests

## Goal

Keep worker-kernel execution linear while preserving phase/task metadata through compile/dispatch and validating end-to-end behavior.

## Candidate files

- `app/worker_kernel/compiler.py`
- `app/schemas.py`
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Scope

- Ensure `Task.metadata` carries through `phase` and `task_id` (if present).
- No new scheduler/orchestrator graph changes.
- Add integration tests for multi-task phase-tagged plans.

## Verification

- `uv run pytest tests/test_worker_kernel.py tests/test_graph.py -q`
- `uv run pytest -q`
