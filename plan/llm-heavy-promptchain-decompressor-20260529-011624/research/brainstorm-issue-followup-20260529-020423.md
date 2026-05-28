# Brainstorm: Follow-up Issue for LLM Prompt-Chain Decompressor

Date: 2026-05-29 02:04:23

## Problem statement

The referenced issue was not specified beyond “that issue,” so this brainstorm assumes it concerns the active plan: the completed optional LLM-heavy prompt-chain decompressor in `DecompressorRuntime`.

Current code already implements the key plan goals:

- deterministic default behavior in `app/decompressor/runtime.py`
- injectable LLM prompt chain in `app/decompressor/prompt_chain.py`
- provider/env configuration in `app/decompressor/env_config.py` and `model_client.py`
- labels/contracts/redaction safeguards
- fake-client tests in `tests/test_decompressor.py`

The main decision is what kind of follow-up work should be prioritized now that the feature exists: production hardening, quality/evaluation, operational observability, or contract simplification.

## Constraints

- Preserve the public `DecompressorRuntime.run(user_input: str) -> Envelope` contract.
- Keep top-level graph topology unchanged: `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- Keep LLM use optional and injectable; default runtime must remain deterministic and testable without network calls.
- Avoid exposing secrets or raw prompts in durable metadata.
- Keep planner/kernel authority boundaries intact; decompressor emits understanding and hints only.

## Options considered

### Option 1: Production hardening pass

Add a focused hardening checklist around timeout behavior, response size limits, provider error categorization, retry policy, metadata shape, and stricter artifact sanitization.

Pros:

- Best fit if the issue is about runtime reliability or safe production use.
- Reduces blast radius of model/provider instability.
- Builds on existing fallback path rather than changing architecture.

Cons:

- May add operational code before real usage data proves it is needed.
- Could overcomplicate a Phase 1 runtime if applied too broadly.

### Option 2: Evaluation and regression suite

Create a curated corpus of decompressor inputs and expected envelope properties, covering vague fixes, questions, infra requests, file hints, prompt-injection attempts, secrets, and malformed model outputs.

Pros:

- Highest confidence gain with low architectural risk.
- Keeps provider calls mocked/canned while validating behavior across many cases.
- Helps future changes avoid silent regressions in labels and planner hints.

Cons:

- Does not directly improve live model output quality.
- Requires careful assertions on properties rather than brittle exact envelopes.

### Option 3: Prompt and schema refinement

Refine per-stage prompt instructions, allowed labels, and stage-specific schema guidance so models produce better outputs before sanitization.

Pros:

- Improves LLM-mode quality if actual model behavior is weak.
- Can reduce fallback frequency caused by avoidable malformed output.

Cons:

- Needs real provider transcripts/eval results to avoid guesswork.
- Prompt changes can be brittle without a regression corpus.

### Option 4: Observability and diagnostics

Add lightweight counters/log events around mode, completed stages, fallback reason, redaction occurrence, and provider latency, while keeping prompt text and secrets out of logs.

Pros:

- Helps understand whether LLM mode is useful or frequently falling back.
- Good prerequisite for production tuning.

Cons:

- Logging/metrics framework may not exist yet in the repo.
- Risk of accidentally logging sensitive prompt data if boundaries are not explicit.

### Option 5: Defer LLM follow-up; focus planner/kernel behavior

Treat decompressor as complete for now and test whether downstream planners and workers correctly honor ambiguous requests, observe-first hints, budgets, and planner selection.

Pros:

- Validates end-to-end value rather than optimizing one component.
- May reveal bigger gaps outside the decompressor.

Cons:

- If the issue is specifically about the LLM chain, this avoids the root problem.
- Broader scope can dilute the follow-up.

## Recommended path

Start with **Option 2: Evaluation and regression suite**, then use results to guide either **Option 1: Production hardening** or **Option 3: Prompt refinement**.

Reasoning:

1. The implementation already has the correct safety architecture: injectable model client, deterministic fallback, Pydantic validation, label clamping, and redaction.
2. The next highest-leverage step is proving behavior across representative inputs before adding more runtime complexity.
3. A corpus-driven suite can clarify whether the real issue is malformed model outputs, poor classification, unsafe metadata, planner hint mistakes, or downstream planner/kernel handling.

Suggested evaluation categories:

- direct questions: `what is docker`
- code mutation with file hint: `fix network_sniffer.py`
- vague mutation: `fix the app`
- infra mixed artifacts: `fix docker-compose.yml and check nginx.conf`
- research-only requests
- prompt-injection-like requests asking to ignore schema
- user input containing API-key/password/bearer-token patterns
- model responses with invalid JSON
- model responses with invalid labels
- model/provider exception mid-chain
- overly confident unsupported planner hints

For each case, assert invariant properties rather than full exact envelopes, such as:

- deterministic mode never calls a model
- LLM mode emits only allowed labels after sanitization
- fallback preserves request ID and records non-sensitive fallback metadata
- vague mutations include observe-first hints
- secret-like input is redacted in prompts but preserved only where intentionally required by the envelope contract
- planner hints remain registry-compatible or low-confidence/null when uncertain

## Risks and mitigations

- Risk: The issue may refer to a different plan or bug.
  - Mitigation: Confirm issue details before implementation; this brainstorm is scoped to the active LLM decompressor plan because it is the most relevant active completed plan.

- Risk: Evaluation tests become brittle by asserting exact LLM-shaped output.
  - Mitigation: Prefer invariant/property assertions and small fake-client fixtures per scenario.

- Risk: Prompt refinement without real model evidence introduces churn.
  - Mitigation: Only change prompts after failures are captured in the evaluation corpus.

- Risk: Observability accidentally logs secrets or raw prompts.
  - Mitigation: Log only mode, stage names, counts, error types, and booleans like `redacted_prompt_input`; never log raw user input, prompt bodies, API keys, or provider responses by default.

- Risk: Production hardening expands scope beyond Phase 1.
  - Mitigation: Keep hardening narrow: timeouts, response-size limits, error categorization, and fallback metadata only.

## Next steps

1. Clarify the exact issue text if available.
2. If it is decompressor-related, draft a small test/evaluation plan first.
3. Use corpus results to decide whether follow-up implementation should target prompts, runtime hardening, or downstream planner/kernel behavior.
4. Keep any implementation in a new or resumed plan phase rather than ad hoc code changes.

## Saved path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/brainstorm-issue-followup-20260529-020423.md`
