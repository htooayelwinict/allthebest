# Research: Input Type Specificity and Decompressor Latency

## Question

How should the LLM-only decompressor avoid generic `input_type` values such as `request` while reducing live latency, without reintroducing deterministic/static semantic Envelope generation?

## Summary

The current coalesced decompressor is directionally correct, but the prompt still suggests generic `input_type` examples and sends redundant instructions alongside the schema on every request. The smallest fix is to make `input_type` a required specific descriptor in the contract, reject only useless generic placeholders locally, and rely on the existing one repair call for correction. Latency should be reduced by shrinking prompt tokens, caching static prompt/schema objects in process, making the provider call use a compact system message, and exposing max-token/env knobs for the OpenAI-compatible client.

## Key Findings

- `app/decompressor/prompt_chain.py` currently describes `input_type` as "short descriptive string such as question, request, mutation_request, ambiguous_request", which explicitly allows the too-generic `request` output.
- The same prompt includes a large `required_output` object that duplicates the JSON Schema already supplied to the model client.
- `DecompressedEnvelope.model_json_schema()` is rebuilt on every decompressor run even though it is static for the process.
- `OpenAICompatibleJSONClient` already supports `response_format=json_schema`, but it does not expose a max-token budget and uses a verbose system message.
- Open Bridge recommended a narrow generic-placeholder validator for `input_type`, schema/Field descriptions, compact prompts with stable static content, and generation token limits.

## Recommendation

1. Add a Pydantic validator on `DecompressedEnvelope.input_type` that rejects empty values and generic placeholders such as `request`, `task`, `input`, `payload`, `data`, `object`, and `unknown`.
2. Update the field description and prompt so the LLM emits specific open-ended descriptors like `docker_concept_question`, `python_file_fix_request`, `infra_config_debug_request`, `sdk_async_performance_refactor_request`, or `ambiguous_app_fix_request`.
3. Keep this as a negative guard, not an allowed taxonomy. The model still owns the semantic label.
4. Cache `DecompressedEnvelope.model_json_schema()` at module load and reuse it for normal and repair calls.
5. Replace the verbose JSON prompt with a compact stable instruction string plus the redacted input at the end.
6. Shorten the model-client system prompt and support `DECOMPRESSOR_LLM_MAX_TOKENS` so live calls cannot over-generate.
7. Keep the one-call normal path and one repair call only after validation failure.

## Source Pointers

- `app/decompressor/contracts.py` — add `input_type` specificity validation and field descriptions.
- `app/decompressor/prompt_chain.py` — compact prompt and cached schema.
- `app/decompressor/model_client.py` — compact system prompt and max-token payload support.
- `app/decompressor/env_config.py` — wire `DECOMPRESSOR_LLM_MAX_TOKENS`.
- `tests/test_decompressor.py` — add repair coverage for generic `input_type` and config coverage for max tokens.

## Saved Path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/input-type-specificity-and-latency-20260529.md`
