# Artifact Write-Scope Semantics Research

Date: 2026-05-30 20:26:51

## Question

Should planner validation enforce that mutating write scope comes from a narrowed design artifact instead of broad discovery artifacts?

## Summary

Yes. The recommendation is valid for the current planner. Recent successful live outputs passed validation while allowing `MUTATE` steps to use broad `DISCOVER` artifacts as `write_paths_from_artifacts`.

## Evidence

Current validator behavior before the fix only checked that each `write_paths_from_artifacts` entry was produced by an earlier step. It did not validate the semantic role or producer phase of that artifact.

Examples found in saved live outputs:

- `discovered_targets` from `DISCOVER` used directly as write scope.
- `auth_code_locations` from `DISCOVER` used directly as write scope.
- `target_files`, `scheduler_paths`, `retry_paths`, `target_locations`, and similar broad discovery outputs used directly as write scope.
- A few plans used design-like artifacts such as `fix_design` or `change_design` as write scope, but those artifacts were not explicitly scoped write manifests.

These outputs were validator-clean, so this is a deterministic validation gap, not just prompt quality.

## Decision

Enforce the highest-value narrow rule first:

- `DISCOVER` may produce candidate paths and target locations.
- `DESIGN` must convert candidates into an explicit write-scope artifact.
- `MUTATE` may use `write_paths_from_artifacts` only when the referenced artifact was produced by a prior `DESIGN` step and is named like a write scope.

Allowed write-scope artifact names/signals:

- `mutation_scope`
- `patch_scope`
- `allowed_write_paths`
- `writable_targets`

Explicit literal `permissions.write_paths` remains allowed when specific enough.

## Recommendation

Keep this rule. It improves safety without adding new graph nodes, worker topology, or domain hardcoding. Defer stricter rules for evidence-gap enforceability and semantic worker choices until more live-output evidence accumulates.

## Source Pointers

- `app/planner/validator.py`
- `app/planner/prompt_chain.py`
- `tests/test_planner.py`
- `plan/live-decompressor-planner-runs-20260530-192522.json`
- `plan/live-decompressor-planner-runs-batch2-20260530-195350.json`
