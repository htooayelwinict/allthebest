# Phase 5: Rollout Notes and Cleanup

## Goal

Stabilize rollout and document operational guidance.

## Scope

- Update planner docs with phase-mode contract.
- Capture migration guidance for old plans.
- Add a smoke script (optional) to print envelope + phase plan.
- Keep anti-overengineering guardrails explicit.

## Files

- `README.md` or runtime docs
- `scripts/` optional smoke helper
- this plan folder status updates

## Verification

```bash
uv run pytest -q
```

## Exit Criteria

- Documentation reflects new planner contract.
- Full suite green.
- No unnecessary architecture expansion introduced.
