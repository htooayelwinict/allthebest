# Implementation Plan

## Goal

Make planner/worker artifact contracts deterministic enough that file-management and manifest workflows use canonical validation and specialized tools without depending on retries.

## Scope

- Add a central exact artifact alias registry.
- Normalize planner step input/output artifacts and write-scope references after JSON parse and before validation.
- Normalize worker task expected outputs and input artifact IDs at compile time.
- Make worker artifact quality, contracts, and mutation synthesis alias-aware.
- Sharpen planner and worker prompt payloads around canonical artifact names and `write_json_manifest`.
- Add regression tests for canonical aliases and strict manifest contracts.

## Non-Goals

- Do not change public `Plan`, `PlanStep`, `Envelope`, or `ReplanRequest` schemas.
- Do not add fuzzy semantic matching.
- Do not refactor the whole agent loop or switch to provider-native tool calls in this pass.

## Verification

- `uv run pytest tests/test_planner.py tests/test_worker_agentic.py -q`
- `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_kernel.py tests/test_graph.py -q`
- `uv run python -m compileall -q app`
- `git diff --check`

## Results

- Implemented exact artifact alias canonicalization at planner parse/validation, worker compilation, artifact quality, and worker final-result normalization boundaries.
- Added canonical artifact catalog guidance to planner prompts and canonical-id guidance to worker prompts.
- Sharpened `write_json_manifest` tool description for manifest/index/inventory/report workflows.
- Verification passed:
  - `uv run pytest tests/test_planner.py tests/test_worker_agentic.py -q` -> 146 passed
  - `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_kernel.py tests/test_graph.py -q` -> 216 passed
  - `uv run python -m compileall -q app`
  - `git diff --check`
- Live probe passed:
  - `uv run python scripts/live_worker_runtime_probe.py --worker-model xiaomi/mimo-v2.5 --scenario file_workspace_cleanup --repo live_worker_alias_contract_probe_20260605 --matrix-poll-interval 1 --out-dir plan`
  - Output: `plan/live-worker-xiaomi-mimo-v2-5-20260605-123305.json`
  - Baseline pytest returncode: 1
  - After pytest returncode: 0
  - Result status: completed
  - MUTATE outputs were canonical: `moved_items_record`, `manifest_update_record`, `change_summary`, `rollback_patch`
  - MUTATE used `write_json_manifest`; all artifact quality invalid counts were 0
  - Remaining non-blocking issue: repo discovery still retried once due `empty_worker_decision`

## Saved Follow-Up Issues

- `worker_empty_decision_retry`: In `plan/live-worker-xiaomi-mimo-v2-5-20260605-123305.json`, the `DISCOVER` repo worker retried once because the first repo locator attempt produced `empty_worker_decision` after several successful readonly observations. Kernel recovery worked and the run completed, but this is still attempt churn. Likely area: repo worker finalization prompt or agent-loop handling when observations are sufficient but the model emits neither tools nor a final result.
- `repo_reader_finalization_budget_retry`: In `plan/live-worker-xiaomi-mimo-v2-5-20260605-124531.json`, the `payment_retry` probe completed successfully and verification passed, but `DISCOVER` retried once because `repo_reader` collected `read_many_files` observations and then hit `model_budget_exhausted_before_final_result`. Kernel recovery worked, no replan was needed, and after-pytest returned 0. This is the same attempt-churn family as `worker_empty_decision_retry`: repo worker instances need a stronger finalization path when observations are already sufficient.
