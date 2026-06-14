# Existing Code Research

## Current Runtime Shape

Current public pipeline:

```text
app.graph.build_graph
  -> app.decompressor.runtime.DecompressorRuntime
  -> app.planner.runtime.PlannerRuntime
  -> app.worker_kernel.runtime.WorkerKernelRuntime
```

The graph stores `envelope`, `plan`, `result`, `runtime_matrix`, and `errors` in `RuntimeState`.

## Decompressor

Relevant files:

- `app/decompressor/runtime.py`
- `app/decompressor/prompt_chain.py`
- `app/decompressor/contracts.py`
- `app/decompressor/canonicalize.py`
- `app/decompressor/env_config.py`
- `app/decompressor/model_client.py`

Current strengths:

- Runtime boundary is clean.
- One prompt-chain class owns LLM decomposition.
- Runtime adds request IDs, trace rows, metrics, and metadata.
- Prompt chain uses schema validation plus one repair call.
- Deterministic literal extraction and canonicalization protect exact paths, keys, and generated placeholders.
- OpenRouter SDK-backed client already exists and supports `provider.sort=latency`.

Current limits for AppV2:

- Prompt chain is coalesced into one semantic call. That is efficient, but AppV2 should use a gated chain for complex file/code requests:
  - decompose
  - extract exact contracts
  - enrich only when file/code management or ambiguity requires it
  - repair only on validation failure
- Keep the boundary: decomposer describes the request, never plans execution.

## Planner

Relevant files:

- `app/planner/runtime.py`
- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `app/planner/contracts.py`

Current strengths:

- LLM draft plus repair stages.
- Deterministic `PlannerPlanValidator`.
- Existing seven-phase vocabulary is valuable.
- Replan support already exists.
- Artifact dependency validation is useful.

Current limits for AppV2:

- `PlanStep` requires `worker_type`; AppV2 must remove that.
- Permissions are worker-facing and step-specific; AppV2 should express phase-level tool/mutation/verification policies.
- Current planner tries to compensate for worker weaknesses by over-specifying worker handoff artifacts.
- AppV2 should make artifact contracts first-class and phase-owned.
- Planner validation should not run multiple uncontrolled repair loops. It should have a fixed, observable validation-repair budget.

## Worker Runtime

Relevant files:

- `app/worker_kernel/runtime.py`
- `app/worker_kernel/agent_loop.py`
- `app/worker_kernel/agentic.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/control.py`
- `app/worker_kernel/tools.py`
- `app/worker_kernel/artifact_contracts.py`
- `app/worker_kernel/memory.py`
- `app/worker_kernel/worker_quality_tools.py`
- `app/worker_kernel/workers/*.py`

Current strengths:

- One `AgentRunLoop` exists now.
- Tools are permission-gated.
- Mutation operations are mediated by tool code.
- Runtime matrix gives good observability.
- Kernel retry memory exists.
- File-management helper tools improved quality.

Current limits for AppV2:

- Worker-type templates are still present and create extra indirection.
- `TaskCompiler` translates planner steps into worker tasks; AppV2 can skip this layer by compiling a `PhaseFrame` directly.
- `agentic.py` still normalizes many model output shapes and contains historical compatibility logic.
- Artifact validation is split between schemas, artifact contracts, worker group runner, compiler, and runtime.
- Retry/replan/final status decisions are better than before, but still inherit V1 worker-type history.
- AppV2 should use one artifact ledger as the runtime source of truth instead of separate completed/partial/failed stores.

## Existing Tests To Preserve

Keep V1 tests green while building AppV2:

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_control.py tests/test_worker_kernel.py tests/test_graph.py -q
```

Add AppV2 tests separately, probably under:

- `tests/test_appv2_decomposer.py`
- `tests/test_appv2_phase_planner.py`
- `tests/test_appv2_validator.py`
- `tests/test_appv2_worker_loop.py`
- `tests/test_appv2_graph.py`

## Reusable Patterns

Reuse these ideas, not necessarily the files:

- OpenRouter SDK client pattern from `app/decompressor/model_client.py`.
- `.env` parsing pattern from current env config modules.
- Runtime matrix logging pattern.
- Literal contract extraction and generated-placeholder rejection.
- Planner artifact dependency validation.
- Tool-gated mutation from `WorkerToolbox`.
- File-management classifier and verifier concepts.

## Main Design Lesson

The current system got difficult because a phase plan, worker-type fanout, artifact contracts, retry memory, and mutation safety all became separate abstractions. AppV2 should make the planner phase-oriented and the worker loop state-oriented from the start.
