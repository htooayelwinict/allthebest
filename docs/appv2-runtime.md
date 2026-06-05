# AppV2 Runtime

AppV2 is an additive runtime lane beside the current `app/` implementation.

```text
prompt
  -> appV2.decomposer.DecomposerRuntime
  -> appV2.planner.PhasePlannerRuntime
  -> appV2.worker.WorkerRuntime
```

The planner emits phases, not worker types. The worker runtime uses one agent
loop and owns the artifact ledger, mutation ledger, policy gate, verification
gate, context control, and final reconciliation.

## Default Live Models

AppV2 OpenRouter defaults are:

```text
APPV2_DECOMPOSER_LLM_MODEL=openai/gpt-5.3-codex
APPV2_PLANNER_LLM_MODEL=openai/gpt-5.3-codex
APPV2_WORKER_LLM_MODEL=xiaomi/mimo-v2.5-pro
```

Set a key and enable each runtime:

```bash
APPV2_DECOMPOSER_LLM_ENABLED=true
APPV2_PLANNER_LLM_ENABLED=true
APPV2_WORKER_LLM_ENABLED=true
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_PROVIDER_SORT=latency
```

## Worker Feedback Loop

The AppV2 worker model can return exactly one of:

- `tool_calls`
- `mutation`
- `final_phase_output`
- `planner_replan_signal`

Every failed tool call, policy denial, mutation denial, model decision parse
error, and artifact validation failure is written as a feedback observation.
The next model turn receives those observations and must repair the next action
within the phase `max_model_calls` and `max_tool_calls` budgets.

Planner replan is reserved for planner-quality issues only. Runtime/tool/model
failures stay inside the worker loop until repaired or budget-exhausted.
When a true planner-quality drift is found inside the worker runtime, the worker
runtime calls `PhasePlannerRuntime.replan(...)` internally and resumes with
completed carryover artifacts. The graph does not route replan.

Retry memory is runtime state injected through the compact phase frame. It is
not exposed as a worker tool.

## Worker Tool Surface

The AppV2 worker tool surface is intentionally narrower than V1, but it keeps
the related production-grade tool qualities:

- high-signal repo reads: `repo_snapshot`, `read_many_files`, `diff_summary`
- deterministic file-management support: `classify_file_management_candidates`
- gated writes: `apply_file_operations`, `write_json_manifest`, `replace_in_file`
- verification proof: `run_required_verification`,
  `verify_file_state_against_manifest`, `mutation_scope_check`

Tools return structured denials when the model can repair the next action.
Policy and mutation gates remain runtime-owned.

## Verification

The worker cannot pass a `VERIFY` phase using model text alone. Passing
verification requires runtime evidence from tools or verified ledger records.

## Tests

```bash
uv run pytest tests/test_appv2_decomposer.py tests/test_appv2_phase_planner.py tests/test_appv2_validator.py -q
uv run pytest tests/test_appv2_worker_tools.py tests/test_appv2_worker_gates.py tests/test_appv2_worker_loop.py -q
uv run pytest tests/test_appv2_graph.py -q
```
