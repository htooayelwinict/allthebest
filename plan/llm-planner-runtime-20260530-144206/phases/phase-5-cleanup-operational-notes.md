# Phase 5: Cleanup And Operational Notes

## Status

- ✅ Completed for this slice (static selector/planner path no longer primary; runtime uses LLM compiler when available with safe fallback).

## Goal

Clean up obsolete planner skeleton code and document planner operations after the LLM planner is stable.

## Scope

- Remove or quarantine static planner classes if no longer used.
- Document planner configuration.
- Add optional planner smoke script.
- Update plan status and operational notes.

## Likely Files

- `app/planner/selector.py`
- `app/planner/planners/*.py`
- `app/planner/__init__.py`
- `scripts/smoke_test_plans.py` optional
- `README.md` or project docs if planner config is externally visible
- This plan's `README.md` and phase docs

## Cleanup Options

### Option A: Remove static planner classes

Pros: cleaner architecture.

Cons: bigger diff; tests must fully migrate.

### Option B: Keep static planners as fallback only

Pros: safer rollout.

Cons: old code can confuse future maintenance.

### Option C: Keep only a safe fallback planner

Pros: good balance; one emergency path remains.

Cons: still a second planning path.

## Recommendation

Keep only a safe observe-only fallback during initial rollout. Remove domain-specific static planners after LLM planner tests and smoke scripts are stable.

## Planner Smoke Script Idea

Future command:

```bash
uv run python scripts/smoke_test_plans.py "<prompt>"
```

Flow:

```text
prompt -> decompressor runtime -> planner runtime -> print envelope summary + plan JSON + validation diagnostics
```

No worker execution by default. Add `--execute` later if needed.

## Documentation Notes

Document:

- Planner env vars.
- Fake-client testing pattern.
- Safety validator rules.
- Fallback behavior.
- How planner metadata reports repairs/fallbacks.

## Verification

```bash
uv run pytest -q
```

Optional live smoke:

```bash
uv run python scripts/smoke_test_plans.py "do we have lighthouse sdk if we do, use it as async function to connect all transation apis and fix lagging issues"
```

## Exit Criteria

- Obsolete static planner code is removed or clearly fallback-only.
- Planner operation is documented.
- Full suite passes.
