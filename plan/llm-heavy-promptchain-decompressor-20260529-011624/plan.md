# Implementation Plan: LLM Heavy Prompt-Chain Decompressor

## Goal

Implement an optional, injectable LLM-powered prompt-chain mode inside `DecompressorRuntime` that preserves the existing `DecompressorRuntime.run(user_input: str) -> Envelope` boundary, existing LangGraph topology, and deterministic fallback path.

## Acceptance criteria

- `DecompressorRuntime()` with no constructor arguments keeps current deterministic behavior and passes all existing tests.
- `DecompressorRuntime(model_client=...)` or an equivalent explicit injection enables an LLM prompt-chain path without requiring provider SDK dependencies in the decompressor runtime.
- The prompt-chain path validates every stage output through small Pydantic models before assembling the final `Envelope`.
- Unknown or invalid labels for input type, planner hint, budget hint, intents, domains, risks, and execution hints are dropped, mapped, or clamped deterministically before returning an `Envelope`.
- If any model stage fails with invalid JSON, schema validation errors, exceptions, or timeout-like errors, the runtime falls back safely to deterministic decompression for the affected stage or whole chain.
- The graph remains unchanged in topology: `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- Tests use fake model clients with canned JSON and never call live model providers.
- Model prompts redact common API key, token, password, and secret patterns before calling the injected client.
- `Envelope.metadata` may contain sanitized chain diagnostics such as mode, stage names, and fallback names, but never raw prompts, full model responses, credentials, or large file contents.

## Existing patterns

- `app/decompressor/runtime.py` already has deterministic stage boundaries matching the proposed prompt-chain seams: `_normalize_request`, `_extract_artifacts`, `_classify_request`, `_infer_context_and_risk`, `_recommend_planner`, and `_validate_envelope`.
- `app/schemas.py` already defines an enriched `Envelope` with fields needed for model-backed decompression: `user_goal`, `context_needed`, `execution_hints`, `planner_hint`, `planner_confidence`, `planner_alternatives`, `budget_hint`, `ambiguity`, `assumptions`, and `metadata`.
- `app/planner/selector.py` already treats `planner_hint` as advisory and only honors it when `planner_confidence >= 0.70` and the hint exists in the registry.
- `app/graph.py` has thin top-level nodes and module-level runtime instances; it currently calls only `_decompressor_runtime.run(...)` and serializes the returned envelope.
- Existing tests in `tests/test_decompressor.py`, `tests/test_planner.py`, and `tests/test_graph.py` assert deterministic behavior and should remain valid.
- `pyproject.toml` already depends on `pydantic>=2.0`; use Pydantic v2 APIs such as `model_validate_json`, `model_validate`, and `model_json_schema`.

## Files to change

### Primary implementation files

- `app/decompressor/runtime.py` — add backward-compatible constructor injection, route between deterministic and LLM prompt-chain mode, keep deterministic methods as fallback.
- `app/decompressor/contracts.py` — new internal Pydantic models/protocols for staged LLM outputs and the minimal model-client interface.
- `app/decompressor/labels.py` — new allowed-label constants and deterministic sanitization/clamping helpers.
- `app/decompressor/prompt_chain.py` — new internal prompt-chain orchestrator, prompt construction, stage execution, JSON validation, assembly, and fallback tracking.
- `app/decompressor/redaction.py` — new secret/token redaction helpers for prompt inputs.
- `app/decompressor/__init__.py` — export only stable objects if needed; avoid exposing internal implementation details unnecessarily.

### Tests

- `tests/test_decompressor.py` — keep current deterministic tests and add LLM-mode tests with fake clients.
- `tests/test_planner.py` — add or adjust selector-oriented tests if LLM envelopes need coverage through planner runtime.
- `tests/test_graph.py` — add a guard that default graph invocation does not require or invoke a model client, while keeping node topology assertions.

### Files expected to remain topology-stable

- `app/graph.py` — ideally unchanged; only consider optional dependency-injection factory later if needed, without adding prompt-chain graph nodes.
- `app/schemas.py` — no schema changes expected; only change if implementation uncovers a missing field that planners actually consume.
- `app/planner/selector.py` — no structural change expected; may add tests around low-confidence or invalid hints if not already covered.
- `pyproject.toml` — no provider SDK dependency expected for the minimal injectable protocol approach.

## Phase plan

### Phase 1 — Internal contracts, labels, redaction, and fake-client test scaffolding

Create the internal stage-output models, model-client protocol, allowed-label constants, redaction helper, and fake client patterns in tests. Do not change default runtime behavior yet.

Independent verification:

- `uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"`
- `uv run pytest tests/test_decompressor.py -q`

