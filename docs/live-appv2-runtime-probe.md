# Live AppV2 Runtime Probe

This probe runs the AppV2 pipeline:

```text
prompt
  -> AppV2 DecomposerRuntime
  -> AppV2 PhasePlannerRuntime
  -> AppV2 WorkerRuntime
  -> final RuntimeResult
```

It now supports seeded filesystem scenarios modeled after the V1 probe so AppV2
can be QA'd against the same kinds of file-management tasks.

## Scenarios

Supported built-ins:

- `readme_status_update`
- `file_workspace_cleanup`
- `file_policy_archive_reorg`

## What The Script Does

- seeds or refreshes the target repo for the selected scenario
- runs baseline `pytest`
- runs AppV2 decomposer, planner, and worker with live LLM calls
- runs `pytest` again after the worker finishes
- saves envelope, phase plan, result, runtime matrix, git status, and final file snapshot

## Example

```bash
uv run python scripts/live_appv2_runtime_probe.py \
  --scenario file_workspace_cleanup \
  --worker-model openai/gpt-oss-120b \
  --matrix-poll-interval 1 \
  --out-dir plan
```

## Output

Saved JSON includes:

- `baseline_pytest`
- `envelope`
- `phase_plan`
- `result`
- `after_pytest`
- `runtime_matrix`
- `git_status`
- `final_files`

This makes AppV2 filesystem QA comparable to the richer V1 probe output.
