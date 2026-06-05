# AppV2 Phase Agent Runtime Plan

## Goal

Create a new `appV2/` package that rebuilds the three-runtime pipeline with a cleaner contract:

```text
user prompt
  -> appV2 decomposer runtime
  -> appV2 phase planner runtime
  -> appV2 single agentic worker runtime
  -> result
```

V2 is not a worker-type system. The planner emits a phase plan. The worker runtime executes that phase plan through one agent loop, one state controller, one artifact ledger, one mutation ledger, one policy gate, and one verification gate.

## Core Design Decision

Keep the three-runtime boundary, but simplify the contracts:

- Decomposer describes the request and outputs an `Envelope`.
- Planner turns the `Envelope` into a `PhasePlan`.
- Worker runtime executes the `PhasePlan` with file/code-management tools.

The LLM may propose tool calls, file operations, phase completion, or planner-level replan signals. The runtime validates and disposes every proposal. The model never directly mutates files, never owns policy, and never decides verification truth by itself.

## Acceptance Criteria

- `appV2/` exists beside `app/` and does not disturb the current runtime.
- Decomposer output is a validated envelope suitable for the planner.
- Planner output is phase-based, not worker-type-based.
- Seven phases remain available: `DISCOVER`, `ANALYZE`, `RESEARCH`, `DESIGN`, `MUTATE`, `VERIFY`, `FINALIZE`.
- Artifact handoff is phase-level through a single artifact ledger.
- One shared validator validates envelopes, phase plans, artifacts, tool proposals, mutation proposals, verification evidence, and final results.
- Worker runtime has one loop with tools, state, ledgers, gates, compact context, retries, and internal planner replan for planner-quality issues only.
- No implementation starts until this plan is reviewed.

## Saved Files

- [plan.md](plan.md) - implementation source of truth
- [research/requirements.md](research/requirements.md) - requested behavior and acceptance details
- [research/existing-code.md](research/existing-code.md) - current repo patterns and lessons
- [research/references.md](research/references.md) - external guidance and design implications
- [phases/](phases/) - execution-ready implementation phases

## Recommended First Step

Start with `phase-1-skeleton-contracts`: create `appV2/` package structure, schema contracts, and the unified validator with unit tests. Do not port runtime logic before the contracts are stable.

## Implementation Status

2026-06-05: Initial AppV2 implementation completed.

- Added `appV2/` contracts, validator, model/env wiring, decomposer, phase planner, worker loop, graph, docs, and live probe script.
- Wired AppV2 OpenRouter defaults:
  - decomposer: `openai/gpt-5.3-codex`
  - planner: `openai/gpt-5.3-codex`
  - worker: `xiaomi/mimo-v2.5-pro`
- Added worker feedback loop for model parse errors, failed tool calls, policy/mutation denials, and artifact validation failures with phase budget ceilings.
- Verification:
  - `uv run pytest tests/test_appv2_decomposer.py tests/test_appv2_phase_planner.py tests/test_appv2_validator.py tests/test_appv2_worker_tools.py tests/test_appv2_worker_gates.py tests/test_appv2_worker_loop.py tests/test_appv2_graph.py -q`
  - `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_control.py tests/test_worker_kernel.py tests/test_graph.py -q`
  - `uv run pytest -q`

2026-06-05: Prompt and worker-control hardening completed.

- Replaced generic AppV2 prompt blocks with production prompt contracts for decomposer, planner, and worker stages.
- Added worker-runtime artifact drift checks, mutation snapshot safety, and internal planner-quality replan with carryover artifacts.
- Ported only related V1 tool-quality patterns into AppV2: richer repo reads, batch file operations, manifest-aware writes, deterministic file-management classification/verification, diff/scope audit, and required verification wrapper.
- Kept retry memory as runtime state in the compact phase frame, not as a worker tool.
- Verification:
  - `uv run pytest tests/test_appv2_prompt_quality.py tests/test_appv2_decomposer.py tests/test_appv2_phase_planner.py tests/test_appv2_validator.py tests/test_appv2_worker_tools.py tests/test_appv2_worker_gates.py tests/test_appv2_worker_loop.py tests/test_appv2_graph.py -q`
  - `uv run pytest -q`
