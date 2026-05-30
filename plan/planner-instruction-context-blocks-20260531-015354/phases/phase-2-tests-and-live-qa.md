# Phase 2: Tests And Live QA

Status: Partially completed 2026-05-31

## Goal

Verify that prompt-only instruction-block changes do not regress planner contract quality.

## Tests

Run:

```bash
uv run pytest tests/test_planner.py -q
uv run pytest -q
```

## Live QA

Run a five-level prompt batch and save full outputs:

- lowest direct-support
- low direct-support
- medium conceptual direct-support
- high mutating worker plan
- highest mutating worker plan

## QA Checks

- All runs succeed.
- `qa_issue_count` is zero.
- Each `step.instruction` includes all context-block labels.
- Direct-support remains one `direct_worker` `FINALIZE` step with no tool/file permissions.
- Mutating plans preserve DESIGN scope/rollback/verification, MUTATE scoped writes, and VERIFY context.

## Exit Criteria

- Full automated tests pass.
- Live QA shows no contract regression.

## Progress Notes

- Automated tests passed after the prompt-policy update:
  - `uv run pytest tests/test_planner.py -q` — 48 passed.
  - `uv run pytest -q` — 87 passed.
- Added prompt-content tests for draft and repair context-block policy.
- Ran the two user-requested live QA prompts and saved full outputs to `research/live-two-prompt-qa-20260531.json`:
  - simple MRT support prompt — success, direct-support shape preserved, all instruction labels present.
  - complex fintech dispute-state issue — success, mutating plan preserved scope/rollback/verify/finalize semantics, all instruction labels present.
- Live two-prompt QA summary: `success_count=2`, `failure_count=0`, `qa_issue_count=0`.

## Follow-up

- The original five-level live QA batch remains optional follow-up if broader regression sampling is desired.
