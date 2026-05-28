# Phase 4 — Integration and Graph-Boundary Protection

## Goal

Verify LLM-mode envelopes remain compatible with planner selection and that the default graph stays deterministic and model-free.

## Status

Completed. Added planner integration tests for high-confidence, low-confidence, and invalid LLM planner hints, plus a graph test guard that default invocation has no prompt-chain metadata.

## Files

- Update `tests/test_planner.py`
- Update `tests/test_graph.py`
- Avoid changing `app/graph.py` unless optional injection support is strictly necessary

## Tasks

1. Test that valid high-confidence planner hints from an LLM envelope are honored by existing selector behavior.
2. Test that invalid or low-confidence hints do not override deterministic selector fallback.
3. Test that default graph invocation does not require or call a model client.
4. Reconfirm graph node keys remain `decompressor_node`, `planner_node`, and `worker_kernel_node`.

## Risks

- Overfitting tests to LangGraph internals.
- Introducing graph injection changes that are not needed for the feature.

## Rollback

Revert integration-test changes and any optional graph changes.

## Verification

```bash
uv run pytest tests/test_graph.py tests/test_planner.py -q
uv run pytest -q
```
