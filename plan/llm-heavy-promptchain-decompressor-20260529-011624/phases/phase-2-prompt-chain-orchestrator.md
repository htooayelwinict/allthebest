# Phase 2 — Prompt-Chain Orchestrator

## Goal

Implement the internal LLM prompt-chain orchestrator with staged validation, allowed-label clamping, envelope assembly, and deterministic fallback.

## Status

Completed. Added the prompt-chain orchestrator with staged `complete_json(...)` calls, Pydantic validation, label clamping, sanitized diagnostics, prompt redaction, and whole-chain deterministic fallback.

## Files

- Add `app/decompressor/prompt_chain.py`
- Update `tests/test_decompressor.py`

## Tasks

1. Implement a chain component that accepts a `PromptChainModelClient` and deterministic fallback callable/runtime.
2. Build minimal prompts per stage using redacted raw input and validated prior outputs.
3. Call `complete_json(...)` with stage name, prompt text, and `model_json_schema()`.
4. Validate responses with `model_validate_json(...)`.
5. Clamp labels after validation.
6. Assemble an `Envelope` with runtime-owned `request_id` and original raw input.
7. Store only sanitized diagnostics in `Envelope.metadata`.
8. Fall back deterministically when model calls or validation fail.

## Risks

- Accidentally storing raw model responses or prompts in metadata.
- Making fallback too complex before basic whole-chain fallback works.

## Rollback

Remove `prompt_chain.py` and LLM-mode tests.

## Verification

```bash
uv run pytest tests/test_decompressor.py -q
```
