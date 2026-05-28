# Brainstorm: Decompressor Prompt Chaining for Richer Envelopes

Date: 2026-05-29

## Problem statement

The current Phase 1 runtime keeps the top-level graph simple: `decompressor_node → planner_node → worker_kernel_node → END`. The implemented `DecompressorRuntime` is deterministic and heuristic-driven, producing a compact `Envelope` with request classification, domains, risks, artifacts, context needs, budget hint, and confidence.

`plan/suggest.md` proposes shifting the decompressor from deterministic heuristics toward an LLM-heavy internal prompt chain so the emitted `Envelope` carries richer understanding: normalized user goal, artifact extraction, intent/domain/risk classification, inferred context requirements, planner recommendation, budget hint, ambiguity, assumptions, and final validation.

The core design question is how to improve envelope quality without making the decompressor chaotic, slow, expensive, or contract-breaking for downstream `PlannerRuntime` and `WorkerKernelRuntime`.

## Constraints

- Preserve the strict top-level architecture: exactly three graph nodes and the flow `decompressor_node → planner_node → worker_kernel_node → END`.
- Preserve the contract boundary: decompressor emits only a validated `Envelope`; it must not plan steps, choose workers, dispatch tools, mutate files, enforce budgets, or perform retry policy for workers.
- Avoid schema explosion. If the schema expands, keep it flat and focused.
- Downstream planner logic should receive richer hints but retain deterministic safeguards.
- Any LLM-driven path must have deterministic validation, allowed-label checks, timeouts, and fallback behavior.
- Current implementation and tests are deterministic; rollout should not require an immediate hard switch.

## Options considered

### Option 1 — Keep deterministic heuristic decompressor

Keep `DecompressorRuntime` as a pure Python classifier using regex/rule-based extraction.

**Pros**

- Fast, cheap, reliable, and easy to test.
- Pydantic validation remains straightforward.
- No model latency, provider failures, prompt drift, or token-cost surprises.

**Cons**

- Poor understanding of nuanced requests.
- Rules will grow brittle as domains, planners, and risk categories expand.
- Ambiguity, assumptions, and planner hints will remain shallow.

**Fit**

- Good for Phase 1 baseline and fallback.
- Weak as the final architecture if the system needs high-quality envelopes from natural-language requests.

### Option 2 — One giant LLM prompt directly to Envelope

Send raw input and schema instructions to one LLM call and parse the result as `Envelope`.

**Pros**

- Simpler than multi-step chaining.
- Better natural-language understanding than heuristics.
- Lower latency and cost than sequential prompt chaining.

**Cons**

- More likely to blur normalization, classification, risk, and planner recommendation into one opaque decision.
- Harder to inspect intermediate reasoning artifacts.
- If not strongly structured, can produce invalid labels, invented context, or schema drift.

**Fit**

- Viable as an LLM enrichment mode if wrapped in strict deterministic validation and fallback.
- Not ideal if the team specifically wants separable intermediate artifacts for debugging.

### Option 3 — Heavy sequential prompt chaining inside decompressor

Use multiple internal model calls: normalize request, extract artifacts, classify intent/domain, infer risk/context, recommend planner, assemble envelope, then validate.

**Pros**

- Best conceptual separation of concerns inside the decompressor.
- Intermediate outputs are inspectable and can be logged in metadata.
- Can improve envelope richness for ambiguous, multi-domain, or high-risk requests.

**Cons**

- Sequential model calls multiply latency, cost, and failure points.
- Each chain step can compound hallucination or drift from previous steps.
- More moving pieces to test, version, and observe.
- Risk of giving the decompressor too much operational influence if planner hints become de facto commands.

**Fit**

- Useful for offline analysis, high-complexity requests, or an optional high-quality mode.
- Risky as the default decompressor path unless latency/cost budgets and fallback behavior are explicit.

### Option 4 — Hybrid deterministic harness with structured LLM enrichment

Keep deterministic decompression as the baseline and fallback, but add an optional LLM-backed enrichment path. The LLM can be one structured call initially, with selectively chained substeps only when request complexity or ambiguity justifies it.

**Pros**

- Preserves reliability and testability while improving envelope quality.
- Allows shadow evaluation against the deterministic baseline.
- Enables strict Pydantic validation, allowed-label normalization, confidence thresholds, and deterministic fallback.
- Can adopt `plan/suggest.md` concepts without committing to heavy chaining for every request.

**Cons**

- More architecture than pure heuristics.
- Requires metrics and comparison tooling to know when LLM output is actually better.
- Needs clear precedence rules when deterministic and LLM outputs disagree.

**Fit**

- Best fit for evolving beyond Phase 1 while preserving the simple top-level graph and strict contracts.

## Recommended path

Adopt a hybrid staged path rather than a direct switch from deterministic to heavy prompt chaining.

