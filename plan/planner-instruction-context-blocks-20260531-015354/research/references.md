# References

## Repo References

- `app/planner/prompt_chain.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/workers/direct_worker.py`
- `app/worker_kernel/workers/code_worker.py`
- `app/worker_kernel/workers/verify_worker.py`
- `tests/test_planner.py`
- `plan/live-complexity-qa-current-model-20260531-004706.json`

## Relevant Prior Notes

- `plan/constraint-phase-planner-runtime-20260530-171038/research/planner-contract-discipline-qa.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/planner-contract-hygiene-qa-2.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/brainstorm-user-reply-replan-vs-resume-20260531-010346.md`

## Design Principle

Prompt changes should improve worker-local context without making deterministic code parse or enforce natural-language instruction internals in the first pass.
