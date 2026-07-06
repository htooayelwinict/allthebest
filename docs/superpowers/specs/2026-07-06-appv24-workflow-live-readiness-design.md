# appv24 Workflow Live Readiness Design

## Goal

Make the path from a valid workflow package to an actual live run easier to operate by adding a no-side-effect readiness report.

The report answers:

- which live mode is being prepared
- which actions are requested and configured
- which credential-reference env vars are required
- which transport env vars are required
- whether a prior local approval run is required or present
- what the existing live preflight says

## Zone Scope

Green-zone primary work:

- `appV2.4/appv24/workflows/live_readiness.py`
- workflow tests and docs

Yellow-zone optional wiring:

- narrow CLI/TUI slash command registration and handlers
- command tests

Red-zone files are out of scope.

## Command Shape

```text
/workflow-live-ready <mode> <path> <fixture> [approval-run-id] --allow <action>[,<action>]
```

Modes:

- `live-read`
- `live-draft`
- `live-production`

`approval-run-id` is required for `live-draft` and `live-production`, and omitted for `live-read`.

## Behavior

The readiness report must not call provider APIs or execute connector transports.

It may construct env-gated transport objects to determine configured action names, because construction does not perform network calls. It must never print raw tokens, credential references, provider responses, or secret values.

Existing preflight behavior remains unchanged. The readiness layer wraps it and adds operator-facing setup hints.

## Output

Report sections:

- header: mode, status, workflow, fixture
- preflight issues
- checked actions
- configured transports
- required credential env
- required transport env
- next steps

Status:

- `ready` when preflight passes and all requested transports are configured
- `blocked` otherwise

## Tests

Add tests proving:

- live-read readiness reports missing credential and transport env without leaking raw env values
- live-production readiness reports approval-run requirement and Telegram env hints
- ready live-read reports configured transport action when env is present
- CLI/TUI command renders the report without invoking the model or creating run artifacts

## Rollback

Remove the readiness module, command wiring, tests, and docs. Existing preflight/run behavior remains intact.
