# Research: LLM Heavy Prompt-Chain Decompressor Runtime

## Question

How should the current codebase implement an LLM-powered `DecompressorRuntime` using a heavy internal prompt-chain technique while preserving the existing LangGraph topology and runtime boundaries?

## Scope

This research is based on the current codebase plus a narrow Pydantic API documentation check:

- `app/schemas.py`
- `app/decompressor/runtime.py`
- `app/graph.py`
- `app/planner/runtime.py`
- `app/planner/selector.py`
- `tests/test_decompressor.py`
- `tests/test_planner.py`
- `tests/test_graph.py`
- `pyproject.toml`

Old plan documents were not used as inputs for this research.

## Summary

The current decompressor is already shaped as deterministic internal stages:

```text
normalize -> extract artifacts -> classify -> infer risk/context -> recommend planner -> validate Envelope
```

That makes it a good host for a heavy prompt-chain implementation. The clean path is to replace each deterministic stage with a model-backed stage, keep deterministic validators around every boundary, and preserve the public runtime contract:

```python
DecompressorRuntime.run(user_input: str) -> Envelope
```

The top-level graph should not change. `app/graph.py` should still call only `decompressor_runtime.run(...)`, serialize the `Envelope`, then pass it to the planner node.

## Current Codebase Findings

### 1. `Envelope` already has LLM-friendly fields

`app/schemas.py` defines an enriched `Envelope` with fields that map well to prompt-chain outputs:

- `raw_input`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `domains`
- `risks`
- `artifacts`
- `context_needed`
- `execution_hints`
- `planner_hint`
- `planner_confidence`
- `planner_alternatives`
- `budget_hint`
- `confidence`
- `ambiguity`
- `assumptions`
- `metadata`

This means an LLM prompt chain can target the existing schema without a major schema rewrite.

The desired `Envelope` shape is still one object, with no schema explosion:

```python
from typing import Any

from pydantic import BaseModel, Field


class Envelope(BaseModel):
    request_id: str

    raw_input: str
    normalized_input: str
    user_goal: str | None = None

    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    execution_hints: list[str] = Field(default_factory=list)

    planner_hint: str | None = None
    planner_confidence: float = 0.0
    planner_alternatives: list[str] = Field(default_factory=list)

    budget_hint: str = "medium"
    confidence: float = 0.0

    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
```

### 2. The current decompressor methods already define chain boundaries

`app/decompressor/runtime.py` currently has method boundaries that map directly to prompt-chain stages:

- `_normalize_request(...)`
- `_extract_artifacts(...)`
- `_classify_request(...)`
- `_infer_context_and_risk(...)`
- `_recommend_planner(...)`
- `_validate_envelope(...)`

These are the correct seams for converting deterministic logic into model-backed calls.

### 2a. Decompressor authority boundary

Give `DecompressorRuntime` authority over understanding only:

- normalization
- classification
- artifact hints
- risk hints
- context requirements
- planner recommendation
- budget hint
- ambiguity detection

Do not give `DecompressorRuntime` authority over execution:

- worker steps
- tool choices
- task dispatch
- budget enforcement
- file mutation
- retry decisions

This keeps the split clean:

```text
Decompressor: What does the user mean?
Planner:      What should be done?
Kernel:       How do we safely execute the plan?
Worker:       Do this bounded task.
```

### 3. Planner hint semantics already exist

`app/planner/selector.py` honors `envelope.planner_hint` when `planner_confidence >= 0.70` and the hint exists in the selector registry.

This is important for LLM decompression because the model can recommend a planner without directly selecting one. The selector still performs registry validation and deterministic fallback.

### 4. Graph topology does not need changes

`app/graph.py` has exactly three top-level nodes:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

The LLM prompt chain should remain internal to `DecompressorRuntime`. Do not add LangGraph nodes for prompt-chain steps unless the runtime architecture intentionally changes later.

### 5. Tests currently expect deterministic outputs

The current tests assert specific envelope labels and planner choices. An LLM-backed decompressor should use canned model responses in tests, not live model calls.

Recommended new test style:

- fake model returns valid JSON per stage
- fake model returns invalid JSON
- fake model returns invalid labels
- fake model times out or raises
- chain falls back or repairs output deterministically

