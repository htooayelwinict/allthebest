# Requirements

## User-Approved Scope

Perform a dedicated full planner-prompt optimization pass.

## Hard Constraints

- Prompt-only changes unless explicitly requested otherwise.
- Keep runtime topology unchanged.
- Do not add `PlannerGate` or routing between decompressor and planner.
- Do not change schema, validator, worker-kernel, worker, or graph contracts.
- Preserve LLM-heavy prompt chaining plus deterministic validation.
- Semantic repair stays in LLM repair prompts, not deterministic AST mutation.

## Required Contracts To Preserve

- Canonical phases: `DISCOVER / ANALYZE / RESEARCH / DESIGN / MUTATE / VERIFY / FINALIZE`.
- Allowed modes only: `observe_only`, `plan_only`, `bounded_mutation`, `verify_only`, `summarize_only`.
- Phase-to-mode mapping stays exact.
- Explicit permissions stay required: `read_files`, `write_files`, `run_commands`.
- `envelope.artifacts` remain semantic hints only.
- `step.input_artifacts` must reference earlier `step.output_artifacts` only.
- Mutating plans require prior design scope, rollback, evidence/design context, verify, and finalize behavior.
- Direct-support remains a normal phase-aware plan, not a separate runtime contract.

## Baselines

- `plan/live-complexity-qa-current-model-20260531-004706.json`
- `plan/planner-instruction-context-blocks-20260531-015354/research/live-two-prompt-qa-20260531.json`
