# Brainstorm: Option 1 — Keep Deterministic Decompressor

Date: 2026-05-29

## Problem statement

The active LangGraph runtime architecture keeps the top-level flow simple:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

The current `DecompressorRuntime` is deterministic and heuristic-driven. It already emits a richer validated `Envelope` with fields such as `user_goal`, `execution_hints`, `planner_hint`, `planner_confidence`, `ambiguity`, and `assumptions`.

The question for Option 1 is whether to keep this deterministic decompressor as the near-term architecture instead of moving immediately to one-shot LLM enrichment or multi-step prompt chaining.

## Options considered

### Option 1 — Keep deterministic heuristic decompressor

Keep the decompressor as Python rules plus Pydantic validation. Continue using deterministic parsing for request normalization, artifact hints, input type, intents, domains, risks, context needs, budget hints, planner hints, ambiguity, and assumptions.

**Strengths**

- Fast and cheap: no model call before planning.
- Easy to test with stable fixture assertions.
- Safer schema boundary: `Envelope` remains predictable and Pydantic-validated.
- Easier debugging: each classification comes from inspectable rules instead of opaque model behavior.
- Good fit for Phase 1, where the goal is stable runtime boundaries rather than maximum semantic understanding.

**Weaknesses**

- Limited understanding of informal, multi-step, or unusually phrased requests.
- Rule maintenance can become brittle as domains and intents expand.
- Planner hints may be shallow when user intent depends on context not visible in simple text patterns.
- Ambiguity handling is conservative and may route too many requests to fallback/observe-first behavior.

### Option 2 — One structured LLM pass before planning

Use one model call to enrich the same `Envelope` fields, then validate and sanitize before `PlannerRuntime` sees the result.

**Why not now**

- Adds latency, provider failure modes, prompt drift, and test complexity.
- Still needs deterministic fallback and allowed-label validation.
- Best introduced later behind a feature flag or shadow mode, not as a Phase 1 replacement.

### Option 3 — Full internal prompt chain

Use multiple decompressor prompts for normalization, artifact extraction, classification, risk/context inference, planner recommendation, envelope assembly, and validation.

**Why not now**

- Highest cost and latency.
- More failure points and more intermediate contracts to test.
- Better reserved for complex, ambiguous, multi-domain, or high-risk requests after simpler enrichment proves valuable.

## Recommended path

Use Option 1 as the near-term/default path.

The deterministic decompressor is the right baseline for the current phase because it preserves the architecture contract and gives downstream components stable inputs. It should remain the always-available fallback even if later phases add LLM enrichment.

Recommended guardrails for keeping Option 1 strong:

1. Treat deterministic output as the contract baseline.
2. Keep `Envelope` flat and planner-oriented; avoid fields that imply worker steps, tool calls, dispatch, or budget enforcement.
3. Add new rules only when they map to clear planner behavior.
4. Track low-confidence and ambiguous cases explicitly through `ambiguity`, `assumptions`, and `execution_hints`.
5. If semantic quality becomes a bottleneck, add LLM enrichment later as shadow/optional mode, not as a hard replacement.

Open Bridge second-opinion synthesis agreed that Option 1 is strongest when latency, schema reliability, security, and reproducible auditing matter most. It also warned that heuristic parsing can become a maintenance bottleneck and recommended threshold-based hybrid routing only after deterministic confidence gaps become measurable.

## Risks and mitigations

### Risk: Heuristics misclassify nuanced input

**Mitigation:** Prefer conservative outputs for uncertain requests: lower confidence, add ambiguity, require observe-first, and route to fallback planner when needed.

### Risk: Rule growth becomes hard to maintain

**Mitigation:** Keep rules narrow and aligned to accepted planner/domain labels. Avoid adding one-off text patterns unless they unlock concrete planner behavior or test coverage.

### Risk: Planner over-trusts shallow hints

**Mitigation:** Continue treating `planner_hint` as advisory. Require a confidence threshold and registry validation, then fall back to deterministic selector rules.

### Risk: Future LLM enrichment breaks stable contracts

**Mitigation:** Keep Pydantic validation and allowed-label checks as the permanent boundary. Any LLM path should emit the same `Envelope` schema and fall back to deterministic output on invalid, low-confidence, or timed-out responses.

### Risk: Ambiguous requests produce poor downstream plans

**Mitigation:** Preserve `observe_first_required` and `do_not_patch_before_observation` style hints. For vague mutation requests, the correct behavior is observation or clarification, not immediate patching.

## Saved path

`plan/langgraph-runtime-architecture-20260528-233454/research/brainstorm-option-1-deterministic-decompressor.md`
