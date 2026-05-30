# Phase 1: Shared Prompt Policy

## Goal

Extract duplicated prompt policy data into shared constants or helpers while preserving model-visible semantics.

## Steps

- Add shared helpers/constants in `app/planner/prompt_chain.py` for direct-support template and instruction-context-block payload.
- Reuse the shared direct-support template in both `_draft_prompt` and `_repair_prompt`.
- Reuse the shared instruction-context-block payload in both `_draft_prompt` and `_repair_prompt`, adding repair-specific metadata only in repair.
- Keep critical rule wording intact.

## Verification

- `uv run pytest tests/test_planner.py -q`

## Status

Completed.

## Result

- Added shared direct-support constants and `_direct_support_plan_template()` in `app/planner/prompt_chain.py`.
- Reused `direct_support_plan_template` from both draft and repair prompts.
- Replaced repeated model-visible direct-support archetype step shapes with `Use direct_support_plan_template exactly.`.
- Added `_instruction_context_block()` so draft and repair prompts share the required labels, with repair-specific `repair_goal` added only for repair.
- Preserved high-risk draft and repair instruction wording for phase/mode/artifact/mutation/verification contracts.
