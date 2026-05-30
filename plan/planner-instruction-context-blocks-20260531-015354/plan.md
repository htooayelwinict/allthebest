# Plan: Planner Instruction Context Blocks

## Goal

Add prompt-only planner policy so every generated `step.instruction` starts with a tiny context block that preserves essential facts, unknowns, action, prohibitions, and expected output for the worker.

## Acceptance Criteria

- Draft planner prompt tells the model to format every `step.instruction` with a compact context block.
- Repair planner prompt tells the model to repair missing or weak instruction context blocks.
- Existing direct-support and worker-plan routing behavior remains unchanged.
- No schema, validator, worker-kernel, worker, or graph topology changes.
- Tests verify the prompt policy is present and existing valid plans still pass.
- Live QA confirms direct-support and mutating worker plans still produce valid contracts.

## Existing Patterns

- `app/planner/prompt_chain.py` owns draft and repair prompt policy.
- `tests/test_planner.py` asserts prompt contents and fake-client planner behavior.
- `TaskCompiler` passes `PlanStep.instruction` directly into `Task.instruction`.
- Workers use `task.instruction` as their main content payload.
- Current planner prompt already uses structured sections for `instructions`, `safety_policies`, `plan_archetypes`, `artifact_mapping_rules`, `phase_model`, and schema.

## Files To Change

- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Do Not Change

- `app/schemas.py`
- `app/planner/validator.py`
- `app/worker_kernel/*`
- `app/graph.py`
- Runtime topology

## Instruction Block Policy

Recommended minimal block:

```text
Known facts: <facts from envelope, prior artifacts, phase context>
Unknowns: <missing details or evidence gaps; use "none" when none>
Do now: <single primary action for this step>
Do not do: <scope/safety boundaries for this step>
Output: <expected output artifact names and success signal>
```

Additional mutation-sensitive fields can be included inside the same lines rather than adding a larger template:

- `Known facts` should mention `mutation_scope`, `rollback_plan`, evidence/design artifacts when relevant.
- `Do not do` should mention no writes outside scope and no claims without evidence.
- `Output` should name expected artifacts such as `change_summary`, `rollback_patch`, or `verification_report`.

## Prompt Placement

- Add the policy near existing instruction-generation rules after the phase/mode/task_id rules and before artifact/budget rules.
- Add a small `instruction_context_block` object to the prompt payload with field definitions and direct/mutation examples.
- Add repair instructions near current artifact dependency and phase repair rules.

## Test Plan

- Add assertions that draft prompt contains `instruction_context_block` and each required label.
- Add assertions that repair prompt instructs repairing missing context blocks.
- Keep existing planner fake-client tests intact.

## Live QA Plan

After implementation, run a five-level live batch similar to the latest successful set:

- lowest: gratitude/chitchat direct-support
- low: MRT/transit support direct-support
- medium: conceptual infra explanation direct-support
- high: mutating code/debug plan
- highest: security/data isolation mutating plan

QA should check:

- contract validity remains `5/5` success
- direct-support instructions include the context block and no tool/file permissions
- mutating steps include the context block and maintain scope/rollback/evidence semantics

## Risks

- Prompt bloat may dilute existing rules.
- Rigid instruction formatting may make direct-support output too verbose.
- LLM may place labels in instructions but fill them with weak content.
- Validator does not enforce this, so compliance remains prompt-dependent.

## Mitigations

- Keep the context block to five short labels.
- Do not add validator parsing in the first pass.
- Keep examples compact and phase-agnostic.
- Verify with prompt assertions and live QA rather than broad runtime changes.

## Recommended First Implementation Step

Update `app/planner/prompt_chain.py` draft and repair prompts with the context-block policy and compact examples, then add prompt-content tests in `tests/test_planner.py`.
