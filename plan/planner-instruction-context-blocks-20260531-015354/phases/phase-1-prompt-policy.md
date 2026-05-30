# Phase 1: Prompt Policy

Status: Completed 2026-05-31

## Goal

Add compact instruction-context-block requirements to draft and repair prompts.

## Changes

- Add instruction rule requiring every `step.instruction` to start with:
  - `Known facts:`
  - `Unknowns:`
  - `Do now:`
  - `Do not do:`
  - `Output:`
- Add an `instruction_context_block` payload section with field definitions and compact examples.
- Add repair instruction to rewrite weak or missing instruction context blocks.

## Constraints

- Do not change schema or validator.
- Do not add new graph nodes or runtime branches.
- Do not expand direct-support routing rules.

## Exit Criteria

- Prompt content includes the context block policy and labels.
- Existing planner tests still pass.

## Implementation Notes

- Added draft prompt rules requiring every generated `step.instruction` to start with `Known facts:`, `Unknowns:`, `Do now:`, `Do not do:`, and `Output:`.
- Added `instruction_context_block` payload definitions and compact direct-support/mutation examples.
- Updated the direct-support prompt templates so generated direct-support instructions demonstrate the required block.
- Added repair prompt rules to fix missing, weak, or non-leading context blocks without changing schemas or runtime topology.

## Verification

- `uv run pytest tests/test_planner.py -q` — passed, 48 tests.
- `uv run pytest -q` — passed, 87 tests.
