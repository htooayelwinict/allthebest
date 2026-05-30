# Existing Patterns Research

## Runtime topology and boundaries

- Topology is hard-wired in `app/graph.py` as:
  - `START -> decompressor_node -> planner_node -> worker_kernel_node -> END`
- Planner and worker kernel communicate via `Plan`/`PlanStep` in `app/schemas.py`.
- Planner is already LLM prompt-chain + deterministic validator:
  - Prompt chain: `app/planner/prompt_chain.py`
  - Validation: `app/planner/validator.py`

## Current schema and planning model

- `Envelope` already carries constraint-like signals: `constraints`, `risks`, `confidence`, `ambiguity`, `complexity_hint`.
- `PlanStep` currently has execution fields only (worker_type/instruction/artifacts/budgets/permissions), no explicit phase field.
- `Plan.metadata` is extensible and already used for planner diagnostics and stop/replan metadata.

## Current worker/kernel behavior

- Worker registry is fixed and supports these workers:
  - `direct_worker`, `repo_worker`, `code_worker`, `research_worker`, `infra_worker`, `verify_worker`
- Worker kernel compiles and executes steps sequentially; no phase engine exists yet (`app/worker_kernel/runtime.py`, `compiler.py`).

## Deterministic validation today

- Existing validator already enforces non-domain safety constraints:
  - artifact dependency ordering
  - budget coverage
  - discovery-before-mutation (from envelope context/constraints)
  - verify-after-write
  - path-scoped writes
  - rollback/replan metadata
  - low-confidence mutation gating
- But some helpers still use keyword signals tied to domain words (`dependency`, `manifest`, `package`) and artifact-name heuristics.

## Tests and verification hooks

- Planning behavior covered in `tests/test_planner.py`.
- Worker execution behavior covered in `tests/test_worker_kernel.py`.
- Topology check in `tests/test_graph.py`.

## Implications for requested change

- Lowest-risk path: augment schema + validator + prompt instructions, not topology.
- Phase semantics should be represented as metadata/step annotations, then enforced deterministically.
- Multi-task envelopes can be modeled as task groups in metadata with grouped phase sequences; kernel can remain step-linear initially.
