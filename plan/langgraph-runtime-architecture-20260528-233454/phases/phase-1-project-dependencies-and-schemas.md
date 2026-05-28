# Phase 1: Project Dependencies and Schemas

## Status

- Completed: 2026-05-29
- Notes:
  - `pyproject.toml` already contained required dependencies (`langgraph`, `pydantic`) and dev dependency (`pytest`), so no metadata edits were required.
  - Added `app/__init__.py` and `app/schemas.py` with the required core models: `Envelope`, `PlanStep`, `Plan`, `Task`, `Result`, and `RuntimeState`.
  - Follow-up correction pass aligned `Envelope`, `Plan`, `Task`, and `Result` fields exactly to the plan contract (`model_validate`/`model_dump` compatible shape for graph state handoff).
  - Ran `uv sync` to refresh lockfile dependency resolution.
  - Verified schema imports with `uv run python -c "from app.schemas import Envelope, Plan, PlanStep, Task, Result, RuntimeState"`.
  - No blockers in this phase.

## Objective

Create the project foundation: dependencies, package scaffolding, core schemas, and graph state type.

## Files

- `pyproject.toml`
- `uv.lock`
- `app/__init__.py`
- `app/schemas.py`

## Steps

1. Add runtime dependencies: `pydantic`, `langgraph`.
2. Add dev dependency: `pytest`.
3. Sync/update lockfile with `uv sync` or `uv lock`.
4. Create `app/` package.
5. Implement `Envelope`, `PlanStep`, `Plan`, `Task`, `Result`, and `RuntimeState` in `app/schemas.py`.
6. Keep schema defaults exactly aligned with prompt fields and avoid additional contract classes.

## Verification

```bash
uv sync
uv run python -c "from app.schemas import Envelope, Plan, PlanStep, Task, Result, RuntimeState"
```

## Risks

- Dependency resolution may fail without network access.
- Pydantic v1 would not support `model_dump()`/`model_validate()`; ensure v2.

## Rollback

Revert `pyproject.toml`, `uv.lock`, and remove `app/` if no later phases have been applied.
