# Phase 5 - Single Agent Loop Runtime

## Goal

Build the one-loop AppV2 worker runtime.

## Files

- `appV2/worker/agent_loop.py`
- `appV2/worker/result_reconciler.py`
- `appV2/worker/runtime.py`
- `tests/test_appv2_worker_loop.py`

## Tasks

- Implement `WorkerRuntime.run(phase_plan, envelope=..., trace=...)`.
- For each phase, build a compact `PhaseFrame`.
- Run one loop:
  - model decision
  - schema validation
  - tool/mutation proposal validation
  - gate execution
  - observation recording
  - final phase artifact validation
- Add local repair for malformed model output and repairable policy denials.
- Add retry memory from ledger summaries, not full transcripts.
- Add internal planner replan only for validator-approved planner-quality issues.
- Reconcile final `RuntimeResult` from ledger evidence.

## Tests

```bash
uv run pytest tests/test_appv2_worker_loop.py -q
```

## Done When

- Fake LLM can complete a read-only repo scan.
- Fake LLM can propose a denied mutation, receive observation, repair, and complete.
- Missing final phase artifacts produce local repair or failure, not fake success.
- Planner replan is not triggered for tool/runtime/model failures.
- Planner replan is triggered for missing impossible phase artifact only when planner runtime is available.

## Status

Completed 2026-06-05.

Note: initial tests cover local repair for denied tools, denied mutations, artifact validation feedback, and model budget ceiling. Planner replan handoff is wired in `WorkerRuntime`; deeper live replan QA remains a follow-up probe.
