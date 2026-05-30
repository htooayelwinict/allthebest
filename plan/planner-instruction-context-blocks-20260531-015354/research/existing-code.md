# Existing Code Research

## Planner Prompt

`app/planner/prompt_chain.py` builds structured JSON prompts for draft and repair stages.

Current prompt strengths:

- phase-aware plan contract
- direct-support archetype
- artifact mapping rules
- mutation scope/rollback/verification rules
- repair instructions with validation feedback

Prompt risk:

- already dense; new instruction policy must be compact and placed carefully.

## Worker Instruction Path

`app/worker_kernel/compiler.py` copies `PlanStep.instruction` into `Task.instruction`.

Workers use `Task.instruction` directly:

- `DirectWorker` stores instruction as output content.
- `CodeWorker` and `VerifyWorker` are simple stubs now, but future LLM/tool workers will rely on task instructions.

## Tests

`tests/test_planner.py` already includes prompt-content assertions and fake-client validation tests.

Best-fit test style:

- assert prompt contains new policy labels
- assert existing valid direct-support and mutating plan shapes still pass
