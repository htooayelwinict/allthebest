# Phase 1: Backward-Compatible Schema Evolution

## Goal

Add phase-aware planning fields without breaking existing plan execution.

## Scope

- Add optional fields to plan schemas:
  - `Plan.execution_pattern`
  - `Plan.global_invariants`
  - `PlanStep.phase`
  - `PlanStep.mode`
  - `PlanStep.task_id`
- Preserve existing required fields and behavior.

## Files

- `app/schemas.py`
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`

## Verification

```bash
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
```

## Exit Criteria

- Old plan fixtures still pass unchanged.
- New fields parse and serialize correctly.
