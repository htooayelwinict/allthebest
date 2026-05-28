# Stack Requirements and Documentation Research

## Research question

For `plan/langgraph-runtime-architecture-20260528-233454/`, are any additional technical documentation sources or stack requirements needed before implementing the planned Phase 1 LangGraph runtime architecture?

## Summary

The plan's current stack choices are sufficient for the requested Phase 1 implementation if the implementation stays within the documented APIs for LangGraph `StateGraph`, Pydantic v2 models, uv dependency groups, and pytest. The plan does not require additional runtime frameworks or decompression libraries beyond the already declared `langgraph` and `pydantic` dependencies because the planned `DecompressorRuntime` is a deterministic request-classification component, not a binary compression/decompression engine.

The main documentation needed during implementation is narrow API confirmation for LangGraph graph assembly and Pydantic v2 serialization/validation. Those docs were consulted through enabled Context7 MCP sources. A second-opinion synthesis was also requested through the enabled OpenRouter-backed open-bridge MCP; it identified useful risks, but one dependency-layout concern did not apply to the current repository because `pyproject.toml` already places `langgraph` and `pydantic` under `[project].dependencies` and only `pytest` under `[dependency-groups].dev`.

## Key findings

### 1. LangGraph API documentation is needed and sufficient

Official LangGraph documentation for version `0.6.0` confirms the planned graph shape is compatible with the Python API:

- Import pattern: `from langgraph.graph import StateGraph, START, END`.
- Create a state graph with a state schema, commonly a `TypedDict`: `StateGraph(State)`.
- Register nodes with either function inference or explicit names, including `builder.add_node("double", double)`.
- Define a linear flow with `add_edge(START, "node")`, intermediate `add_edge(...)`, and `add_edge("last", END)`, or use `set_entry_point("node")` for the start.
- Compile and run with `graph = builder.compile()` and `graph.invoke({...})`.

Implication for this plan: `app/graph.py` should be able to use a simple `StateGraph(RuntimeState)` with exactly the required node keys:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

The plan should prefer explicit `add_node("decompressor_node", decompressor_node)` registration because the acceptance criteria require exact node keys.

### 2. Pydantic v2 documentation matches the schema plan

Official Pydantic documentation confirms the v2 APIs assumed by the plan:

- Models inherit from `pydantic.BaseModel`.
- `Model.model_validate({...})` validates dictionary-like input.
- `model.model_dump()` serializes model instances to dictionaries.

Implication for this plan: the dependency requirement `pydantic>=2.0` is appropriate for `Envelope`, `PlanStep`, `Plan`, `Task`, and `Result` schemas that are serialized into `RuntimeState`.

### 3. uv dependency layout is already aligned

uv documentation confirms:

- Runtime dependencies belong in `[project].dependencies`.
- Development-only dependencies can be declared under `[dependency-groups]`, commonly `dev`.
- `[tool.uv] package = false` is valid for a virtual/non-packaged project.

Current `pyproject.toml` is already aligned:

```toml
[project]
dependencies = [
  "langgraph>=0.6.0",
  "pydantic>=2.0",
]

[dependency-groups]
dev = [
  "pytest>=8.0",
]

[tool.uv]
package = false
```

Implication for this plan: no dependency-layout correction is needed before implementation. The earlier plan text that says to add these dependencies is already satisfied in the current repository state, though `uv.lock` should still be checked/updated by the implementer if it is stale.

### 4. pytest is sufficient for planned verification

The plan's test requirements are unit/integration tests over deterministic runtime classes and a compiled LangGraph invocation. No browser, database, service container, or end-to-end framework is needed for Phase 1.

Implication for this plan: `pytest>=8.0` in the dev dependency group is enough unless the implementation later introduces async graph tests or extra test helpers. The plan currently does not require them.

### 5. Python 3.13 remains the main stack risk

The project requires Python `>=3.13,<3.14`. LangGraph and its transitive dependencies may resolve successfully, but dependency resolution should be verified early because Python 3.13 compatibility can expose wheel or transitive-version issues.

Implication for this plan: implementation should start with dependency verification from the repository root:

```bash
uv sync
uv run python -c "import pydantic, langgraph, pytest"
```

If dependency resolution fails, the blocker is environmental/stack compatibility rather than an architecture issue in the plan.

### 6. No extra decompression package is needed for Phase 1

Although the term `DecompressorRuntime` could imply binary compression support, the plan defines it as a deterministic request decompressor/classifier that consumes `str` and emits `Envelope`.

Implication for this plan: do not add `gzip`, `brotli`, `zstandard`, or similar dependencies unless the product requirement changes to actual compressed payload decoding. Standard-library modules are enough if trivial string parsing helpers are needed.

### 7. State merge behavior should stay simple

LangGraph can use reducers for complex state accumulation, but this plan's `RuntimeState` is a simple linear pipeline where each node writes distinct serialized keys: `envelope`, `plan`, `result`, and `errors`.

Implication for this plan: no reducer documentation or advanced state-management stack is required if nodes return partial state updates that preserve existing keys through LangGraph's normal state update behavior. If implementation changes `errors` into an accumulating list updated by multiple nodes, reducer documentation may become useful.

## Recommendation

Proceed with the existing stack requirements:

- Runtime: `langgraph>=0.6.0`, `pydantic>=2.0`.
- Dev/test: `pytest>=8.0`.
- Tooling: uv with `[tool.uv] package = false`.
- Python: keep current `>=3.13,<3.14`, but verify dependency resolution before building runtime code.

Do not add additional technical documentation or dependencies before implementation. During implementation, keep the documentation lookup focused on:

1. LangGraph `StateGraph` node registration, edges, `END`, compile, and invoke behavior.
2. Pydantic v2 `model_validate()` and `model_dump()` behavior.
3. uv lock/sync behavior if dependency resolution differs from the current `pyproject.toml`.

## References and source pointers

- Local plan: `plan/langgraph-runtime-architecture-20260528-233454/plan.md`.
- Local requirements: `plan/langgraph-runtime-architecture-20260528-233454/research/requirements.md`.
- Local project metadata: `pyproject.toml`.
- Context7: `/langchain-ai/langgraph/0.6.0`, query on Python `StateGraph`, `START`, `END`, `add_node`, `add_edge`, `compile`, and `invoke`.
- Context7: `/pydantic/pydantic`, query on Pydantic v2 `BaseModel`, `model_validate`, and `model_dump`.
- Context7: `/astral-sh/uv`, query on uv `[project].dependencies`, `[dependency-groups]`, `uv sync`, `uv lock`, and `[tool.uv] package = false`.
- Open-bridge second-opinion synthesis: checked for additional stack/documentation gaps and risks; useful findings incorporated where applicable.

## Saved path

`plan/langgraph-runtime-architecture-20260528-233454/research/stack-requirements-docs.md`