1. **Keep deterministic decompressor as baseline and fallback.** It should remain responsible for request ID creation, empty input handling, schema-safe defaults, allowed-label checks, confidence thresholds, and emergency fallback.

2. **Expand the `Envelope` carefully only if downstream planners will use the fields.** Candidate additions from `plan/suggest.md` are reasonable:
   - `user_goal`
   - `execution_hints`
   - `planner_hint`
   - `planner_confidence`
   - `planner_alternatives`
   - `ambiguity`
   - `assumptions`

   Keep these as hints, not commands. Avoid fields that imply worker steps, tool choices, or dispatch decisions.

3. **Introduce an LLM enrichment mode behind a feature flag or configuration boundary.** Start with a single structured LLM pass that emits the expanded envelope fields. Use Pydantic validation and deterministic sanitization before the planner sees anything.

4. **Use prompt chaining selectively, not universally.** Reserve multi-step prompting for cases where it has clear value:
   - vague mutation requests (`fix the app`)
   - multi-artifact or multi-domain requests
   - high-risk infra/security/database requests
   - low-confidence deterministic classifications

   For simple questions or obvious file-targeted fixes, a single structured pass or deterministic output is likely enough.

5. **Use shadow mode before promotion.** Run deterministic and LLM-derived envelopes side by side, but feed only the deterministic envelope to `PlannerRuntime` until metrics show the LLM path is valid, stable, and materially better.

6. **Keep planner selection safeguarded.** The planner may prefer `envelope.planner_hint` only when `planner_confidence` clears a threshold and the hint is in the registry. Otherwise, fall back to deterministic selector rules over `input_type`, `intents`, and `domains`.

7. **Treat decompressor authority as understanding-only.** The decompressor can say what the user likely means, what artifacts were mentioned, what context is needed, what risks exist, and which planner seems appropriate. It must not define plan steps, worker types, tool calls, budget enforcement, mutations, or retry logic.

## Risks and mitigations

### Risk: Latency and cost blow up from sequential model calls

**Mitigation:** Do not make heavy prompt chaining the default path. Start with deterministic baseline plus optional single structured LLM call. Add chain steps only for requests that cross ambiguity/risk thresholds. Apply hard timeouts and fallback to deterministic output.

### Risk: Envelope schema drift or invalid model output

**Mitigation:** Keep `Envelope` as the only decompressor output. Validate with Pydantic, clamp labels to allowed sets, reject unknown planner hints, and record invalid/repair events in metadata or logs rather than adapting downstream planners to arbitrary model output.

### Risk: LLM invents missing facts or overstates confidence

**Mitigation:** Prompts and validators should require ambiguity and assumptions to be explicit. Missing files, absent error messages, and unspecified failure modes should lower confidence and add `observe_first` / `do_not_patch_before_observation` style hints rather than fabricate details.

### Risk: Planner becomes too dependent on decompressor hints

**Mitigation:** Planner selection should treat hints as recommendations. Keep deterministic fallback ordering and confidence thresholds. Planner remains responsible for turning `Envelope → Plan`.

### Risk: Contract explosion

**Mitigation:** Add only fields with clear planner use. Avoid nested chains of domain-specific schemas in the shared `Envelope`. Put diagnostic chain traces in `metadata` if needed, not in required contract fields.

### Risk: Hard-to-test nondeterminism

**Mitigation:** Preserve deterministic tests as the contract baseline. Add fixture-based LLM adapter tests with canned responses for validation, repair, fallback, threshold, and planner-hint behavior. Do not make live model calls in unit tests.

## Open Bridge second-opinion synthesis

An Open Bridge comparison pass agreed that pure deterministic is reliable but shallow, one-shot LLM is simpler but opaque, and heavy prompt chaining has substantial latency/cost/failure risks. It recommended a hybrid structured approach: deterministic harness plus validated LLM enrichment, with shadow rollout, canary promotion, circuit breaking, timeouts, schema validation, and deterministic fallback.

The useful adjustment from that pass is to avoid treating heavy prompt chaining as the default runtime. Prompt chaining should be an escalation mode for complex or ambiguous inputs, while a structured single-pass LLM or deterministic result handles simpler cases.

## Next steps for a future implementation plan

1. Decide whether expanded `Envelope` fields are accepted for the next phase.
2. Define allowed labels for `input_type`, `intents`, `domains`, `risks`, `execution_hints`, `budget_hint`, and planner names.
3. Add deterministic selector behavior for `planner_hint` with confidence threshold and registry validation.
4. Design an `EnvelopeEnricher` or similar boundary that can run deterministic-only, shadow, single-pass LLM, or selective-chain modes without changing the top-level graph.
5. Add non-live tests using canned LLM responses for valid output, invalid schema, hallucinated planner, timeout fallback, low-confidence fallback, and ambiguous observe-first behavior.