## Recommended Implementation Design

### Public Runtime Interface

Keep:

```python
class DecompressorRuntime:
    def run(self, user_input: str) -> Envelope:
        ...
```

Add constructor injection for model and validator components:

```python
class DecompressorRuntime:
    def __init__(self, model=None, validator=None):
        self.model = model
        self.validator = validator
```

The exact names can differ, but injection must be backward-compatible: `DecompressorRuntime()` with no arguments should keep the current deterministic mode. A model-backed prompt chain should activate only when a model and validator are provided or when an explicit mode/config enables it.

The intended prompt-chain skeleton is:

```python
class DecompressorRuntime:
    def __init__(self, model=None, validator=None):
        self.model = model
        self.validator = validator

    def run(self, user_input: str) -> Envelope:
        normalized = self.normalize(user_input)

        artifacts = self.extract_artifacts(
            raw_input=user_input,
            normalized=normalized,
        )

        classification = self.classify_request(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
        )

        context = self.infer_context_and_risk(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
            classification=classification,
        )

        planner_hint = self.recommend_planner(
            classification=classification,
            artifacts=artifacts,
            context=context,
        )

        envelope = self.assemble_envelope(
            raw_input=user_input,
            normalized=normalized,
            artifacts=artifacts,
            classification=classification,
            context=context,
            planner_hint=planner_hint,
        )

        return self.validator.validate_or_repair(envelope)
```

This is the pattern: the model proposes structured stage outputs and final envelope content; deterministic code validates, repairs, or falls back.

### Prompt Chain Stages

Use a sequential internal chain:

```text
1. normalize_request
2. extract_artifacts
3. classify_request
4. infer_context_and_risk
5. recommend_planner
6. assemble_envelope
7. validate_or_repair_envelope
```

Each stage should accept only the previous validated outputs it needs.

### Stage Output Shapes

Use small Pydantic models for internal stage outputs. These are internal implementation details, not new top-level runtime objects.

Suggested internal models:

```python
class NormalizedRequest(BaseModel):
    normalized_input: str
    user_goal: str | None = None
    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ArtifactExtraction(BaseModel):
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class RequestClassification(BaseModel):
    input_type: str
    intents: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    budget_hint: str = "medium"
    confidence: float = 0.0


class RiskContextInference(BaseModel):
    risks: list[str] = Field(default_factory=list)
    context_needed: list[str] = Field(default_factory=list)
    execution_hints: list[str] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)


class PlannerRecommendation(BaseModel):
    planner_hint: str | None = None
    planner_confidence: float = 0.0
    planner_alternatives: list[str] = Field(default_factory=list)
```

These models keep each prompt output narrow and validateable.

### Model Client Boundary

Define a minimal model-client protocol instead of coupling the runtime directly to a provider SDK:

```python
class PromptChainModelClient(Protocol):
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        ...
```

The decompressor can call `complete_json(...)`, then validate with Pydantic.

This keeps provider setup outside the decompressor and makes testing straightforward.

### Validation Strategy

Context7 Pydantic docs confirm these relevant APIs:

- `Model.model_validate(...)` validates dictionary-like input.
- `Model.model_validate_json(...)` validates JSON strings.
- `Model.model_json_schema()` generates JSON schema for prompt instructions or provider structured-output APIs.

Use this pattern for each stage:

```python
raw_json = model_client.complete_json(
    stage="classify_request",
    prompt=prompt,
    schema=RequestClassification.model_json_schema(),
)
classification = RequestClassification.model_validate_json(raw_json)
```

Then run deterministic label sanitization after Pydantic validation.

Even if the decompressor is LLM-heavy, deterministic logic must remain as a guardrail for:

- Pydantic validation
- allowed label checking
- empty input handling
- request ID creation
- schema repair retry
- confidence thresholds
- fallback if the LLM fails

Correct pattern:

```text
LLM proposes Envelope
Validator accepts/rejects/repairs
```

Incorrect pattern:

```text
LLM outputs whatever and planner adapts
```

The second pattern would rot the system because downstream code would gradually normalize arbitrary model output instead of enforcing the runtime contract.

### Allowed Label Sets

LLM output needs deterministic clamps. The current codebase already implies these labels:

