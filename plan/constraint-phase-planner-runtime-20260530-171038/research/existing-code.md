# Existing Code Research

## Runtime Topology

- `app/graph.py` enforces stable flow:
  - decompressor -> planner -> worker kernel
- This should remain unchanged for minimal-risk rollout.

## Current Planner Stack

- `app/planner/prompt_chain.py`
  - LLM draft + optional repair
  - deterministic post-LLM validation
  - diagnostics metadata
  - budget auto-alignment before validation

- `app/planner/validator.py`
  - worker type checks
  - artifact dependency ordering
  - mutation gating, rollback and verify checks
  - stop/replan metadata checks for mutating plans
  - currently lacks explicit phase/task progression model

- `app/planner/runtime.py`
  - clean boundary: `run(envelope) -> Plan`
  - safe fallback plan if LLM unavailable/fails

## Current Plan Contract

- `app/schemas.py`
  - `Plan` and `PlanStep` do not yet carry explicit phase/mode/task fields.
  - metadata exists and can carry policy context now.

## Worker Runtime Constraints

- `app/worker_kernel/runtime.py` executes steps linearly.
- `app/worker_kernel/registry.py` supports fixed worker set.
- Sequential execution is sufficient for first phase-aware rollout.

## Test Baseline

- `tests/test_planner.py` already validates many safety properties.
- Existing tests demonstrate LLM planner + repair behavior is stable.
- Additional phase-task tests can be layered incrementally.

## Key Observations

1. The architecture already supports staged planner behavior and deterministic checks.
2. Adding optional phase/task fields is low-risk and high-value.
3. Multi-task can be represented within one linear plan using `task_id` + phase tags.
4. No immediate need for new workers or new graph nodes.
