# Phase 2 - Decomposer Runtime

## Goal

Port the V1 decompressor idea into `appV2.decomposer.DecomposerRuntime` and improve prompt chaining with gated stages.

## Files

- `appV2/decomposer/__init__.py`
- `appV2/decomposer/contracts.py`
- `appV2/decomposer/prompt_chain.py`
- `appV2/decomposer/runtime.py`
- `appV2/decomposer/redaction.py`
- `appV2/decomposer/canonicalize.py`
- `tests/test_appv2_decomposer.py`

## Tasks

- Implement `DecomposerRuntime.from_env`.
- Implement request IDs, metrics, trace rows, and schema validation.
- Add gated prompt-chain stages:
  - `decompose_request`
  - deterministic literal extraction
  - optional `enrich_file_code_contracts`
  - `repair_envelope`
- Canonicalize exact paths, keys, symbols, and generated-placeholder removal.
- Ensure decomposer never produces phase plans, tool names, budgets, or worker concepts.

## Tests

```bash
uv run pytest tests/test_appv2_decomposer.py -q
```

## Done When

- Simple prompts complete in one model call under fake client tests.
- Complex file/code prompts run the enrichment path.
- Invalid envelope output gets exactly one repair attempt.
- Envelope metadata records stages and model-call count.

## Status

Completed 2026-06-05.
