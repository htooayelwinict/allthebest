# Planner Prompt Optimization

Refactor the current passing planner prompt policy so it is easier to maintain while preserving behavior.

This is prompt-only work. Runtime topology, schemas, validator rules, worker kernel behavior, and graph wiring stay unchanged.

## Current Baseline

- Planner prompt policy lives in `app/planner/prompt_chain.py`.
- Prompt-content and fake-client planner tests live in `tests/test_planner.py`.
- Latest full tests before this plan: `87 passed`.
- Latest current-model five-level live QA baseline: `plan/live-complexity-qa-current-model-20260531-004706.json` with `5/5` success and `0` QA issues.
- Latest two-prompt instruction-context live QA: `plan/planner-instruction-context-blocks-20260531-015354/research/live-two-prompt-qa-20260531.json` with `2/2` success and `0` QA issues.

## Plan

See `plan.md` for the implementation plan and `phases/` for execution steps.
