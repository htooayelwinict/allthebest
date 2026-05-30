# References

## Repository Sources

- `app/schemas.py`
- `app/planner/contracts.py`
- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `app/planner/runtime.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/runtime.py`
- `tests/test_planner.py`
- `tests/test_worker_kernel.py`
- `tests/test_graph.py`

## Related Internal Plans

- `plan/llm-planner-runtime-20260530-144206/`
- `plan/constraint-driven-phase-planning-20260530-130500/`

## Open Bridge Second Opinion

Used `open-bridge_consult_openrouter` for architecture stress-test.

Key takeaway preserved:
- Phase-annotated linear execution (metadata-driven) is best minimal path.
- Defer state-machine refactor and subgraph orchestration until evidence justifies it.

## Planner Subagent Synthesis

Used planner subagent for repo-local option analysis.

Key takeaway preserved:
- Add optional `phase/task_id` first, then layer validator policy.
- Avoid introducing new workers/scheduler in initial rollout.
