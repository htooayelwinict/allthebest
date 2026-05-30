# Phase 2 — Deterministic Phase Validation + Planner Prompt Wiring

## Goal

Encode deterministic phase rules derived from constraints/risks/confidence/ambiguity, and instruct planner prompt-chain to emit compliant phase-tagged steps.

## Candidate files

- `app/planner/validator.py`
- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Scope

- Add phase ordering and gating rules (domain-agnostic):
  - no `MUTATE` before required `DISCOVER/ANALYZE/RESEARCH/DESIGN`
  - `VERIFY` after `MUTATE`
  - `FINALIZE` terminal when present
- Add confidence/ambiguity risk gates for mutation.
- Add multi-task envelope checks (each task has valid phase progression).

## Verification

- `uv run pytest tests/test_planner.py -q`
