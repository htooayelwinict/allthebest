# Requirements Research: Constraint-Driven Multi-Phase Planning

## Question

How should planner runtime support generic multi-phase reasoning (`DISCOVER/ANALYZE/RESEARCH/DESIGN/MUTATE/VERIFY/FINALIZE`) and multi-task planning without domain hardcoding or heavy refactor?

## User Requirements (Normalized)

- Planner should reason in canonical phases.
- Ideal plan schema includes:
  - `execution_pattern`
  - `global_invariants`
  - per-step `phase`
  - per-step `mode`
  - bounded permissions
- Planning must be constraint-driven, e.g.:
  - `mutation_requested + ambiguous_scope` -> require discovery before mutation
  - `*_require_evidence` -> require analysis/research evidence before claims
  - `needs_verification` -> require verify after mutation
- No domain-hardcoded gates.
- Avoid over-engineering.
- Keep existing topology unless absolutely necessary.

## Implications

1. Phase representation must be part of plan contract (optional/additive first).
2. Validator should enforce phase order from envelope policy signals.
3. Prompt-chain should produce phase-aware output with per-task grouping.
4. Existing workers must be reused through phase->worker mapping.
5. Multi-task behavior should be planner-level composition, not kernel rewrite.

## Non-Goals

- Introducing new graph nodes now.
- Implementing parallel DAG scheduler now.
- Introducing domain-specific planner classes.
- Adding many new worker types immediately.

## Acceptance Criteria for Implementation

- Backward compatibility for old plans.
- Deterministic phase policy validation based on envelope constraints/risks/confidence/ambiguity.
- Prompt-chain can emit multiple task groups in one plan.
- Full tests pass.
