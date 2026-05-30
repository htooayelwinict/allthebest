# LLM-Heavy Prompt-Chain Planner Refactor Research

Date: 2026-05-30 22:03:25

## Question

How should the planner runtime handle invalid structured plans while preserving the goal of an LLM-heavy prompt-chain architecture?

## Sources

- Context7 LangChain structured-output docs: structured output validation should provide specific validation errors back to the model and retry correction.
- Context7 OpenAI Cookbook structured-output examples: use schemas for reliable JSON outputs and pass constrained context/instructions rather than free-form parsing.
- Open Bridge second-pass architecture review: recommended generator -> deterministic validator -> targeted LLM repair loop over deterministic semantic AST rewriting.
- Repo-local evidence: batch-4 failures and current compiler implementation.

## Key Findings

- Deterministic validation is appropriate as a safety gate.
- Deterministic budget normalization is acceptable because it is arithmetic/runtime accounting, not semantic planning.
- Deterministic semantic normalization is not aligned with the LLM-heavy goal. The previous compiler rewrote phases, write scopes, execution patterns, and scope producer phases after the LLM output, which hid planner reasoning mistakes and made the deterministic layer act like a planner.
- Prompt chaining should repair invalid plans by giving the model precise validation errors and the previous JSON, then asking it to produce a corrected full JSON plan.
- A single repair pass is sometimes insufficient for strict validators, so a bounded second repair pass is a better prompt-chain pattern than deterministic semantic mutation.

## Decision

Keep deterministic validation rules. Remove semantic AST repair from the compiler. Use a bounded prompt chain:

1. `draft_plan`
2. deterministic schema/safety validation
3. `repair_plan_1` with validation errors and previous response
4. deterministic validation
5. `repair_plan_2` with remaining validation errors and previous response
6. deterministic validation or fail with diagnostics

The compiler now only normalizes budget arithmetic before validation.

## What Was Removed From Compiler Semantics

- Auto-filling missing phase/mode/task_id.
- Auto-generating execution_pattern/global_invariants.
- Reclassifying write-scope producers to DESIGN.
- Filling missing write_paths_from_artifacts.
- Rewriting phase-order inversions.

These are now LLM repair responsibilities, enforced by deterministic validators.

## Files Updated

- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Recommendation

Continue improving prompt specificity and validator error messages rather than adding deterministic semantic patching. If repeated failures remain, add targeted critique/repair prompt content or a separate LLM critique stage, not AST mutation.
