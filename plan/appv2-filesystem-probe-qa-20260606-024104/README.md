# AppV2 Filesystem Probe QA

Date: 2026-06-06
Model: `openai/gpt-oss-120b`

## Scope

Ran AppV2 live probe scenarios using the upgraded `scripts/live_appv2_runtime_probe.py` with V1-style filesystem scenarios and full runtime matrix capture.

Outputs:

- `plan/live-appv2-file_workspace_cleanup-openai-gpt-oss-120b-20260606-023340.json`
- `plan/live-appv2-file_policy_archive_reorg-openai-gpt-oss-120b-20260606-023639.json`

## Scenario 1: file_workspace_cleanup

Result:

- `result.status = budget_exceeded`
- baseline `pytest -q` return code: `1`
- after `pytest -q` return code: `1`

What happened:

- Decomposer completed cleanly.
- Planner completed cleanly with a 6-phase plan.
- Worker completed `DISCOVER`, `ANALYZE`, and `DESIGN`.
- `MUTATE` failed three times with `feedback_code=path_not_in_strict_policy`.
- The phase then exhausted its 3 model-call budget.

Observed issue:

- The mutation phase could not translate its planned file operations into writes accepted by the strict mutation policy.
- Denial feedback did not lead to a successful repair inside the phase budget.

Evidence:

- Matrix rows `49`, `54`, `59`: `mutation_completed failed path_not_in_strict_policy`
- Matrix row `60`: `model_budget_exceeded`

## Scenario 2: file_policy_archive_reorg

Result:

- `result.status = failed`
- baseline `pytest -q` return code: `1`
- after `pytest -q` return code: `1`

What happened:

- Decomposer completed cleanly.
- Planner completed cleanly with a 6-phase plan.
- Worker completed `DISCOVER`, `ANALYZE`, `DESIGN`, and `MUTATE`.
- `DESIGN` had one invalid structured response, then repaired on the next turn.
- `MUTATE` successfully moved files and created the archive index.
- `VERIFY` ran tests and the run failed on one remaining assertion.

Observed issues:

1. Structured output fragility in `DESIGN`
   - First design response violated the artifact schema before recovering on turn 2.

2. Semantic verification gap after successful mutation
   - The worker moved files successfully, but the resulting archive index still included `README.md` inside `moved_documents`.
   - Verification reported filesystem state and archive index as valid enough to continue, but pytest caught the exact content mismatch.

Evidence:

- Matrix row `41`: `model_decision_invalid` with missing `producer` and malformed artifact payload
- Matrix row `53`: `mutation_completed completed`
- Matrix row `70`: `VERIFY phase_completed failed`
- After-test failure:
  - expected `moved_documents = ["client_alpha_notes.md", "client_beta_followup.md", "retention_policy.md"]`
  - actual output also included `README.md`

## Cross-run insights

1. Decomposer and planner both completed successfully in both runs.
   - Current AppV2 bottlenecks are worker-side, not envelope/plan generation for these scenarios.

2. Two distinct worker-quality problems showed up:
   - mutation-policy execution mismatch
   - semantic verification not strict enough to catch wrong manifest/index content before pytest

3. Planner latency is still noticeable.
   - Each run pays for chained decomposer/planner calls before worker execution starts.
   - That is not the immediate correctness failure here, but it remains part of end-to-end probe cost.

4. Verification currently leans too much on model-reported success structure.
   - In scenario 2, tool evidence existed, but the final verification artifact still missed the exact semantic assertion that pytest later exposed.

## Bottom line

AppV2 is now probeable with realistic filesystem scenarios and full matrix output, which is good progress.

The current failures are not random:

- Scenario 1 shows a mutation policy / repair-loop weakness.
- Scenario 2 shows a verification-quality weakness after an otherwise successful mutation.

These two issues are the main next targets before broader AppV2 filesystem QA.
