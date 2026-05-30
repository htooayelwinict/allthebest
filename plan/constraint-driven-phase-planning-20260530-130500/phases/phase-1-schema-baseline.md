# Phase 1 — Backward-Compatible Schema Baseline

## Goal

Add optional phase and envelope-task annotations to planning schemas without breaking existing plans.

## Candidate files

- `app/schemas.py`
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`

## Scope

- Add optional `phase` to `PlanStep` (`Literal` enum or constrained string).
- Add optional `task_id` to `PlanStep` to group steps for multi-task envelopes.
- Add optional plan-level metadata contract entries:
  - `phase_order`
  - `task_graph` / `task_groups`
  - `envelope_constraints_snapshot`

## Verification

- `uv run pytest tests/test_planner.py -q`
- `uv run pytest tests/test_worker_kernel.py -q`
