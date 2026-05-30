# Implementation Plan: LLM Planner Runtime

## Goal

Implement an LLM-powered planner runtime that absorbs the decompressor envelope and emits a validated, safe, artifact-connected `Plan` for the existing worker runtime.

## Acceptance Criteria

- Planner runtime uses an LLM client to draft `Plan` JSON from an `Envelope`.
- Planner prompt includes the full envelope, worker catalog, plan schema, permission rules, artifact rules, budget rules, and safety policies.
- Planner output is validated before `WorkerKernelRuntime` receives it.
- Invalid planner output gets one LLM repair attempt with structured validation errors.
- If repair fails, planner returns a safe observe-only plan or raises a controlled planner error, depending on the selected implementation policy.
- Plans use only registered worker types.
- Every step `input_artifact` must be produced by an earlier step.
- Plan budget must cover all step budgets.
- Mutation is not allowed before target discovery when envelope context requires target/dependency/performance evidence.
- Mutation plans must include a later verification step.
- Existing graph topology remains unchanged.
- Tests use fake planner clients and do not call live providers.

## Existing Patterns

- Decompressor already uses a small model-client protocol and fake clients in tests.
- Decompressor performs LLM JSON generation, Pydantic validation, canonicalization, and one repair attempt.
- Worker kernel already validates budget at execution time.
- Existing static `CodePlanner` has a useful observe -> patch -> verify pattern.
- Existing `FallbackPlanner` has a useful safe observe-only pattern.

## Recommended Architecture

Add a planner-specific prompt-chain runtime similar in spirit to the decompressor prompt chain, but planner-owned:

```text
PlannerRuntime
  -> LLMPlanCompiler
      -> model_client.complete_json(stage="draft_plan", prompt=..., schema=Plan JSON schema)
      -> Plan.model_validate_json(...)
      -> PlannerPlanValidator.validate(envelope, plan)
      -> optional repair with validation errors
      -> validated Plan
```

Keep `PlannerRuntime.run(envelope) -> Plan` as the public interface so `app/graph.py` does not need topology changes.

## Contract Design

### Planner Model Client

Define a planner-local protocol, similar to decompressor:

```python
class PlannerModelClient(Protocol):
    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str: ...
```

Future implementation can reuse OpenRouter-compatible client code or introduce a shared JSON client abstraction later. Keep the first implementation local and small.

### Planner Output

Use existing `Plan` and `PlanStep` schemas first. Avoid schema expansion unless validation reveals a real need.

The LLM should fill:

- `planner`: likely `llm_planner`.
- `objective`: based on `user_goal` or `normalized_input`.
- `strategy`: concise plan strategy string.
- `steps`: linear worker sequence.
- `budget`: totals matching step ceilings.
- `success_criteria`: concrete completion criteria.
- `metadata`: planner diagnostics, envelope signals used, repair flag, and safety decisions.

## Prompt Requirements

The planner prompt must include:

- Full envelope JSON.
- Allowed worker catalog:
  - `direct_worker`
  - `repo_worker`
  - `code_worker`
  - `research_worker`
  - `infra_worker`
  - `verify_worker`
- Worker capability descriptions.
- Permission semantics:
  - `read_files`
  - `write_files`
  - `run_commands`
- Artifact dependency rule: `input_artifacts` must reference prior `output_artifacts`.
- Budget rule: plan budget must be at least sum of step budgets.
- Safety policies:
  - Do not mutate before target locations are identified if envelope requires target context.
  - Do not make performance claims without evidence if envelope requires performance evidence.
  - Add verification after any write-capable code step.
  - Low confidence or strong ambiguity requires observe-only or discovery-first sequencing.
  - Artifacts from envelope are search hints, not proven files unless explicitly path-like.
- Output only JSON matching the `Plan` schema.

## Validation Rules

Implement validation outside the LLM.

Required checks:

- `Plan.model_validate_json` passes.
- `plan.request_id == envelope.request_id`.
- `plan.plan_id` is non-empty and preferably `plan_{request_id}` or equivalent.
- `plan.steps` is non-empty.
- Step IDs are unique.
- Worker types are in the default registry catalog.
- `max_tool_calls` and `max_model_calls` are non-negative.
- Plan budget covers sum of step budgets.
- Plan budget `max_workers` covers number of steps.
- Every `input_artifact` was produced by an earlier step.
- Write permissions appear only on worker types that can mutate, currently `code_worker`.
- If any step has `write_files=True`, a prior read-only discovery step must exist when envelope has target/dependency/performance context needs.
- If any step has `write_files=True`, a later `verify_worker` step must exist.
- If envelope confidence is below threshold, no write step unless prior discovery and validation policy explicitly allows it.

## Complex Lighthouse Prompt Target Plan Shape

For the known complex envelope, the LLM planner should produce a plan similar to:

