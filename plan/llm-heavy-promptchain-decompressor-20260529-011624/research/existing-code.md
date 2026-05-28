# Existing Code Research

## Files reviewed

- `plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md`
- `app/decompressor/runtime.py`
- `app/schemas.py`
- `app/graph.py`
- `app/planner/runtime.py`
- `app/planner/selector.py`
- `tests/test_decompressor.py`
- `tests/test_planner.py`
- `tests/test_graph.py`
- `pyproject.toml`
- `plan/langgraph-runtime-architecture-20260528-233454/plan.md`
- `plan/langgraph-runtime-architecture-20260528-233454/research/suggest-prompt-chain-decompressor.md`

## Current decompressor shape

`app/decompressor/runtime.py` is deterministic but already organized as a prompt-chain-like sequence:

```text
_normalize_request -> _extract_artifacts -> _classify_request -> _infer_context_and_risk -> _recommend_planner -> _validate_envelope
```

This provides a direct seam for staged LLM outputs while keeping deterministic methods as fallback.

## Envelope readiness

`app/schemas.py` already includes fields needed for a richer decompressor:

- `raw_input`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `domains`
- `risks`
- `artifacts`
- `context_needed`
- `execution_hints`
- `planner_hint`
- `planner_confidence`
- `planner_alternatives`
- `budget_hint`
- `confidence`
- `ambiguity`
- `assumptions`
- `metadata`

No immediate schema expansion is required.

## Planner selector behavior

`app/planner/selector.py` only honors `envelope.planner_hint` when confidence is at least `0.70` and the hint exists in the registry. This makes LLM planner recommendations advisory and safe to reject.

## Graph constraints

`app/graph.py` uses exactly three top-level nodes:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

The decompressor node calls `_decompressor_runtime.run(...)` and serializes the envelope. The prompt chain should remain internal to `DecompressorRuntime`.

## Existing tests

`tests/test_decompressor.py` currently asserts deterministic classifications for:

- direct question: `what is docker`
- file fix: `fix network_sniffer.py`
- vague fix: `fix the app`
- infra artifacts: `docker-compose.yml` / `nginx.conf`

`tests/test_planner.py` asserts planner behavior based on decompressor outputs.

`tests/test_graph.py` asserts compiled graph invocation and node keys.

## Dependency constraints

`pyproject.toml` already includes:

- Python `>=3.13,<3.14`
- `langgraph>=0.6.0`
- `pydantic>=2.0`
- dev `pytest>=8.0`

No provider SDK is necessary for the first implementation because the decompressor can depend on a small protocol.
