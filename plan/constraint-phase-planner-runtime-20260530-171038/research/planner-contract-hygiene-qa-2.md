# Planner Contract Hygiene QA 2

Date: 2026-05-30 23:03:29

## Question

Should the planner contract be tightened after live output showed mutation with `read_files=false` and mutation without explicit root-cause/design context?

## Research Summary

- Repo-local review confirmed the previous contract pass already enforced allowed modes, phase/mode mapping, DESIGN scope and rollback, MUTATE scoped writes, and VERIFY context.
- The saved two-prompt batch showed one regression: a `MUTATE` step used `read_files=false` and consumed only scope/rollback/write-path artifacts, not explicit root-cause or fix-design context.
- This is contract hygiene because it validates declared permissions and artifact lineage, not actual runtime path resolution or file mutation behavior.
- Open Bridge second-opinion review agreed these additions are structural contract checks if implemented as broad artifact-name lineage rules rather than domain-specific gates.

## Decisions

- Require `MUTATE` to set `permissions.read_files=true`.
- Require `MUTATE` to consume at least one root-cause, evidence, or design context artifact.
- Require mutating `DESIGN` output `verification_plan` or `test_plan` in addition to `mutation_scope` and `rollback_plan`.
- Reject `plan.planner` when it equals a worker type from the worker catalog.
- Do not require `FINALIZE.permissions.read_files=true`; artifact-only finalization remains valid.
- Keep actual runtime enforcement in the worker kernel.

## Files Updated

- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `tests/test_planner.py`

## Verification

```bash
uv run pytest tests/test_planner.py -q
# 42 passed

uv run pytest -q
# 81 passed
```

## Live QA

First live hygiene run found a prompt-repair weakness:

- `plan/live-planner-hygiene-qa-20260530-225812.json`
- `success_count`: 1
- `failure_count`: 1
- failure cause: the model fixed mutation scope/read/context but left `plan.execution_pattern` empty after two repairs.

Prompt repair instructions were tightened to explicitly repair top-level `execution_pattern` and `global_invariants` when validation errors mention them.

Final live hygiene run:

- `plan/live-planner-hygiene-qa-20260530-230329.json`
- `success_count`: 2
- `failure_count`: 0
- `qa_issue_count`: 0

Manual QA confirmed both plans had:

- planner identity not equal to a worker type
- non-empty `execution_pattern` and `global_invariants`
- `DESIGN` outputs `mutation_scope`, `rollback_plan`, and `verification_plan`
- `MUTATE` permissions include `read_files=true` and `write_files=true`
- `MUTATE` consumes scope, rollback, and root-cause/evidence/design context
- `MUTATE` writes through `write_paths_from_artifacts`
- `VERIFY` consumes `change_summary`, write scope, root-cause/evidence context, and verification plan

## Follow-Up

Worker-kernel enforcement remains the next boundary for runtime behavior: resolving scope artifacts to paths, blocking out-of-scope writes, forbidden commands, budget enforcement during execution, output-artifact validation, and stop-on-verification-failure.
