# Brainstorm: Live Prompt-Chain Schema Compliance Failure

Date: 2026-05-29

## Problem statement

The live LLM decompressor smoke test reached the configured provider, but the prompt chain fell back before completing the first stage.

Observed safe summary:

```text
DecompressorRuntime.from_env().run("fix network_sniffer.py")
mode = deterministic_fallback
chain.mode = fallback
chain.completed_stages = []
chain.error_type = PromptChainError
```

A direct provider diagnostic for the `normalize_request` stage succeeded at the HTTP/provider level, but returned JSON that did not match the expected `NormalizedRequest` schema:

```json
{
  "intent": "fix_code",
  "file": "network_sniffer.py",
  "language": "python"
}
```

The expected first-stage shape is closer to:

```json
{
  "normalized_input": "Fix network_sniffer.py.",
  "user_goal": "Repair the target Python file.",
  "ambiguity": [],
  "assumptions": []
}
```

So the current issue is not credentials or network connectivity. It is live model/provider schema noncompliance.

## Options considered

### Option 1: Strengthen per-stage prompts

Make each prompt more explicit about the exact required keys, no extra keys, and stage-specific examples.

Pros:

- Lowest implementation risk.
- Keeps provider/client architecture unchanged.
- Directly addresses schema misunderstanding.

Cons:

- Some providers still ignore JSON schema guidance.
- Prompt-only fixes can be brittle without eval coverage.

### Option 2: Add schema repair retry

When `model_validate_json()` fails, call the model once more with the validation error and the expected schema, asking it to repair the previous response.

Pros:

- Handles providers that are close but imperfect.
- Preserves strict final validation.

Cons:

- Adds latency/cost.
- Needs a strict retry limit and sanitized error metadata.

### Option 3: Use provider-native strict structured output if available

Adjust `OpenAICompatibleJSONClient` options so providers that support strict JSON schema enforcement use it.

Pros:

- Best compliance when supported.
- Reduces reliance on prompt wording.

Cons:

- Provider support varies.
- Some OpenAI-compatible endpoints ignore or partially implement `response_format`.
- Strict schemas may reject flexible fields like `dict[str, Any]` artifacts depending on provider.

### Option 4: Add a stage response normalizer before validation

Map common wrong response shapes into the expected stage shape. For example, map `file` into an artifact only for the artifact stage, or reject it for the normalizer stage.

Pros:

- Can salvage common mistakes.
- Reduces fallback rate.

Cons:

- Dangerous if it teaches the runtime to accept arbitrary model output.
- Can blur stage boundaries and recreate the “planner adapts to bad LLM output” problem.

### Option 5: Build a live eval harness before changing prompts

Run a small corpus through the live provider, collect only sanitized stage status/errors, and use failures to refine prompts/schema.

Pros:

- Avoids guessing.
- Produces a repeatable benchmark for prompt changes.

Cons:

- Requires live provider calls and cost.
- Must avoid logging raw secrets or full prompts.

## Recommended path

Use a narrow sequence:

1. **Strengthen per-stage prompts** with exact required keys, no extra keys, and one compact positive example per stage.
2. **Add one schema repair retry** for invalid JSON/schema output, still ending in Pydantic validation and deterministic fallback if repair fails.
3. **Add a small live eval harness** that records only sanitized status: stage name, success/failure, validation error type, fallback mode, latency, and redaction boolean.

Do not implement broad response-shape normalization as the first fix. It risks weakening the schema boundary by making the runtime adapt to arbitrary model output.

## Risks and mitigations

### Risk: Prompt improvements still fail with the current provider

Mitigation: keep deterministic fallback as authoritative, and expose provider/response-format tuning as configuration.

### Risk: Repair retry increases latency and cost

Mitigation: allow at most one repair retry per failed stage, and skip repair for provider/network exceptions.

### Risk: Validation errors leak sensitive content

Mitigation: validation metadata should store error type and stage only. Do not persist raw provider response or raw prompt.

### Risk: Live eval leaks prompt content

Mitigation: store only aggregate/sanitized diagnostics. Do not write raw prompt, model response, API keys, or user secrets to disk.

## Saved path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/brainstorm-live-schema-compliance-20260529.md`
