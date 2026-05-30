# Existing Code

## Prompt Builder

`app/planner/prompt_chain.py` owns planner prompt generation.

Current shape:

- `_draft_prompt` serializes one JSON prompt payload for `draft_plan`.
- `_repair_prompt` serializes one JSON prompt payload for `repair_plan_1` and `repair_plan_2`.
- Both prompts repeat direct-support template content.
- Both prompts repeat instruction-context-block content.
- Both prompts include mutation/rollback/evidence/verify/finalize policy, with repair-specific language in the repair prompt.

## Tests

`tests/test_planner.py` has fake-client tests and prompt-content assertions.

Existing prompt assertions cover:

- worker catalog and envelope presence
- instruction-context-block policy presence
- repair instruction-context-block policy presence
- direct-support archetype and artifact mapping rules
- direct-support guidance not overriding runtime actions

## Validator Boundary

`app/planner/validator.py` validates deterministic contract rules but does not parse or enforce instruction-context-block internals.

This means the prompt refactor must preserve model-visible instruction rules; validator changes are intentionally out of scope.
