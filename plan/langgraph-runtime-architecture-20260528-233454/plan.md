# Implementation Plan: Phase 1 LangGraph Runtime Architecture

## Goal

Implement Phase 1 runtime architecture using LangGraph as the graph orchestration layer, with thin graph nodes delegating business logic to Python runtime classes and preserving the strict flow `Envelope → Plan → Task → Result`.

## Acceptance criteria

- Exactly three top-level LangGraph runtime nodes exist in `app/graph.py`: `decompressor_node`, `planner_node`, and `worker_kernel_node`.
- The compiled graph must register those exact node keys with `graph.add_node("decompressor_node", decompressor_node)`, `graph.add_node("planner_node", planner_node)`, and `graph.add_node("worker_kernel_node", worker_kernel_node)`.
- The compiled graph flow is `decompressor_node → planner_node → worker_kernel_node → END` with no Phase 1 conditional branches.
- `app/schemas.py` defines only the four core runtime objects plus needed nested shape: `Envelope`, `PlanStep`, `Plan`, `Task`, and `Result`.
- `RuntimeState` is simple and stores only `user_input`, serialized `envelope`, serialized `plan`, serialized `result`, and `errors`.
- `DecompressorRuntime` consumes `str` and emits `Envelope`; it does not create plan steps, choose workers, or execute tools.
- `PlannerRuntime` consumes `Envelope`, internally selects one planner strategy, and emits `Plan`; it does not execute tools, mutate files, or dispatch workers.
- `WorkerKernelRuntime` consumes `Plan`, validates it, enforces deterministic budget ceilings, compiles steps into tasks, dispatches workers, collects results, and emits `Result`.
- Workers use a single `BaseWorker` protocol/interface, consume only `Task`, return only `Result`, and do not know about graph/planner or call other workers.
- Tests cover direct question, code fix request, vague request, budget rejection before dispatch, budget rejection after an over-budget worker result, invalid plan handling, unknown worker handling, and compiled LangGraph invocation.

## Existing patterns

- Repository is effectively a new Python project: `pyproject.toml` exists with no dependencies, `uv.lock` contains only the local virtual package, and there are currently no `app/` or `tests/` Python files.
- `AGENTS.md` requires researching before editing, small reversible changes, durable plan artifacts, and exact verification commands.
- Because no runtime pattern currently exists, implementation should follow the requested structure closely and avoid unnecessary abstractions beyond the specified Phase 1 objects and folders.

## Files to change

### Project metadata

- `pyproject.toml` — add runtime dependencies `pydantic` and `langgraph`; add `pytest` to a dev dependency group if using project-managed test execution.
- `uv.lock` — update via `uv sync`/`uv lock` after dependency changes.

### Application package

- `app/__init__.py`
- `app/schemas.py`
- `app/graph.py`
- `app/decompressor/__init__.py`
- `app/decompressor/runtime.py`
- `app/planner/__init__.py`
- `app/planner/runtime.py`
- `app/planner/selector.py`
- `app/planner/base.py`
- `app/planner/planners/__init__.py`
- `app/planner/planners/direct.py`
- `app/planner/planners/code.py`
- `app/planner/planners/research.py`
- `app/planner/planners/infra.py`
- `app/planner/planners/fallback.py`
- `app/worker_kernel/__init__.py`
- `app/worker_kernel/runtime.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/dispatcher.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/workers/__init__.py`
- `app/worker_kernel/workers/base.py`
- `app/worker_kernel/workers/direct_worker.py`
- `app/worker_kernel/workers/repo_worker.py`
- `app/worker_kernel/workers/code_worker.py`
- `app/worker_kernel/workers/research_worker.py`
- `app/worker_kernel/workers/infra_worker.py`
- `app/worker_kernel/workers/verify_worker.py`

### Tests

- `tests/test_decompressor.py`
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Phase plan

### Phase 1 — Project dependencies and schemas

Add runtime dependencies and create the base package plus `app/schemas.py` with Pydantic models and `RuntimeState`. Keep `pytest` in a dev dependency group rather than runtime dependencies.

Independent verification:

- `uv run python -c "from app.schemas import Envelope, Plan, PlanStep, Task, Result"`
- `uv run pytest tests/test_decompressor.py tests/test_planner.py -q` once tests exist, or import smoke tests before runtime tests are added.

Rollback: revert `pyproject.toml`, `uv.lock`, and newly added `app/` files.

### Phase 2 — Decompressor and planner runtimes

Implement deterministic, heuristic Phase 1 decompression and planner strategy selection. Keep planner selection inside `PlannerRuntime` through `PlannerSelector`.

Independent verification:

- `uv run pytest tests/test_decompressor.py tests/test_planner.py -q`

Rollback: remove/restore `app/decompressor/`, `app/planner/`, and related tests.

### Phase 3 — Worker-kernel runtime, budget gate, compiler, dispatcher, workers

Implement budget enforcement before plan execution, before each task, and after each result. Add registry and simple workers that return deterministic `Result` artifacts suitable for Phase 1 tests.

Independent verification:

- `uv run pytest tests/test_worker_kernel.py -q`

Required worker-kernel test cases:

