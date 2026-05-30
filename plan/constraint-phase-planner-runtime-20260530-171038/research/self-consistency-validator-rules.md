# Planner Self-Consistency Rule Research

Date: 2026-05-30 21:26:52

## Question

Should the planner validator enforce additional self-consistency between plan invariants and step artifacts after the mutation-scope rule?

## Summary

Yes, for three deterministic gaps found in batch 3 live outputs:

1. Rollback-before-write is not always enforced.
2. Verification inputs are too thin.
3. FINALIZE steps can be structurally present but output no artifact.

Worker-type overuse is intentionally deferred because worker runtime is not complete and the planner will receive a worker list later.

## Evidence From Batch 3

Saved source: `plan/live-decompressor-planner-runs-batch3-20260530-204008.json`

Across 8 successful live runs:

- `rollback_before_write`: 3/8
- `mutation_scope_before_write`: 8/8
- `verify_has_change`: 8/8
- `verify_has_scope`: 0/8
- `verify_has_evidence`: 2/8
- `finalize_outputs_ok`: 7/8
- `runs_with_odd_workers`: 6/8, but deferred by product direction

The existing validator already guaranteed some rollback/revert artifact in mutation outputs, but that is weaker than ensuring rollback is designed before writes. It also guaranteed a later `verify_worker`, but not that verification consumed the write scope or evidence context it should validate.

## Decision

Implement narrow deterministic validation rules now:

- Mutating plans require a rollback/revert artifact before the first write.
- MUTATE must consume that pre-write rollback/revert artifact.
- VERIFY after mutation must consume mutation outputs.
- VERIFY after mutation must consume write-scope artifacts used by mutation.
- VERIFY after mutation must consume evidence/root-cause artifacts used by mutation.
- FINALIZE steps must output a final artifact.

Do not enforce worker-choice rules yet. Worker allocation should be revisited after worker runtime and planner worker catalog are complete.

## Files Updated

- `app/planner/validator.py`
- `app/planner/prompt_chain.py`
- `tests/test_planner.py`

## Recommendation

Keep these self-consistency rules. They are artifact/phase based, domain-neutral, and directly supported by live-output evidence. Reassess worker-type semantics later when actual worker capabilities are stable.