Rollback: remove the new internal decompressor modules and any new tests added for them.

### Phase 2 — Prompt-chain orchestrator with deterministic validation and safe fallback

Implement the internal LLM prompt-chain component that executes staged `complete_json(...)` calls, validates each stage with Pydantic, clamps labels, assembles an `Envelope`, and records sanitized fallback diagnostics.

Independent verification:

- `uv run pytest tests/test_decompressor.py -q`

Rollback: remove `app/decompressor/prompt_chain.py` and restore tests to deterministic-only coverage.

### Phase 3 — Backward-compatible `DecompressorRuntime` wiring

Add constructor injection to `DecompressorRuntime` and route to LLM prompt-chain mode only when an explicit model client or chain is provided. Preserve deterministic mode as the no-argument default and as the chain fallback.

Independent verification:

- `uv run pytest tests/test_decompressor.py tests/test_planner.py -q`

Rollback: revert `app/decompressor/runtime.py` constructor/routing changes while keeping contract modules if useful.

### Phase 4 — Integration and graph-boundary protection tests

Verify LLM envelopes flow safely into planner selection, low-confidence/invalid hints do not override the selector, and graph tests remain deterministic with no implicit model calls.

Independent verification:

- `uv run pytest tests/test_graph.py tests/test_planner.py -q`
- `uv run pytest -q`

Rollback: revert added integration tests and any optional graph injection changes; default graph should remain usable with deterministic decompressor.

### Phase 5 — Documentation and operational notes

Document the mode boundary, injection contract, no-live-model test rule, redaction policy, metadata policy, and fallback behavior in code docstrings or a small durable note if project docs exist.

Independent verification:

- `uv run pytest -q`

Rollback: revert documentation-only changes.

## Risks and unknowns

- **Provider semantics are unspecified:** Plan around a minimal protocol, not a concrete SDK. Provider configuration should live outside the decompressor.
- **Latency/cost of heavy chaining:** Multiple model calls can be expensive; keep the feature explicit/injected and consider future gating or shadow mode.
- **Prompt injection:** User input may attempt to override JSON/schema instructions. Pydantic validation and allowed-label clamps must be authoritative.
- **Secret leakage:** Redaction must happen before model calls, and metadata must never persist raw prompts or full responses.
- **Fallback granularity:** Stage-level fallback is ideal, but whole-chain deterministic fallback is the smallest safe first implementation. Prefer simple safe fallback before complex repair loops.
- **Test brittleness:** Existing deterministic tests assert exact labels. Keep LLM-mode tests separate and fixture-driven.
- **Graph singleton runtime:** `app/graph.py` currently creates module-level runtimes. Avoid changing this unless dependency injection is needed for future integration tests.
- **Request IDs:** Preserve runtime-owned request ID creation; never trust model-generated IDs.

## Verification commands

Run from repository root:

```bash
uv run python -c "from app.decompressor.runtime import DecompressorRuntime; print(DecompressorRuntime().run('what is docker').input_type)"
uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"
uv run pytest tests/test_decompressor.py -q
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

## Recommended first implementation step

Begin with Phase 1 by creating `app/decompressor/contracts.py`, `app/decompressor/labels.py`, and `app/decompressor/redaction.py`, plus narrowly scoped unit tests for Pydantic stage validation, allowed-label clamping, and secret redaction. This creates safe boundaries before adding any model-call orchestration.

## Detailed order of operations

1. Add internal Pydantic stage models: `NormalizedRequest`, `ArtifactExtraction`, `RequestClassification`, `RiskContextInference`, and `PlannerRecommendation`.
2. Add `PromptChainModelClient` protocol with `complete_json(*, stage: str, prompt: str, schema: dict[str, Any]) -> str`.
3. Add label constants and clamping helpers based on current runtime/test labels.
4. Add redaction helper and tests for token/password/API-key-like strings.
5. Add `LLMPromptChainDecompressor` or equivalent internal component that accepts a model client and deterministic fallback runtime/callback.
6. Implement staged calls using `Model.model_json_schema()` and `Model.model_validate_json(...)`.
7. Assemble final `Envelope` using runtime-owned `request_id` and original raw input, then validate with `Envelope.model_validate(...)`.
8. Wire `DecompressorRuntime.__init__` so no-argument behavior is unchanged and injected model-client behavior is explicit.
9. Add fake-client tests for valid staged responses, invalid JSON fallback, invalid labels, low planner confidence, vague mutation observe-first hints, redaction, and prompt-injection resistance.
10. Run full verification and update documentation/comments.

## Plan folder path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/`
