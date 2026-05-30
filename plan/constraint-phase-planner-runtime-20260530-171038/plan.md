# Implementation Plan: Constraint-Driven Phase Planner

## Goal

Implement a constraint-driven, phase-aware planner that consumes decompressor envelope signals and emits validated multi-step plans using canonical phases:

- `DISCOVER`
- `ANALYZE`
- `RESEARCH`
- `DESIGN`
- `MUTATE`
- `VERIFY`
- `FINALIZE`

The design must remain domain-agnostic and avoid over-engineering.

## Acceptance Criteria

- Planner output can include phase-aware steps without breaking existing plans.
- Planner derives phase sequencing from envelope constraints/risks/confidence/ambiguity, not domain strings.
- Multi-task requests can be represented under one plan with per-task phase progression.
- Deterministic validator enforces phase ordering and mutation safety invariants.
- Existing topology remains unchanged:
  - `decompressor_node -> planner_node -> worker_kernel_node -> END`
- Existing workers continue to work:
  - `direct_worker`, `repo_worker`, `code_worker`, `research_worker`, `infra_worker`, `verify_worker`

## Current State Summary

- Planner is already LLM prompt-chain + deterministic validation.
- Validator currently enforces artifact ordering, mutation gating, bounded writes, rollback artifacts, and verification.
- Budget under-sizing is auto-normalized in planner prompt-chain.
- Worker kernel is linear and artifact-driven.

This is a good baseline; full refactor is not required.

## Recommended Architecture (Minimal Change)

Use a single planner runtime with three internal logical stages (not separate graph nodes):

1. `derive_task_graph` (LLM stage)
   - Convert one envelope into one or more abstract tasks.
   - Capture each task's objective, ambiguity, and risk summary.

2. `compile_phase_plan` (LLM stage)
   - For each task, select required phases from canonical phase order based on constraints.
   - Emit linear steps tagged with `task_id` + `phase` + `mode`.

3. `repair_phase_plan` (LLM stage, optional)
   - Repair schema/policy violations once, using deterministic validation feedback.

This gives “multi-planner for multi-task” behavior without adding new runtime components.

## Ideal Output Contract (Backward Compatible Evolution)

### Plan (additive fields)

- `execution_pattern: str | None`
- `global_invariants: list[str]`

### PlanStep (additive fields)

- `phase: str | None`
- `mode: str | None`
- `task_id: str | None`

All new fields remain optional for backward compatibility.

## Constraint-Driven Rules (No Domain Hardcoding)

Rules are driven by envelope signals:

1. If mutation intent/risk/constraint exists and scope is ambiguous, require pre-mutation `DISCOVER`.
2. If any constraint/context requires evidence, require `ANALYZE` and/or `RESEARCH` evidence artifacts before mutation claims.
3. If verification is required or mutation occurs, require post-mutation `VERIFY`.
4. If confidence is low, block early mutation and require discovery/analysis first.
5. If mutation occurs, writes must be path-bounded and rollback output must exist.
6. If task mutates, require per-task `FINALIZE` (or global finalize step) summarizing outcomes and unresolved risks.

These are policy-driven, not domain-specific.

## Phase-to-Worker Mapping (Current Worker Set)

Until new workers exist, planner maps phases onto existing workers:

- `DISCOVER` -> `repo_worker`
- `ANALYZE` -> `research_worker` or `infra_worker` (observe-only)
- `RESEARCH` -> `research_worker`
- `DESIGN` -> `research_worker` (plan-only mode)
- `MUTATE` -> `code_worker` (bounded mutation)
- `VERIFY` -> `verify_worker`
- `FINALIZE` -> `direct_worker` or `verify_worker` summary step

No new worker class is required in the first rollout.

## Files Likely To Change

- `app/schemas.py`
- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `app/planner/runtime.py` (only if extra stage wiring is needed)
- `app/worker_kernel/compiler.py` (optional metadata pass-through)
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Phased Implementation

See phase docs under `phases/`.

### Phase 1
- Additive schema fields for `phase/mode/task_id/execution_pattern/global_invariants`.

### Phase 2
- Add constraint-driven phase policy checks in validator.

### Phase 3
- Upgrade prompt-chain to phase-graph planning stages (`derive_task_graph`, `compile_phase_plan`, `repair_phase_plan`).

### Phase 4
- Add multi-task tests + compatibility tests.

### Phase 5
- Optional cleanup and docs; no graph-node refactor.

## Risks and Mitigations

- Risk: over-constraining validator and rejecting useful plans.
  - Mitigation: phase rules enforced only when mutation/evidence constraints are present.

- Risk: over-engineering via new workers/scheduler.
  - Mitigation: explicitly defer new workers and DAG execution.

- Risk: brittle intent keyword gates.
  - Mitigation: prefer constraint/risk/confidence/ambiguity checks first.

## Rollback Strategy

- Keep new schema fields optional.
- Keep runtime path unchanged (`PlannerRuntime.run -> Plan`).
- If phase mode regresses, planner can still output old shape and pass compatibility validation.

## Verification Commands

```bash
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

## What Not To Build Yet

- No new LangGraph nodes.
- No separate phase scheduler.
- No new worker types (`analysis_worker`, `design_worker`, `mutation_worker`, etc.) yet.
- No domain ontology routers.
- No kernel-level automatic replan loop.

## Recommended First Implementation Step

Implement Phase 1 (additive schema evolution) with strict compatibility tests. Then layer Phase 2 validator logic.
