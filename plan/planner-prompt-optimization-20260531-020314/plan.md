# Plan: Planner Prompt Optimization

## Goal

Reduce duplication and attention dilution in the LLM planner prompt policy while preserving the current validated planning semantics and live QA baseline.

## Acceptance Criteria

- Prompt-only changes in `app/planner/prompt_chain.py` plus focused prompt-content test updates in `tests/test_planner.py`.
- No changes to `app/schemas.py`, `app/planner/validator.py`, `app/worker_kernel/*`, or `app/graph.py`.
- Runtime topology remains `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- Draft and repair prompts still include the existing critical policy anchors:
  - phase-aware output contract
  - direct-support archetype and direct-support template
  - artifact mapping rules
  - instruction context block labels
  - mutation scope, rollback, evidence, verification, and finalization rules
  - exact allowed modes and phase-to-mode mapping
- Tests pass:
  - `uv run pytest tests/test_planner.py -q`
  - `uv run pytest -q`
- If feasible, live QA is rerun against the current model using the existing five-level and two-prompt baselines.

## Existing Patterns

- `LLMPlanCompiler._draft_prompt` builds a structured JSON payload with `instructions`, `permission_semantics`, `allowed_modes`, `write_scope_artifacts`, `safety_policies`, `plan_archetypes`, `direct_support_plan_template`, `artifact_mapping_rules`, `instruction_context_block`, `phase_model`, `worker_catalog`, `envelope`, and `plan_schema`.
- `LLMPlanCompiler._repair_prompt` mirrors draft policy and includes `validation_errors` plus `previous_response`.
- Tests assert prompt contents by checking string anchors in the serialized JSON prompt.
- Deterministic validation enforces contracts but does not parse instruction internals.
- Budget normalization is retained because it is arithmetic accounting, not semantic synthesis.

## Files To Change

- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Files Not To Change

- `app/schemas.py`
- `app/planner/validator.py`
- `app/planner/runtime.py`
- `app/planner/contracts.py`
- `app/worker_kernel/*`
- `app/graph.py`

## Implementation Approach

Use a conservative code-level prompt refactor rather than a semantic rewrite:

- Extract shared prompt policy constants or small helper functions inside `app/planner/prompt_chain.py`.
- Reuse one direct-support plan template in both draft and repair prompts.
- Reuse one instruction-context-block payload in both draft and repair prompts, with repair-specific goal added only where needed.
- Keep the current model-visible wording for high-risk rules wherever possible.
- Keep all existing critical tokens and anchors that current tests and live behavior depend on.
- Do not introduce XML/Markdown prompt formats because the current JSON payload pattern is already tested and working.

## Phases

1. Extract shared prompt policy data.
2. Update draft and repair prompt builders to consume shared policy data without changing planner semantics.
3. Tighten prompt-content tests around shared policy anchors and regression-sensitive direct-support/runtime-action boundaries.
4. Run planner tests and full tests.
5. Record implementation progress and verification results in this plan folder.

## Risks

- Over-condensing rules may cause direct-support over-routing or mutation-plan contract drift.
- Draft and repair prompts may diverge if shared structures are not reused consistently.
- Removing apparently redundant wording may harm live model behavior even if unit tests pass.
- Prompt-content tests check anchors, not full LLM quality.

## Mitigations

- Keep wording for critical rules intact on the first pass.
- Prefer deduplication through Python shared structures over model-visible semantic compression.
- Preserve the latest successful live QA files as explicit baselines.
- Run narrow and broad tests before considering live QA.

## Verification

Run locally:

```bash
uv run pytest tests/test_planner.py -q
uv run pytest -q
```

Live QA, if model/API access is available:

- Repeat the five-level batch corresponding to `plan/live-complexity-qa-current-model-20260531-004706.json`.
- Repeat direct-support plus complex fintech two-prompt QA corresponding to `plan/planner-instruction-context-blocks-20260531-015354/research/live-two-prompt-qa-20260531.json`.

## Implementation Status

Completed.

## Verification Results

- `uv run pytest tests/test_planner.py -q` -> `48 passed`
- `uv run pytest -q` -> `87 passed`
- Live five-level QA -> `5/5` success, `0` failures, `0` QA issues
- Live QA artifact: `plan/planner-prompt-optimization-20260531-020314/research/live-five-level-qa-20260530-194934.json`

## Recommended First Implementation Step

Extract shared direct-support and instruction-context-block prompt payload helpers, then update `_draft_prompt` and `_repair_prompt` to reuse them while preserving existing prompt strings.
