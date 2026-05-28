# Requirements Research

## Source request

Create a structured implementation plan for `plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md` without coding.

## Restated goal

Plan an optional LLM-heavy prompt-chain implementation inside the decompressor runtime while preserving the existing graph topology and runtime boundaries.

## Functional requirements

- Keep public runtime contract: `DecompressorRuntime.run(user_input: str) -> Envelope`.
- Keep graph topology unchanged: `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- Use internal staged prompt-chain boundaries:
  1. normalize request
  2. extract artifacts
  3. classify request
  4. infer context and risk
  5. recommend planner
  6. assemble envelope
  7. validate or repair/fallback
- Use small internal Pydantic models for stage outputs.
- Use a minimal injectable model-client protocol rather than coupling to a provider SDK.
- Validate model output with Pydantic v2 APIs.
- Keep deterministic runtime behavior as default and fallback.
- Enforce allowed labels before downstream planner code consumes the envelope.
- Redact common secrets before sending prompt inputs to a model provider.
- Avoid storing raw prompts, full responses, credentials, or large file contents in metadata.

## Test requirements

- Existing deterministic tests must continue to pass.
- Unit tests must use fake/canned model clients only.
- Add tests for:
  - valid staged responses produce expected envelope
  - invalid JSON fallback
  - invalid labels are clamped/rejected
  - unknown planner hints do not force selector choice
  - low planner confidence does not force selector choice
  - vague mutation keeps observe-first hints
  - default graph does not invoke a model client
  - no-argument runtime remains deterministic
  - prompt inputs redact common secret patterns
  - prompt injection does not bypass schema/label validation

## Non-requirements

- Do not add prompt-chain stages as LangGraph nodes.
- Do not give the decompressor authority over worker steps, tool choices, dispatch, budget enforcement, file mutation, or retry decisions.
- Do not add live model provider dependencies as part of the minimal first implementation.
- Do not require external model configuration for existing tests or default graph use.
