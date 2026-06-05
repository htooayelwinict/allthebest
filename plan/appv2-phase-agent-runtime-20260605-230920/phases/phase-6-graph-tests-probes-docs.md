# Phase 6 - Graph, Tests, Probes, And Docs

## Goal

Wire AppV2 end to end while keeping V1 stable.

## Files

- `appV2/graph.py`
- `tests/test_appv2_graph.py`
- `scripts/live_appv2_runtime_probe.py`
- `docs/appv2-runtime.md`
- `.env.example`
- optionally `README.md`

## Tasks

- Build AppV2 graph:
  - decomposer node
  - phase planner node
  - worker runtime node
- Add env examples:
  - `APPV2_DECOMPOSER_LLM_ENABLED`
  - `APPV2_DECOMPOSER_LLM_MODEL`
  - `APPV2_PLANNER_LLM_ENABLED`
  - `APPV2_PLANNER_LLM_MODEL`
  - `APPV2_WORKER_LLM_ENABLED`
  - `APPV2_WORKER_LLM_MODEL`
  - common OpenRouter/OpenAI aliases
- Add fake-client graph test.
- Add live probe script for file/code scenarios.
- Add documentation explaining the AppV2 runtime boundary.
- Run V1 regression tests.

## Tests

```bash
uv run pytest tests/test_appv2_graph.py -q
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_agentic.py tests/test_worker_control.py tests/test_worker_kernel.py tests/test_graph.py -q
uv run pytest -q
```

## Live Probe

```bash
uv run python scripts/live_appv2_runtime_probe.py --scenario file_workspace_cleanup --worker-model <model> --matrix-poll-interval 5 --out-dir plan
uv run python scripts/live_appv2_runtime_probe.py --scenario greenfield_calculator_api --worker-model <model> --matrix-poll-interval 5 --out-dir plan
```

## Done When

- AppV2 graph works with fake clients.
- AppV2 live probe saves full envelope, phase plan, worker result, ledgers, and runtime matrix.
- V1 tests still pass.
- Documentation explains how AppV2 differs from V1 and how to run it.

## Status

Completed 2026-06-05.

Note: `scripts/live_appv2_runtime_probe.py` is added for future live QA. This implementation turn verified with fake-client tests and full local pytest; no live LLM probe was run in this turn.
