# Phase 4 - Worker Ledgers, Tools, And Gates

## Goal

Build the deterministic worker substrate before adding the LLM loop.

## Files

- `appV2/worker/ledgers.py`
- `appV2/worker/tools.py`
- `appV2/worker/policy_gate.py`
- `appV2/worker/verification_gate.py`
- `appV2/worker/context.py`
- `tests/test_appv2_worker_tools.py`
- `tests/test_appv2_worker_gates.py`

## Tasks

- Implement `ArtifactLedger` as append-only storage of `ArtifactRecord`.
- Implement `MutationLedger`.
- Implement `ToolRegistry` with first-version file/code tools:
  - repo read tools
  - file write tools
  - verification tools
- Implement `PolicyGate`.
- Implement `VerificationGate`.
- Implement `ContextController` compact phase-frame builder.
- Make every tool result an artifact-ready observation.

## Tests

```bash
uv run pytest tests/test_appv2_worker_tools.py tests/test_appv2_worker_gates.py -q
```

## Done When

- Write proposals cannot escape repo root.
- Write proposals can be denied without mutation.
- Mutation ledger derives changed files, patch diff, and rollback information.
- Verification gate cannot pass from model text alone.
- Context frame excludes full ledger content unless explicitly requested.

## Status

Completed 2026-06-05.
