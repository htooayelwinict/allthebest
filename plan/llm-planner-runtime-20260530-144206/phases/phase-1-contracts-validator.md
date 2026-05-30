# Phase 1: Contracts And Validator

## Status

- ✅ Completed (implemented in code and tests).

## Goal

Create the planner-owned contracts and deterministic validation layer before introducing LLM-generated plans.

## Scope

- Add a small planner model-client protocol.
- Add a planner validation module.
- Keep current static planner runtime behavior intact during this phase.
- Add focused tests for validation rules.

## Likely Files

- New `app/planner/contracts.py`
- New `app/planner/validator.py`
- Update `tests/test_planner.py` or add `tests/test_planner_validator.py`

## Validator Rules

- Plan schema validates as `Plan`.
- `plan.request_id` matches `envelope.request_id`.
- At least one step exists.
- Step IDs are unique.
- Worker types are allowed:
  - `direct_worker`
  - `repo_worker`
  - `code_worker`
  - `research_worker`
  - `infra_worker`
  - `verify_worker`
- Step budgets are non-negative.
- Plan budget covers step budget sums.
- Plan budget `max_workers` covers step count.
- Every `input_artifact` is produced by an earlier step.
- Only appropriate workers can request write permissions.
- Mutation requires prior discovery when envelope indicates unknown target/dependency/performance context.
- Mutation requires later verification.

## Tests

- Valid observe-only plan passes.
- Valid observe -> patch -> verify plan passes.
- Unknown worker type fails.
- Missing input artifact fails.
- Future artifact dependency fails.
- Budget undercount fails.
- Write step before observe fails for ambiguous/unknown target envelope.
- Write step without verify fails.

## Verification

```bash
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
```

## Exit Criteria

- Validator exists and is covered.
- No LLM calls have been introduced yet.
- Static planner behavior still passes existing tests or updated equivalent tests.