```text
1. repo_discovery(repo_worker, read-only)
   Output: repo_inventory
   Finds dependency manifests, repo tree, Lighthouse SDK references, transaction API candidates.

2. performance_context(repo_worker, read-only)
   Output: performance_evidence
   Finds lag evidence, transaction flow, tests, logs, slow paths.

3. sdk_research(research_worker, read-only)
   Input: repo_inventory
   Output: sdk_notes
   Determines Lighthouse SDK availability and integration constraints.

4. async_integration_patch(code_worker, write allowed)
   Inputs: repo_inventory, performance_evidence, sdk_notes
   Output: patch_result
   Patches only if SDK and target APIs are identified. Otherwise returns blocked/proposed result.

5. verify_integration(verify_worker, run commands allowed)
   Input: patch_result
   Output: verification_result
   Runs focused checks.
```

This shape is the canonical high-complexity mixed research/code/performance pattern.

## Files Likely To Change

Implementation phase will likely touch:

- `app/planner/runtime.py`
- `app/planner/base.py`
- `app/planner/selector.py` (likely removed or bypassed)
- `app/planner/planners/*.py` (likely retained temporarily or removed after migration)
- New `app/planner/contracts.py`
- New `app/planner/prompt_chain.py`
- New `app/planner/validator.py`
- New `app/planner/env_config.py` if planner LLM config is separate from decompressor config
- `app/schemas.py` only if stricter schema fields become necessary
- `tests/test_planner.py`
- `tests/test_graph.py`
- Possibly `tests/test_worker_kernel.py` for validation-adjacent expectations

## Configuration Strategy

Prefer planner-specific environment variables so decompressor and planner can use different models/settings:

- `PLANNER_LLM_ENABLED`
- `PLANNER_LLM_PROVIDER`
- `PLANNER_LLM_MODEL`
- `PLANNER_LLM_BASE_URL`
- `PLANNER_LLM_API_KEY`
- `PLANNER_LLM_MAX_TOKENS`
- `PLANNER_LLM_TEMPERATURE`

For development, allow fake clients in tests through dependency injection. Do not require live provider calls in the suite.

Open question: should planner env vars fall back to decompressor env vars? Recommendation: only if explicitly desired; separate config is cleaner and avoids accidental coupling.

## Phases

### Phase 1: Contracts And Validator

Create planner model-client protocol and deterministic plan validator. Keep current static runtime intact while tests are added around validation behavior.

See `phases/phase-1-contracts-validator.md`.

### Phase 2: LLM Prompt Chain

Add planner prompt generation, draft call, JSON validation, repair call, and diagnostics metadata. Use fake clients for tests.

See `phases/phase-2-llm-prompt-chain.md`.

### Phase 3: Runtime Integration

Wire `PlannerRuntime` to use the LLM plan compiler when configured/injected. Decide whether static planners remain as fallback or are removed.

See `phases/phase-3-runtime-integration.md`.

### Phase 4: Complex Envelope Coverage

Add tests for the Lighthouse-style envelope and other envelope categories. Verify high-complexity plans use discovery/research/patch/verify sequencing.

See `phases/phase-4-complex-envelope-coverage.md`.

### Phase 5: Cleanup And Operational Notes

Remove or quarantine obsolete static planner code, document planner env/config behavior, and add optional smoke tooling for planner outputs.

See `phases/phase-5-cleanup-operational-notes.md`.

## Risks And Mitigations

### Risk: LLM emits unsafe mutation plan

Mitigation: deterministic validator rejects mutation before required discovery and requires verification after mutation.

### Risk: LLM emits unknown worker type

Mitigation: validator checks against worker catalog before worker runtime.

### Risk: LLM references missing artifacts

Mitigation: validator rejects input artifacts not produced earlier.

### Risk: Plan budget mismatch

Mitigation: validator rejects or repair-prompts before execution.

### Risk: Repair loop hides model quality issues

Mitigation: allow one repair attempt only and record diagnostics in `plan.metadata`.

### Risk: Static planner tests become obsolete

Mitigation: migrate tests to planner output contracts and safety policies rather than exact static planner names.

### Risk: Worker runtime placeholders limit real-world plan execution

Mitigation: planner can still emit safe task structure now; future worker upgrades can improve execution fidelity without changing planner contract.

## Rollback Strategy

- Keep `PlannerRuntime.run(envelope) -> Plan` interface stable.
- During migration, allow injecting either static selector or LLM compiler.
- If LLM planner fails, route to existing safe `FallbackPlanner` or controlled planner error depending on rollout mode.
- Do not remove static planners until LLM planner coverage is stable.

## Verification Commands

Primary commands:

```bash
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_worker_kernel.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

Optional future smoke command:

```bash
uv run python scripts/smoke_test_plans.py "do we have lighthouse sdk if we do, use it as async function to connect all transation apis and fix lagging issues"
```

## Recommended First Implementation Step

Start with Phase 1: add planner contracts and deterministic validator. This gives the safety boundary before any LLM-generated plan is allowed into the worker runtime.
