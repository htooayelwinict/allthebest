# Phase 2 - Artifact Log

## Goal

Replace separate completed/partial/failed/evidence stores with one append-only artifact log.

## Steps

- Add `ArtifactLog` around `ArtifactPayload`.
- Add helper views:
  - completed
  - partial
  - failed
  - retry memory
  - replan carryover
- Convert tool observations into `ArtifactPayload(kind="tool_observation")`.
- Convert worker validation failures into `ArtifactPayload(kind="issue")`.
- Keep existing `Result.artifacts` shape stable.

## Verification

- Missing input artifact tests still trigger blocked/replan correctly.
- Failed tool observations do not become completed truth.
- Replan requests still contain completed artifacts only.

