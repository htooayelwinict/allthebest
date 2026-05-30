# Phase 2: Constraint-Driven Phase Validator

## Goal

Enforce phase progression and mutation safety from envelope policy signals.

## Scope

- Extend validator with phase/task checks:
  - allowed phase enum
  - per-task phase ordering
  - discover-before-mutate when ambiguity/risk/constraints require it
  - evidence-before-claim when constraints/context require evidence
  - verify-after-mutate when mutation or verification constraints exist
  - bounded write + rollback + stop/replan checks remain enforced

## Design Notes

- Drive from envelope fields (`constraints`, `risks`, `confidence`, `ambiguity`) first.
- Avoid domain string routing in phase policy.

## Files

- `app/planner/validator.py`
- `tests/test_planner.py`

## Verification

```bash
uv run pytest tests/test_planner.py -q
```

## Exit Criteria

- Phase policy tests pass.
- No regression in existing non-phase plans.
