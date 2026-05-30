# Existing Code Research: Planner Boundary

## Question

What does the current codebase already provide for converting a decompressor envelope into worker-executable tasks?

## Key Files

- `app/schemas.py`
- `app/decompressor/contracts.py`
- `app/decompressor/runtime.py`
- `app/planner/runtime.py`
- `app/planner/selector.py`
- `app/planner/planners/*.py`
- `app/worker_kernel/runtime.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/workers/*.py`
- `app/graph.py`
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Current Runtime Flow

`app/graph.py` builds a fixed LangGraph topology:

```text
START -> decompressor_node -> planner_node -> worker_kernel_node -> END
```

The planner node currently does:

```python
envelope = Envelope.model_validate(state["envelope"])
plan = planner_runtime.run(envelope)
```

This is a clean boundary and should remain unchanged in shape.

## Envelope Contract

`app/schemas.py` defines `Envelope` with these fields:

- `request_id`
- `raw_input`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `domains`
- `risks`
- `artifacts`
- `context_needed`
- `constraints`
- `complexity_hint`
- `confidence`
- `ambiguity`
- `assumptions`
- `metadata`

`app/decompressor/contracts.py` defines the LLM-owned descriptive shape and forbids planner/kernel leakage. This is important: the planner should consume descriptive signals and produce planner-owned fields itself.

## Current Planner Shape

`app/planner/runtime.py` is minimal:

```python
planner = self._selector.select(envelope)
return planner.create_plan(envelope)
```

`app/planner/selector.py` deterministically chooses one of:

- `DirectPlanner`
- `CodePlanner`
- `ResearchPlanner`
- `InfraPlanner`
- `FallbackPlanner`

Selection is based on simple text matching against `input_type`, `intents`, `domains`, `risks`, `context_needed`, `constraints`, and `confidence`.

## Current Static Planners

### `CodePlanner`

Creates:

```text
observe_target(repo_worker) -> patch_target(code_worker) -> verify_patch(verify_worker)
```

Strengths:

- Correct observe-before-patch shape.
- Includes verification.
- Uses read/write/run permission separation.

Weaknesses:

- Ignores most envelope fields.
- Assumes a patch step should happen even if dependency, performance evidence, or target files are unknown.
- Cannot represent mixed research + code + performance plans well.

### `FallbackPlanner`

Creates one observe-only repo step.

Strengths:

- Safe for ambiguity.

Weaknesses:

- Too weak for high-complexity requests that need discovery, research, patching, and verification.

### `ResearchPlanner`, `InfraPlanner`, `DirectPlanner`

Each creates one static step. Useful as simple patterns but too limited as the main planner strategy.

## Worker Runtime Contract

`app/schemas.py` defines `Plan` and `PlanStep`:

```text
Plan
- plan_id
- request_id
- planner
- objective
- strategy
- steps
- budget
- success_criteria
- metadata

PlanStep
- step_id
- worker_type
- instruction
- input_artifacts
- output_artifacts
- max_tool_calls
- max_model_calls
- permissions
```

`app/worker_kernel/runtime.py` executes steps linearly. It compiles each `PlanStep` into a `Task`, dispatches it, stores produced artifacts by id, and stops on failed/blocked/budget_exceeded worker results.

## Artifact Dependency Rules

`app/worker_kernel/compiler.py` silently ignores missing `input_artifacts` because it only passes through artifacts found in the artifact store. This means planner validation should reject plans whose input artifacts are not produced by earlier steps, otherwise the worker may run with missing context unnoticed.

## Budget Rules

`app/worker_kernel/budget.py` enforces:

- Plan must contain at least one step.
- Step budgets must be non-negative.
- Number of steps must be `<= max_workers`.
- Sum of step tool calls must be `<= max_tool_calls`.
- Sum of step model calls must be `<= max_model_calls`.

Planner validation should check these before worker execution to produce better error handling and repair prompts.

## Worker Catalog

Registered worker types in `build_default_registry()`:

- `direct_worker`
- `repo_worker`
- `code_worker`
- `research_worker`
- `infra_worker`
- `verify_worker`

Current worker implementations are placeholder-like, but their permissions and artifact interfaces are already enough for planning.

## Existing Tests

`tests/test_planner.py` asserts deterministic planner selection and static plan shape. These tests will need to shift toward:

- Fake LLM planner output validation.
- Safety policy enforcement.
- Repair behavior.
- Backward-compatible deterministic fallback only if retained explicitly.

`tests/test_worker_kernel.py` already tests:

- Direct plan execution.
- Code flow execution.
- Budget rejection.
- Invalid empty plan handling.
- Unknown worker handling.

These are useful guardrails and should remain.

## Design Implication

The current planner layer can be simplified to one primary `LLMPlannerRuntime` or `LLMPlanCompiler`. Static planner classes are not necessary as the primary mechanism once the LLM planner receives:

- Envelope JSON.
- Worker catalog.
- Plan schema.
- Safety policies.
- Budget policies.
- Artifact dependency policies.

However, deterministic validation must remain outside the LLM.
