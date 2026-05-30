# Phase 3: Prompt-Chain Multi-Task Phase Planning

## Goal

Teach planner prompt-chain to generate multi-task phase-aware plans.

## Scope

- Introduce/extend planner stages:
  - `derive_task_graph`
  - `compile_phase_plan`
  - `repair_phase_plan`
- Keep one external planner runtime interface.
- Ensure emitted plans include:
  - `execution_pattern`
  - `global_invariants`
  - per-step `phase/mode/task_id`

## Files

- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Verification

```bash
uv run pytest tests/test_planner.py -q
```

## Exit Criteria

- Multi-task phase plan generation works with fake clients.
- Repair behavior remains deterministic and bounded.
