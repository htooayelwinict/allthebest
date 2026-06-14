# Phase 3 - Phase Planner Runtime

## Goal

Build a planner that emits `PhasePlan`, not worker-type plans.

## Files

- `appV2/planner/__init__.py`
- `appV2/planner/contracts.py`
- `appV2/planner/prompt_chain.py`
- `appV2/planner/runtime.py`
- `tests/test_appv2_phase_planner.py`

## Tasks

- Implement `PhasePlannerRuntime.from_env`.
- Implement prompt-chain stages:
  - `draft_phase_skeleton`
  - `draft_artifact_contracts`
  - `draft_phase_plan`
  - validation
  - one repair call
  - internal replan call
- Encode seven phases without worker types.
- Add phase-level policies:
  - `allowed_tool_groups`
  - `mutation_policy`
  - `verification_policy`
  - `acceptance_checks`
- Validate phase artifact flow using `AppV2Validator`.
- Add deterministic fallback only for safe non-mutation clarification/finalization.

## Tests

```bash
uv run pytest tests/test_appv2_phase_planner.py tests/test_appv2_validator.py -q
```

## Done When

- Planner fake client can produce a full phase plan.
- Invalid draft gets repaired once.
- Planner rejects worker-type fields if a model leaks them.
- Mutation plans require `VERIFY` after `MUTATE`.
- Replan keeps completed ledger artifacts and replaces only remaining phase obligations.

## Status

Completed 2026-06-05.
