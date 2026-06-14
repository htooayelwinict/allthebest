# Phase 1 - Freeze And Map

## Goal

Stop adding logic to V2 and map each useful V2 behavior to the single runtime target.

## Steps

- Mark V2 as temporary in docs/config.
- Identify V2 behaviors to keep:
  - provider tool-call normalization
  - typed tool observations
  - evidence-derived mutation artifacts
  - local repair on tool failure
  - retry memory snapshots
- Identify V2 behaviors to discard:
  - separate `TaskFrame`
  - separate `ToolEvent`
  - separate `EvidenceStore`
  - separate `ResultReconciler`
  - separate runtime entry point

## Verification

- Existing tests still pass.
- No live probe required in this phase.

