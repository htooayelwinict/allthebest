# Phase 2: LLM Prompt Chain

## Status

- ✅ Completed (draft + one repair attempt with diagnostics metadata).

## Goal

Add a planner LLM prompt chain that drafts `Plan` JSON from an envelope and repairs invalid output once.

## Scope

- Build prompt generation for `draft_plan`.
- Build repair prompt generation for `repair_plan`.
- Validate model output through `Plan.model_validate_json` and planner validator.
- Add diagnostics metadata.
- Use fake model clients in tests.

## Likely Files

- New `app/planner/prompt_chain.py`
- Update `app/planner/contracts.py`
- Update/add planner prompt-chain tests.

## Prompt Inputs

- Envelope JSON.
- Plan JSON schema.
- Worker catalog and capability descriptions.
- Permission rules.
- Artifact dependency rules.
- Budget rules.
- Safety policies.
- Output-only-JSON instruction.

## Repair Inputs

- Original envelope JSON.
- Invalid draft JSON.
- Structured validation errors.
- Same schema and policy instructions.

## Metadata To Add To Plan

Recommended `plan.metadata["llm_planner"]` fields:

- `mode`: `completed`, `repaired`, or `failed`
- `stages`: stages attempted
- `model_calls`: number of model calls
- `repair_attempted`: boolean
- `validation_errors`: redacted validation summary when repaired or failed
- `envelope_input_type`: source envelope input type
- `envelope_complexity_hint`: source envelope complexity hint

## Tests

- Draft valid plan succeeds with one model call.
- Draft invalid JSON/schema repaired successfully with two model calls.
- Draft valid schema but unsafe policy violation repaired successfully.
- Draft invalid and repair invalid raises controlled planner error or returns configured safe fallback.
- Prompt contains worker catalog and envelope fields.
- Repair prompt contains validation errors.

## Verification

```bash
uv run pytest tests/test_planner.py -q
```

## Exit Criteria

- LLM planner chain can produce validated `Plan` from fake client output.
- Repair path is covered.
- No live model calls in tests.
