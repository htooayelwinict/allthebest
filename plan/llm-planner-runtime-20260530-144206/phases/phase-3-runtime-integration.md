# Phase 3: Runtime Integration

## Status

- ✅ Completed (LLM compiler integrated with safe fallback and env-gated wiring).

## Goal

Wire the LLM plan compiler into `PlannerRuntime` while preserving the graph boundary.

## Scope

- Keep `PlannerRuntime.run(envelope) -> Plan` unchanged.
- Allow dependency injection of planner LLM client/compiler for tests.
- Decide fallback policy when LLM planner is unavailable or fails.
- Avoid graph topology changes.

## Likely Files

- `app/planner/runtime.py`
- Possibly `app/planner/env_config.py`
- Possibly `app/graph.py` only for optional dependency injection; topology should stay unchanged.
- `tests/test_graph.py`
- `tests/test_planner.py`

## Integration Options

### Option A: LLM-first with static safe fallback

If configured, run LLM planner. If unavailable/failing, return safe observe-only fallback.

Pros: rollout-safe.

Cons: can hide planner failures unless metadata/logging is clear.

### Option B: LLM-required planner runtime

Planner fails fast if LLM config is missing.

Pros: honest behavior; no illusion of smart planning.

Cons: less robust during local development.

### Option C: Dependency-injected compiler only

Production wiring comes later; tests inject fake compiler now.

Pros: smallest change.

Cons: does not complete end-to-end LLM runtime.

## Recommendation

Use Option A during migration, but mark fallback clearly in `plan.metadata` with `planner_runtime.fallback_reason`. Once stable, switch production policy to LLM-required if desired.

## Tests

- Runtime with injected fake compiler returns fake LLM plan.
- Runtime with failing compiler returns safe fallback or controlled error according to policy.
- Graph still contains `decompressor_node`, `planner_node`, `worker_kernel_node`.
- Graph can run with fake decompressor and fake planner dependency if supported.

## Verification

```bash
uv run pytest tests/test_planner.py tests/test_graph.py -q
```

## Exit Criteria

- Planner runtime uses the LLM compiler path under test.
- Graph boundary remains stable.
- Static planner code is no longer the primary route when LLM planner is configured.
