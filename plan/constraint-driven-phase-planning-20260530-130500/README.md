# Plan: Constraint-Driven Phase Planning (DISCOVER → FINALIZE)

## Goal

Design the smallest backward-compatible architecture update that adds constraint-driven planning phases (`DISCOVER/ANALYZE/RESEARCH/DESIGN/MUTATE/VERIFY/FINALIZE`) while preserving the current `decompressor -> planner -> worker_kernel` topology and worker set.

## Status

Drafted from current repository patterns. No implementation code changed.

## Artifacts

- `plan.md` — full architecture recommendation and rollout plan
- `research/existing-patterns.md` — concrete codebase findings
- `phases/phase-1-schema-baseline.md`
- `phases/phase-2-validator-and-prompts.md`
- `phases/phase-3-execution-and-tests.md`

## Recommended first implementation step

Add backward-compatible optional phase fields to `PlanStep`/`Plan.metadata` in `app/schemas.py`, plus parser/validation tests that prove old plans still pass unchanged.
