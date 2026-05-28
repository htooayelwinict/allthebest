# Phase 1 — Internal Contracts, Labels, Redaction, and Fake-Client Scaffolding

## Goal

Create safe internal boundaries for future LLM prompt-chain execution without changing default decompressor behavior.

## Status

Completed. Added internal Pydantic stage contracts, the provider-agnostic model-client protocol, allowed-label helpers, redaction helpers, and fake-client test scaffolding.

## Files

- Add `app/decompressor/contracts.py`
- Add `app/decompressor/labels.py`
- Add `app/decompressor/redaction.py`
- Update `tests/test_decompressor.py`

## Tasks

1. Define internal Pydantic models:
   - `NormalizedRequest`
   - `ArtifactExtraction`
   - `RequestClassification`
   - `RiskContextInference`
   - `PlannerRecommendation`
2. Define `PromptChainModelClient` protocol.
3. Move or duplicate allowed-label constants into a focused helper module.
4. Add deterministic helpers to clamp/drop invalid labels.
5. Add redaction helper for common secret-like patterns.
6. Add fake-client scaffolding in tests or test helpers.

## Risks

- Over-exporting internal types as public API.
- Divergence between runtime labels and new label constants.

## Rollback

Remove the new modules and test additions; deterministic runtime remains untouched.

## Verification

```bash
uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"
uv run pytest tests/test_decompressor.py -q
```
