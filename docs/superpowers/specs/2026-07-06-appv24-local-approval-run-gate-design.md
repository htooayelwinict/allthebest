# appv24 Local Approval Run Gate Design

## Problem

The live-run path documents require `approval_run_id` to point to a prior local run in the same run store. Current live-draft and live-production approval indexes read approval decisions from the referenced run, but they do not verify that the referenced run manifest was created in `local` mode.

That leaves a gap where a non-local run directory with approval decisions could satisfy live approval matching.

## Design

Add a small run-manifest reader to `WorkflowRunStore`, then make live preflight and live execution reject approval runs whose `run.json` mode is not `local`.

Rules:

- Missing/unknown run behavior remains `unknown approval run: <id>`.
- A run with `mode` other than `local` returns `<mode> requires approval_run_id from a local run`.
- Approval matching by package fingerprint, step id, connector action, and idempotency key remains unchanged after the local-run gate passes.

## Scope

Green-zone only:

- `appV2.4/appv24/workflows/run_store.py`
- `appV2.4/appv24/workflows/live_preflight.py`
- `appV2.4/appv24/workflows/live_runner.py`
- focused workflow tests and live path docs

No red-zone files. No TUI/CLI changes. No provider calls.

## Tests

- Run store can read a persisted run manifest.
- Live-production preflight blocks when `approval_run_id` points to a run whose manifest mode is not `local`.
- Live-draft runner blocks before broker invocation when `approval_run_id` points to a run whose manifest mode is not `local`.
