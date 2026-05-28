# Research: Deterministic Decompressor Contract Next Step

## Question

After the existing recommendation in `option-1-deterministic-decompressor-next-step.md` to keep the deterministic decompressor as the near-term/default runtime path, what is the next actionable research/implementation step for the active LangGraph runtime plan without changing that existing note?

## Summary

The next step is to lock down the deterministic decompressor as a contract boundary before adding any LLM enrichment, shadow mode, prompt-chain implementation, or dynamic graph behavior.

For Phase 1, the decompressor should remain a pure, deterministic `str -> Envelope` runtime. The important follow-up is not another architecture expansion; it is to make the boundary testable and difficult to violate: define/confirm the `Envelope` fields, enforce Pydantic validation, keep node state serialization predictable, and add focused tests for direct questions, targeted code-fix requests, and vague mutation requests.

This preserves the strict flow:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

while giving later agents a safe baseline for any future optional LLM enrichment.

## Key findings

### 1. Treat the decompressor as a schema gate, not an execution planner

The decompressor's output should be a validated `Envelope` and nothing more. It may normalize the user request, extract mentioned artifacts, classify intent/domain/risk, infer context needs, and provide advisory planner hints. It must not create `PlanStep` objects, choose workers, dispatch tools, mutate files, enforce budgets, or add retry behavior.

This directly supports the active plan acceptance criteria that `DecompressorRuntime` consumes `str` and emits `Envelope`, while `PlannerRuntime` owns `Envelope -> Plan` and `WorkerKernelRuntime` owns `Plan -> Task/Result` execution.

### 2. The immediate implementation target is deterministic contract coverage

The most useful next work is to convert the recommendation into concrete boundary tests and schema assertions:

- Direct question, e.g. `what is docker`, should produce a safe question-style envelope with no mutation assumptions.
- Targeted code-fix request, e.g. `fix network_sniffer.py`, should identify the artifact and code-fix/mutation risk while still only emitting hints.
- Vague mutation request, e.g. `fix the app`, should be marked ambiguous and should prefer observe-first / do-not-patch-before-observation style hints.
- Empty or malformed input should still produce a controlled validation outcome or safe fallback path rather than leaking invalid state downstream.

These tests should verify the decompressor boundary before planner selection or worker dispatch is involved.

### 3. Planner hints must remain advisory and thresholded

`planner_hint` and `planner_confidence` are useful only if the planner continues to validate them against known planner names and a confidence threshold. The decompressor can recommend; it should not select. If the hint is missing, invalid, or low-confidence, `PlannerRuntime` should fall back to deterministic selector rules over envelope fields.

### 4. Keep state serialization simple and explicit

The graph state should only carry the simple fields already called out by the plan: `user_input`, serialized `envelope`, serialized `plan`, serialized `result`, and `errors`. This avoids accidental coupling between graph internals and runtime classes, and it makes failures easier to inspect.

### 5. Do not start LLM work yet

Future LLM enrichment remains a valid path, but not the next Phase 1 step. It should wait until there is a deterministic baseline with tests and measurable gaps. When it happens later, it should be introduced through an adapter boundary, run in shadow/optional mode first, validate against the same Pydantic `Envelope`, clamp labels to allowed values, and use canned-response tests rather than live model calls in unit tests.

## Second-opinion synthesis

Open Bridge was used for a focused second-opinion synthesis because the question is a small architecture next-step decision. The useful retained guidance was:

- make the node-to-node schema transition the immediate implementation focus;
- treat the deterministic decompressor as the baseline metrics source;
- prefer safe observe-first envelopes for unresolved ambiguity;
- avoid LLM APIs, shadow modes, or dynamic routing in this sprint.

One adjustment: the active plan's `RuntimeState` is already specified as a simple state with serialized `envelope`, `plan`, and `result`; this note keeps that plan-specific shape rather than introducing a separate central state class beyond what the plan requires.

## Recommendation

Proceed with a narrow Phase 1 decompressor contract hardening step:

1. Implement or confirm `Envelope` schema fields needed by the deterministic decompressor.
2. Implement `DecompressorRuntime` as deterministic staged logic only.
3. Add decompressor tests for direct question, targeted code fix, vague mutation, empty input/safe fallback, allowed labels, and planner hint confidence behavior.
4. Ensure `decompressor_node` is only a thin graph wrapper that serializes the validated envelope into `RuntimeState`.
5. Defer all LLM enrichment, prompt-chain calls, provider config, shadow mode, and dynamic top-level graph routing until after the deterministic baseline is implemented and verified.

## Risks and mitigations

- **Risk: boundary leakage** — decompressor output starts to look like plan steps or worker directives.  
  **Mitigation:** tests should assert no plan/task/worker fields are emitted by decompressor logic.

- **Risk: over-trusted planner hints** — planner treats decompressor hints as commands.  
  **Mitigation:** planner validates hint name and confidence threshold, then falls back to deterministic selector rules.

- **Risk: ambiguous mutation requests become unsafe edits** — vague requests route to patching too early.  
  **Mitigation:** decompressor emits ambiguity and observe-first hints; downstream planner should produce observation/clarification-style plans.

- **Risk: schema drift** — future enrichment adds fields without planner use.  
  **Mitigation:** add only fields with a clear planner behavior and keep diagnostic traces in metadata/logs, not required contract fields.

## References

- Existing next-step note: `plan/langgraph-runtime-architecture-20260528-233454/research/option-1-deterministic-decompressor-next-step.md`
- Active plan: `plan/langgraph-runtime-architecture-20260528-233454/plan.md`
- Requirements research: `plan/langgraph-runtime-architecture-20260528-233454/research/requirements.md`
- Related brainstorm: `plan/langgraph-runtime-architecture-20260528-233454/research/brainstorm-option-1-deterministic-decompressor.md`
- Prompt-chain synthesis: `plan/langgraph-runtime-architecture-20260528-233454/research/suggest-prompt-chain-decompressor.md`
- Hybrid prompt-chain brainstorm: `plan/langgraph-runtime-architecture-20260528-233454/research/brainstorm-decompressor-prompt-chaining.md`
- MCP second opinion: Open Bridge synthesis performed on 2026-05-29 with focused context from the existing note and active plan criteria.

## Saved path

`plan/langgraph-runtime-architecture-20260528-233454/research/deterministic-decompressor-contract-next-step-20260529-010337.md`
