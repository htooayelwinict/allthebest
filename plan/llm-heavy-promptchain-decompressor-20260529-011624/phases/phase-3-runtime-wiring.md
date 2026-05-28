# Phase 3 — Runtime Wiring

## Goal

Wire optional LLM prompt-chain mode into `DecompressorRuntime` while keeping no-argument behavior unchanged.

## Status

Completed. `DecompressorRuntime()` remains deterministic, while `DecompressorRuntime(model_client=...)` explicitly enables the internal prompt chain with a deterministic fallback callback that avoids recursion.

## Files

- Update `app/decompressor/runtime.py`
- Update `tests/test_decompressor.py`
- Update `tests/test_planner.py` if planner integration coverage is added here

## Tasks

1. Add backward-compatible constructor parameters such as `model_client=None` or `prompt_chain=None`.
2. Preserve deterministic methods and no-argument `run(...)` behavior.
3. Route to prompt-chain mode only when explicitly configured.
4. Ensure deterministic fallback can call existing stage methods or a deterministic runtime path without recursion mistakes.
5. Add tests proving default deterministic behavior remains unchanged.

## Risks

- Recursion bugs if prompt-chain fallback calls `run(...)` on the same LLM-configured runtime.
- Request ID sequencing changes that break assumptions.

## Rollback

Revert constructor and routing changes in `runtime.py`.

## Verification

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py -q
```
