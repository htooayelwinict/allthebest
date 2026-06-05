# Artifact Contract And Tool Selection Research

## Question

How should the planner and worker runtime reduce artifact-name drift and weak first-pass tool choice without adding more retries or changing decompressor/planner public schemas?

## Summary

The best fix is a deterministic canonical artifact boundary plus concise prompt/tool-contract sharpening. Prompt-only enforcement is too weak, and more retries only hides the mismatch. Native tool calling is valuable later, but it does not solve planner artifact aliases by itself.

## Key Findings

- OpenAI Agents guidance emphasizes structured tool inputs, output types, guardrails, tracing, and function/tool contracts. That maps to strict artifact validation and deterministic boundary normalization, not free-form artifact IDs.
- Anthropic's tool-design guidance says effective tools need clear distinct purposes, meaningful compact observations, useful error messages, and evaluation against realistic tasks. This supports specialized tools like `write_json_manifest`, but also argues the runtime should steer aliases and contracts deterministically.
- OpenRouter supports structured outputs and tool calling, but provider support varies. Its provider routing docs point to requiring supported parameters when strict structure is necessary. That supports keeping local validation as the final authority.
- Open Bridge second-pass review favored an exact canonical alias registry over prompt-only, native-tool-only, or retry-based fixes.

## Recommendation

Implement exact alias canonicalization for known domain artifacts at planner validation/normalization and worker compilation/finalization boundaries:

- Canonicalize deterministic aliases such as `moved_item_records` -> `moved_items_record` and `manifest_update_result` -> `manifest_update_record`.
- Preserve metadata that records which aliases were normalized.
- Keep matching exact, not fuzzy, to avoid collisions.
- Expose canonical artifact names and alias examples in planner/worker prompts.
- Make domain artifact synthesis alias-aware so close aliases cannot bypass strict contracts.

## References

- Context7: `/openai/openai-agents-python`, tool contracts and guardrails.
- Context7: `/websites/platform_claude_en_api`, tool-use loop and repair observations.
- Context7: `/websites/openrouter_ai`, tool calling, structured outputs, provider routing.
- Web: OpenAI Agents SDK tools and tool guardrails.
- Web: Anthropic "Writing effective tools for AI agents".
- Web: OpenRouter structured outputs and provider routing.

