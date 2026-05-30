# Planner Instruction Context Blocks

## Goal

Plan a prompt-only improvement that makes every planner-generated `step.instruction` self-contained for workers by adding a compact context block.

## Decision

Use prompt policy only. Do not change runtime topology, schemas, validator rules, worker kernel, or worker implementations in the first pass.

## Why

Workers primarily receive `Task.instruction`, selected artifacts, permissions, and metadata. Essential envelope context can be lost if the instruction is too terse.

## Source Of Truth

- `plan.md`

## Research

- `research/requirements.md`
- `research/existing-code.md`
- `research/references.md`

## Phases

- `phases/phase-1-prompt-policy.md` — completed 2026-05-31.
- `phases/phase-2-tests-and-live-qa.md` — automated tests and two-prompt live QA completed 2026-05-31; five-level live QA remains optional follow-up.

## Latest Verification

- `uv run pytest tests/test_planner.py -q` — 48 passed.
- `uv run pytest -q` — 87 passed.
- Live two-prompt QA saved at `research/live-two-prompt-qa-20260531.json` — 2/2 succeeded, 0 QA issues.
