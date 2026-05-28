# Research: `plan/suggest.md` Prompt-Chain Decompressor Design

## Question

What design changes does `plan/suggest.md` propose for the Phase 1 decompressor runtime, and how should those ideas be preserved for future planning without changing the current implementation immediately?

## Summary

`plan/suggest.md` proposes making `DecompressorRuntime` a stronger understanding layer by replacing a purely deterministic classifier with an internal prompt chain. The top-level LangGraph architecture remains unchanged:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

The suggested change is internal to the decompressor: use multiple focused prompts to normalize input, extract artifacts, classify intent/domain/risk, infer context requirements, recommend a planner, assign a budget hint, assemble an expanded `Envelope`, and validate it with Pydantic.

The idea is valuable for a future phase, but it should be treated as an architecture evolution, not a Phase 1 implementation requirement. The current Phase 1 implementation should remain deterministic unless a new plan explicitly expands the `Envelope` schema and adds an LLM adapter, validation, fallback behavior, and tests.

## Key Findings

### 1. Top-Level Graph Stays The Same

The suggested architecture keeps LangGraph simple and stable:

```text
User Input
  -> DecompressorRuntime
  -> PlannerRuntime
  -> WorkerKernelRuntime
  -> Result
```

The decompressor can become more powerful internally without adding top-level graph nodes or graph branches.

### 2. Suggested Decompressor Chain

The proposed decompressor pipeline is:

1. Normalize request.
2. Extract artifacts.
3. Classify intent, domain, and risk.
4. Infer context requirements.
5. Recommend planner.
6. Assign budget hint.
7. Assemble `Envelope`.
8. Validate `Envelope`.

`plan/suggest.md` argues this is better than a single giant prompt because each step has a narrower responsibility and can be inspected or validated independently.

### 3. Expanded Envelope Fields Proposed

The suggested `Envelope` adds fields while keeping a single object boundary:

```python
user_goal: str | None = None
execution_hints: list[str] = Field(default_factory=list)
planner_hint: str | None = None
planner_confidence: float = 0.0
planner_alternatives: list[str] = Field(default_factory=list)
ambiguity: list[str] = Field(default_factory=list)
assumptions: list[str] = Field(default_factory=list)
```

These fields are meant to make `PlannerRuntime` easier by giving it richer context, not by transferring planning authority into the decompressor.

### 4. Decompressor Authority Boundary

The decompressor may own:

- normalization
- classification
- artifact hints
- risk hints
- context requirements
- planner recommendation
- budget hint
- ambiguity detection

The decompressor must not own:

- worker steps
- tool choices
- task dispatch
- budget enforcement
- file mutation
- retry decisions

This preserves the core boundary: decompressor understands, planner plans, worker-kernel executes.

### 5. Planner Selector Can Use Hints Safely

The suggested selector behavior is:

```python
if envelope.planner_hint and envelope.planner_confidence >= 0.70:
    return registry.get(envelope.planner_hint)
```

Then it falls back to deterministic checks over intents, domains, and input type. This is a useful future design if planner hints are validated against an allowed planner registry.

### 6. Deterministic Guardrails Still Matter

Even with an LLM-heavy decompressor, deterministic code should still control:

- request ID creation
- empty input handling
- Pydantic validation
- allowed-label checking
- schema repair or rejection
- confidence thresholds
- fallback if the model fails

The correct pattern is: LLM proposes, validator accepts or rejects. The planner should not adapt to arbitrary malformed model output.

## Relationship To Current Implementation

The current implementation is intentionally deterministic and matches the Phase 1 contract. `plan/suggest.md` is best treated as a future enhancement proposal because it would require schema expansion, model/provider boundaries, validation rules, and new tests.

The previous brainstorm note reaches a compatible recommendation: adopt a hybrid staged approach instead of directly switching to heavy prompt chaining.

## Recommendation

Create a future implementation plan for a **hybrid prompt-chain decompressor** rather than modifying Phase 1 directly.

Recommended future path:

1. Keep deterministic decompressor as baseline and fallback.
2. Expand `Envelope` only with fields planners will actually use.
3. Add allowed-label registries for input types, intents, domains, risks, execution hints, budget hints, and planner names.
4. Add an internal LLM adapter boundary for decompressor enrichment.
5. Start with shadow mode or a single structured LLM pass.
6. Add multi-step prompt chaining only for ambiguous, multi-domain, low-confidence, or high-risk inputs.
7. Add fixture-based tests with canned LLM responses; do not call live models in unit tests.

## References

- Source proposal: `plan/suggest.md`
- Active architecture plan: `plan/langgraph-runtime-architecture-20260528-233454/plan.md`
- Related brainstorm: `plan/langgraph-runtime-architecture-20260528-233454/research/brainstorm-decompressor-prompt-chaining.md`

## Saved Path

`plan/langgraph-runtime-architecture-20260528-233454/research/suggest-prompt-chain-decompressor.md`
