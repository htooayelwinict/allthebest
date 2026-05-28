# Option 1: Deterministic Decompressor Next Step

## Purpose

Capture the next-step recommendation for Option 1 as a standalone file, without changing existing plan index or reference files.

## Recommendation

Keep the deterministic decompressor as the near-term/default runtime path.

The current decompressor already uses prompt-chain-style internal stages while remaining deterministic:

1. Normalize request.
2. Extract artifacts.
3. Classify intent/domain.
4. Infer risk/context.
5. Recommend planner.
6. Assemble and validate `Envelope`.

This is the safest next step because it preserves stable tests, predictable schema output, low latency, and deterministic fallback behavior.

## Why Not LLM Integration Yet

LLM enrichment should not replace the deterministic path immediately because it adds:

- provider/runtime failure modes
- latency and cost
- nondeterministic outputs
- prompt drift
- extra validation and fallback requirements
- harder unit testing

## Future Path

If semantic quality becomes a measurable bottleneck, add LLM enrichment later as an optional mode:

1. Keep deterministic decompression as baseline and fallback.
2. Add an LLM adapter boundary.
3. Use shadow mode first.
4. Validate model output with the same Pydantic `Envelope` schema.
5. Clamp labels to allowed values.
6. Promote only after canned-response tests and metrics prove value.

## Guardrails

- `planner_hint` remains advisory.
- `planner_confidence` must meet a threshold before selector trust.
- Decompressor may emit understanding hints, but must not create steps, choose workers, dispatch tools, mutate files, or enforce budgets.
- Ambiguous mutation requests should produce observe-first hints, not immediate patch behavior.

## Related Existing Research

- `brainstorm-option-1-deterministic-decompressor.md`
- `brainstorm-decompressor-prompt-chaining.md`
- `suggest-prompt-chain-decompressor.md`

## Saved Path

`plan/langgraph-runtime-architecture-20260528-233454/research/option-1-deterministic-decompressor-next-step.md`