```python
ALLOWED_INPUT_TYPES = {"question", "mutation_request", "ambiguous_request", "request"}
ALLOWED_BUDGET_HINTS = {"low", "medium", "high"}
ALLOWED_PLANNER_HINTS = {
    "direct_planner",
    "code_planner",
    "research_planner",
    "infra_planner",
    "fallback_planner",
}
```

Current tests and code also use these intent/domain/risk/hint labels:

- intents: `question.answer`, `code.fix`, `fix.ambiguous`, `observe_first`, `research.lookup`, `infra.debug`
- domains: `general`, `code`, `infra`, `research`
- risks: `mutation_requested`, `file_mutation`, `needs_verification`, `ambiguous_scope`, `ambiguous_mutation`, `observation_context_needed`
- execution hints: `inspect_target_file_before_patch`, `verify_after_patch`, `observe_first_required`, `do_not_patch_before_observation`

The LLM path should reject, drop, or map unknown labels before creating the final `Envelope`.

### Model Input Safety Policy

The prompt chain must control what is sent to the model provider, not just what is stored afterward.

Policy:

- Send raw user input only after applying lightweight secret redaction for common token/key/password patterns.
- Do not send full repository file contents by default from the decompressor.
- Send artifact references, paths, summaries, and user-provided snippets only when needed for decompression.
- Require explicit provider/model configuration before any external LLM call is made.
- Keep provider credentials outside tracked files and out of prompts/metadata.
- Log only sanitized stage names, validation results, and fallback events.
- Do not store raw prompts, full model responses, secrets, credentials, or large file contents in `Envelope.metadata`.

Test coverage should include prompt-input redaction and prompt-injection-resistant behavior for strings that look like API keys, passwords, or instructions to ignore the schema.

### Fallback Strategy

Heavy prompt chaining introduces multiple failure points. Use deterministic fallback at these levels:

1. Stage-level fallback: if one stage fails, use deterministic version of that stage if available.
2. Chain-level fallback: if envelope assembly fails, run the current deterministic decompressor.
3. Final validation fallback: if `Envelope.model_validate(...)` fails, emit a safe fallback envelope with `input_type="ambiguous_request"`, low confidence, and observe-first hints.

### Metadata

Store chain diagnostics in `Envelope.metadata`, not graph state:

```python
metadata={
    "decompressor_mode": "llm_prompt_chain",
    "chain_stages": ["normalize", "extract_artifacts", ...],
    "fallbacks_used": [],
}
```

Do not store raw prompts, full model responses, secrets, or large file contents in metadata.

## Heavy Prompt Chain Draft

### Stage 1: Normalize Request

Goal: clean user text, preserve intent, identify ambiguity and assumptions.

Output model: `NormalizedRequest`.

Important instruction: do not invent missing files, errors, or behavior.

### Stage 2: Extract Artifacts

Goal: identify file/path/service/config hints mentioned by the user.

Output model: `ArtifactExtraction`.

Important instruction: only extract artifacts explicitly mentioned or strongly implied by path-like syntax.

### Stage 3: Classify Request

Goal: classify `input_type`, `intents`, `domains`, `budget_hint`, and confidence.

Output model: `RequestClassification`.

Important instruction: use allowed labels only.

### Stage 4: Infer Risk And Context

Goal: infer `risks`, `context_needed`, `execution_hints`, and extra ambiguity.

Output model: `RiskContextInference`.

Important instruction: vague mutation requests must include observe-first hints.

### Stage 5: Recommend Planner

Goal: recommend planner hint and alternatives.

Output model: `PlannerRecommendation`.

Important instruction: planner hint is advisory and must use allowed planner names.

### Stage 6: Assemble Envelope

Goal: combine validated stage outputs with request ID and raw input.

Output model: existing `Envelope`.

Important instruction: no plan steps, worker types, task dispatch details, budget enforcement, retries, or file mutation decisions.

## Expected Envelope Examples

These examples define the quality target for the LLM heavy prompt-chain decompressor. The decompressor should produce envelopes rich enough for the planner to choose a safe strategy without rediscovering intent from scratch.

### Example: `fix the app`

For vague mutation requests, the decompressor should strongly signal observe-first behavior:

