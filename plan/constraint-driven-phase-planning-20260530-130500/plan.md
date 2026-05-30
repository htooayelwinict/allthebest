# Implementation Plan: Constraint-Driven Phase Planning

## Goal

Add minimal, domain-agnostic support for constraint-driven planning phases (`DISCOVER/ANALYZE/RESEARCH/DESIGN/MUTATE/VERIFY/FINALIZE`) that can handle multi-task envelopes, without changing the `decompressor -> planner -> worker_kernel` topology or current worker set.

## Acceptance criteria

- Existing plans without phase fields continue to validate and execute unchanged.
- Planner prompt-chain can emit phase-tagged steps and optional task-group metadata.
- Deterministic validator enforces phase sequencing from envelope constraints/risks/confidence/ambiguity (not domains).
- Multi-task envelope plans can encode per-task phase progression in one `Plan`.
- Worker kernel remains sequential; no topology changes.

## Existing patterns

- Topology fixed in `app/graph.py`.
- Planner is already prompt-chain + deterministic validator (`app/planner/prompt_chain.py`, `app/planner/validator.py`).
- Worker capabilities and registry already stable (`app/planner/contracts.py`, `app/worker_kernel/registry.py`).
- Envelope already carries constraint/confidence/ambiguity/risk signals (`app/schemas.py`).

## Architecture options with tradeoffs

### Option A — Step-level phase tags + validator rules (minimal)

**Shape**
- Add optional `phase` and `task_id` fields to `PlanStep`.
- Keep `Plan.steps` linear as today.
- Add optional metadata maps for task envelopes and phase policy.

**Pros**
- Smallest diffs; no scheduler rewrite.
- Fully backward-compatible with existing plans/workers/tests.
- Reuses existing validator architecture.

**Cons**
- No native DAG/task scheduler.
- Multi-task interleaving remains planner-authored, not kernel-optimized.

### Option B — Add `PlanTask` envelope + nested phases

**Shape**
- Add new `Plan.tasks[]`, each containing phase buckets and steps.
- Compile tasks into steps at planner/runtime boundary.

**Pros**
- Cleaner conceptual model for multi-task planning.
- Easier future parallelization.

**Cons**
- Medium diff size; compiler/runtime/validator complexity jumps.
- More migration/test burden now.

### Option C — Separate phase orchestrator module before kernel

**Shape**
- Add a new orchestration layer that transforms plan into execution schedule.

**Pros**
- Most extensible for advanced planning.

**Cons**
- Violates “smallest safe diff” intent.
- Higher risk to stable topology/contracts.

## Recommended path (smallest safe diffs)

Choose **Option A** now.

Rationale: It satisfies all required outcomes with additive schema/validator/prompt changes only, preserves current runtime topology and worker contract, and avoids introducing a scheduler or nested plan model prematurely.

## Proposed backward-compatible schema changes

Target: `app/schemas.py`

### PlanStep (additive)

- `phase: str | None = None`
  - allowed values (validator-enforced): `DISCOVER|ANALYZE|RESEARCH|DESIGN|MUTATE|VERIFY|FINALIZE`
- `task_id: str | None = None`
  - groups steps into envelope-derived tasks (default single task if omitted)

### Plan (metadata extensions, additive)

Keep `Plan` shape unchanged; add conventions under `Plan.metadata`:

- `metadata["phase_policy"]`:
  - `phase_order`: canonical order list
  - `allow_phase_skip`: bool (default true for minimalism)
- `metadata["task_groups"]`:
  - map of `task_id -> {objective, constraints_ref, risk_level}` (optional)
- `metadata["envelope_snapshot"]`:
  - minimal snapshot of `constraints/risks/confidence/ambiguity` used by planner

No required new top-level fields; old plans stay valid.

## Deterministic validation rules (domain-agnostic)

Extend `PlannerPlanValidator` in `app/planner/validator.py` with phase logic based on envelope signals only.

### Core structural rules

1. If any step has `phase`, all steps must have `phase`.
2. `phase` must be in allowed phase set.
3. `FINALIZE` may appear at most once per `task_id` and must be terminal for that task.
4. `MUTATE` steps must use write-capable worker and preserve existing write-scope/rollback rules.
5. `VERIFY` required after last `MUTATE` for each mutated task (existing global rule becomes per-task when task_id present).

### Sequence/gating rules from envelope confidence/ambiguity/risks/constraints

6. If envelope indicates mutation risk (e.g., contains mutation-related risk/constraint), first `MUTATE` requires prior non-mutating evidence phases for that task.
7. Low confidence (`confidence < threshold`, e.g. 0.7) forbids first phase being `MUTATE`.
8. Non-empty ambiguity requires pre-mutation clarification/evidence phase (`DISCOVER|ANALYZE|RESEARCH`).
9. If constraints demand verification semantics, require `VERIFY` phase after mutation for affected task.
10. Artifact dependency ordering remains authoritative across all phases.

### Multi-task envelope rules

11. If `task_id` is used, validate each task independently for phase progression.
12. Cross-task artifact dependencies are allowed but must still obey produced-before-consumed ordering.

## Files to change (implementation)

- `app/schemas.py` (optional fields + doc comments)
- `app/planner/validator.py` (phase/task deterministic checks)
- `app/planner/prompt_chain.py` (prompt instructions to emit compliant phase-tagged plans)
- `app/worker_kernel/compiler.py` (optional: propagate phase/task metadata into `Task.metadata`)
- `tests/test_planner.py` (phase validation + multi-task cases)
- `tests/test_worker_kernel.py` (phase metadata pass-through safety)
- `README.md` (brief architecture note)

## Implementation phases

### Phase 1 — Schema baseline (additive only)

- Add optional `phase` and `task_id` to `PlanStep`.
- Add/standardize metadata keys in plan docs/tests.
- Ensure no behavior change for old plans.

### Phase 2 — Validator + prompt-chain updates

- Add deterministic phase/task validation.
- Update prompt-chain draft/repair instructions to emit phase-tagged steps and per-task sequencing.
- Keep non-domain policy language.

### Phase 3 — Kernel metadata pass-through + integration tests

- Pass `phase/task_id` into `Task.metadata` (if present).
- Verify end-to-end graph still works with old and new plans.

## Risks and unknowns

- **Risk:** Over-constraining phase rules could reject useful simple plans.
  - **Mitigation:** Make phase optional; enforce only when phase-tagged planning is used.
- **Risk:** Existing keyword-based risk signals in validator still partly domain-like.
  - **Mitigation:** Introduce a neutral signal adapter for mutation/evidence intent from envelope constraints first.
- **Unknown:** Exact envelope encoding for “multi-task” intent is not standardized yet.
  - **Assumption:** planner can infer task groups and persist them in `metadata.task_groups` initially.

## Rollback considerations

- Revert additive schema fields and new validator branch; old plan behavior remains intact.
- Because no topology or worker contract changes are required, rollback is low impact.

## Verification commands

```bash
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

## What NOT to build yet (anti-overengineering)

- Do **not** add a new graph node or alter graph topology.
- Do **not** build a separate phase scheduler/DAG executor.
- Do **not** introduce domain ontologies or hardcoded domain routers.
- Do **not** add new worker types.
- Do **not** add persistence layer/migrations for plan history yet.
- Do **not** add automatic replanning loops in kernel runtime.

## Recommended first implementation step

Implement Phase 1 only: additive `PlanStep.phase`/`PlanStep.task_id` plus compatibility tests proving old plans run unchanged. This unlocks deterministic phase validation in Phase 2 with minimal risk.

## Plan folder path

`plan/constraint-driven-phase-planning-20260530-130500/`