- Budget rejection before dispatch when requested step budgets exceed the plan budget.
- Budget rejection after a worker returns usage that exceeds the remaining budget.
- Invalid plan rejection for empty plans or malformed step budgets.
- Unknown `worker_type` fails loudly instead of being skipped silently.

Rollback: remove/restore `app/worker_kernel/` and related tests.

### Phase 4 — LangGraph assembly and integration tests

Implement `app/graph.py` with exactly three top-level runtime nodes and no conditional graph edges. Node keys must be exactly `decompressor_node`, `planner_node`, and `worker_kernel_node`. Add graph invocation tests.

Independent verification:

- `uv run pytest tests/test_graph.py -q`
- `uv run pytest -q`

The graph test should verify final state behavior and, where LangGraph exposes enough metadata without overfitting internals, the exact registered node names.

Rollback: restore `app/graph.py` and graph tests.

## Detailed sequencing

1. Add runtime dependencies `pydantic` and `langgraph`; add dev dependency `pytest`; sync lockfile.
2. Create package scaffolding and schemas.
3. Implement decompressor heuristics:
   - file hints from tokens ending in `.py` or path-like file names.
   - `what/why/how` style questions as `question`.
   - `fix network_sniffer.py` as `mutation_request`, `code.fix`, code domain, file mutation risk, verification risk.
   - `fix the app` as ambiguous with observation context needed.
4. Implement planner protocol/base and concrete planners.
5. Implement selector rules in a narrow deterministic order: direct, code, research, infra, fallback.
6. Implement worker-kernel components and default registry.
7. Implement graph with thin wrappers around singleton runtimes or a factory-compatible pattern and exact graph node keys: `decompressor_node`, `planner_node`, `worker_kernel_node`.
8. Add tests incrementally in the same order, including budget overrun-after-result and invalid plan validation cases.

## Risks and unknowns

- **Dependency availability:** The planned stack is `langgraph>=0.6.0`, `pydantic>=2.0`, `pytest>=8.0`, uv, and Python `>=3.13,<3.14`. External docs reviewed in `research/stack-requirements-docs.md` confirm this stack is sufficient, but implementation should still run `uv sync` early because Python 3.13 dependency resolution can expose transitive compatibility issues.
- **Pydantic version:** Requested schemas use `model_dump()`/`model_validate()`, implying Pydantic v2. Pin or rely on current v2 to avoid API mismatch.
- **Node naming mismatch:** The prompt names functions `decompressor_node`, `planner_node`, `worker_kernel_node`, while one sample uses shorter node keys. Resolve this by registering exact graph node keys `decompressor_node`, `planner_node`, and `worker_kernel_node`; tests should verify observable flow and may assert node names if LangGraph exposes them safely.
- **Workers are stubs in Phase 1:** Without real tool execution, repo/code/verify workers should return deterministic summaries/artifacts and usage counts. This satisfies architecture but does not yet perform real file mutation.
- **Future decompressor enrichment:** `research/brainstorm-option-1-deterministic-decompressor.md` reinforces keeping deterministic decompression as the near-term/default path and always-available fallback. If richer envelopes are needed later, `research/brainstorm-decompressor-prompt-chaining.md` recommends a hybrid staged path: structured single-pass LLM enrichment first, selective heavy prompt chaining only for ambiguous, multi-domain, low-confidence, or high-risk requests, and validated hints that planners can reject.
- **Budget accounting subtlety:** The example reserves capacity before dispatch but adds actual usage after result. Keep checks deterministic and document `before_task` as a pre-dispatch ceiling check. `after_result` must also reject if reported usage exceeds the budget, and tests must cover this overrun path.
- **Avoid contract explosion:** Do not introduce separate final response contracts, verification contracts, DAG executors, recursive replanning, or multiple graph worker/planner nodes.

## Verification commands

Use these from repository root:

```bash
uv sync
uv run python -c "from app.graph import build_graph; g = build_graph(); print(g.invoke({'user_input': 'what is docker', 'errors': []})['result']['status'])"
uv run pytest tests/test_decompressor.py -q
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

If `uv sync` is not possible due to network restrictions, first inspect whether the local environment already has required packages:

```bash
uv run python -c "import pydantic, langgraph, pytest"
```

## Recommended first implementation step

Start with Phase 1: add dependencies in `pyproject.toml`, run `uv sync`, then create `app/schemas.py` and package `__init__.py` files. This creates stable contracts before any runtime or graph code depends on them.

## Final deliverables checklist

When implementation is complete, report:

- Changed/created file tree.
- Final LangGraph flow with exact node keys: `decompressor_node → planner_node → worker_kernel_node → END`.
- The four core schemas: `Envelope`, `Plan`, `Task`, and `Result` plus nested `PlanStep`.
- Planner selection behavior.
- Worker-kernel budget gate behavior, including pre-dispatch and post-result checks.
- Test commands and results.
- Known limitations, including deterministic stub workers and no Phase 1 replanning/DAG execution.
- Near-term deterministic decompressor recommendation from `research/brainstorm-option-1-deterministic-decompressor.md` and future prompt-chaining recommendation from `research/brainstorm-decompressor-prompt-chaining.md`, if relevant.