```json
{
  "request_id": "req-0001",
  "raw_input": "fix the app",
  "normalized_input": "Fix the application.",
  "user_goal": "Repair the application, but the specific failure is not provided.",
  "input_type": "mutation_request",
  "intents": ["fix.ambiguous", "observe_first"],
  "domains": ["code"],
  "risks": [
    "mutation_requested",
    "ambiguous_mutation",
    "needs_verification",
    "observation_context_needed"
  ],
  "artifacts": [],
  "context_needed": ["repo_tree", "error_context", "target_files_unknown"],
  "execution_hints": [
    "observe_first_required",
    "do_not_patch_before_observation"
  ],
  "planner_hint": "fallback_planner",
  "planner_confidence": 0.74,
  "planner_alternatives": ["code_planner"],
  "budget_hint": "medium",
  "confidence": 0.62,
  "ambiguity": [
    "No error message was provided.",
    "No target file was provided.",
    "No failing behavior was described."
  ],
  "assumptions": [
    "The request likely refers to the current repository or application workspace."
  ]
}
```

Then the planner creates an observe-first plan. It should not patch immediately.

### Example: `fix network_sniffer.py`

For a targeted file-fix request, the decompressor should identify the file artifact, risk, context needs, and code planner recommendation:

```json
{
  "request_id": "req-0002",
  "raw_input": "fix network_sniffer.py",
  "normalized_input": "Fix network_sniffer.py.",
  "user_goal": "Repair the target Python file.",
  "input_type": "mutation_request",
  "intents": ["code.fix"],
  "domains": ["code"],
  "risks": [
    "mutation_requested",
    "file_mutation",
    "needs_verification"
  ],
  "artifacts": [
    {
      "type": "file_hint",
      "path": "network_sniffer.py",
      "language_hint": "python"
    }
  ],
  "context_needed": ["repo_tree", "target_file"],
  "execution_hints": [
    "inspect_target_file_before_patch",
    "verify_after_patch"
  ],
  "planner_hint": "code_planner",
  "planner_confidence": 0.91,
  "planner_alternatives": ["fallback_planner"],
  "budget_hint": "medium",
  "confidence": 0.86,
  "ambiguity": [
    "No concrete bug description was provided."
  ],
  "assumptions": [
    "The target file exists in the current workspace."
  ]
}
```

This is the kind of output that makes the planner strong: the planner receives intent, risk, context, and planner hints, then creates the actual plan without asking the raw input to carry all meaning again.

## Fit With Current Tests

Existing tests should continue covering deterministic behavior. Add new tests for LLM mode:

1. Valid staged responses produce expected envelope.
2. Invalid JSON in one stage falls back deterministically.
3. Unknown planner hint is rejected or maps to fallback.
4. Low planner confidence does not force selector choice.
5. Vague mutation request keeps observe-first hints.
6. Model client is never invoked from graph tests unless injected explicitly.
7. `DecompressorRuntime()` still works with no constructor arguments and stays deterministic.
8. Prompt inputs redact common secret patterns before calling the model client.
9. Prompt injection attempts do not bypass Pydantic validation or allowed-label checks.

## Recommendation

Implement the LLM heavy prompt-chain decompressor as a new injectable mode inside `DecompressorRuntime`, not as graph nodes and not as a replacement for the graph flow.

Suggested next implementation order:

1. Add internal stage-output Pydantic models under `app/decompressor/`.
2. Add a model-client protocol and fake test client.
3. Add an `LLMPromptChainDecompressor` or equivalent internal component.
4. Keep the existing deterministic decompressor as fallback.
5. Add tests with canned JSON responses.
6. Wire `DecompressorRuntime` constructor to choose deterministic or LLM chain mode by injection/config.
7. Leave `app/graph.py` unchanged except optional dependency injection later.

## References

- Codebase: `app/schemas.py`
- Codebase: `app/decompressor/runtime.py`
- Codebase: `app/planner/selector.py`
- Codebase: `app/graph.py`
- Codebase tests: `tests/test_decompressor.py`, `tests/test_planner.py`, `tests/test_graph.py`
- Pydantic docs via Context7: `model_validate(...)`, `model_validate_json(...)`, and `model_json_schema()` for validating structured JSON stage outputs.

## Saved Path

`plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md`
