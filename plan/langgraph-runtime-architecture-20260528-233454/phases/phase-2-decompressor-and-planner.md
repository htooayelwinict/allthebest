# Phase 2: Decompressor and Planner Runtime

## Status

- Completed: 2026-05-29
- Notes:
  - Implemented `DecompressorRuntime` with deterministic heuristics for direct question, code fix with file hint, and vague fix requests.
  - Implemented planner protocol, concrete planners (direct/code/research/infra/fallback), `PlannerSelector`, and `PlannerRuntime`.
  - Follow-up correction pass aligned decompressor output to `Envelope` contract fields (`input_type`, `intents`, `domains`, `risks`, artifact hints, context needs, budget hint, confidence).
  - Follow-up correction pass aligned planner outputs to `Plan` contract fields and required strategies (`direct_answer`, `observe_then_patch`, and `observe_first`).
  - Added tests for required request shapes and planner selection/plan structure.
  - Verification passed: `uv run pytest tests/test_decompressor.py tests/test_planner.py -q`.
  - Follow-up refactor implemented the prompt-chain decompressor recommendation from `research/suggest-prompt-chain-decompressor.md` as deterministic internal stages: normalize request, extract artifacts, classify intent/domain, infer risk/context, recommend planner, assemble and Pydantic-validate `Envelope`.
  - Expanded `Envelope` with bounded enrichment fields (`user_goal`, `execution_hints`, `planner_hint`, `planner_confidence`, `planner_alternatives`, `ambiguity`, `assumptions`) while keeping decompressor authority limited to understanding hints only.
  - Updated `PlannerSelector` to honor validated high-confidence planner hints before falling back to deterministic selector rules.
  - Refactor verification passed: `uv run pytest tests/test_decompressor.py tests/test_planner.py -q` and `uv run pytest -q`.
  - No blockers.

## Objective

Implement deterministic Phase 1 input classification and planner strategy selection without executing tools or mutating files.

## Files

- `app/decompressor/__init__.py`
- `app/decompressor/runtime.py`
- `app/planner/__init__.py`
- `app/planner/base.py`
- `app/planner/selector.py`
- `app/planner/runtime.py`
- `app/planner/planners/__init__.py`
- `app/planner/planners/direct.py`
- `app/planner/planners/code.py`
- `app/planner/planners/research.py`
- `app/planner/planners/infra.py`
- `app/planner/planners/fallback.py`
- `tests/test_decompressor.py`
- `tests/test_planner.py`

## Steps

1. Implement `DecompressorRuntime.run(user_input: str) -> Envelope`.
2. Add simple request ID generation that is deterministic enough for tests to assert prefixes rather than exact global counters if needed.
3. Implement heuristic extraction/classification for:
   - direct question (`what is docker`)
   - code fix with file hint (`fix network_sniffer.py`)
   - vague/ambiguous request (`fix the app`)
   - research and infra keyword paths for future coverage.
4. Implement a `BasePlanner` protocol/ABC with `planner_name` and `create_plan(envelope)`.
5. Implement Direct, Code, Research, Infra, and Fallback planners.
6. Implement `PlannerSelector` and call it only from `PlannerRuntime`.
7. Add tests for decompressor output and planner selection/plan shapes.

## Verification

```bash
uv run pytest tests/test_decompressor.py -q
uv run pytest tests/test_planner.py -q
```

## Risks

- Over-classification can make `fix the app` patch too early. Ensure first step is observation and write permissions are false for first step.
- Planner budgets must align with worker-kernel budget checks; CodePlanner's example budget must be at least the sum of step budgets.

## Rollback

Remove or revert decompressor/planner packages and tests.
