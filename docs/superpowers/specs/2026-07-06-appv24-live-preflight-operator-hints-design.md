# appv24 Live Preflight Operator Hints Design

## Intent

Make live workflow preflight failures actionable when a requested live transport is missing. The current message says the transport is not configured, but it does not tell the operator which safe environment gates are required before a real run can proceed.

## Boundary

This is a diagnostics-only change.

- No provider calls.
- No token validation.
- No raw secret values.
- No new connector actions.
- No CLI/TUI command behavior changes.
- No appv24 red-zone files.

## Design

Keep the existing `available_transports` gate in `appV2.4/appv24/workflows/live_preflight.py`.

When an action is missing from `available_transports`, append a safe env hint to the issue string for supported live transports:

```text
live-read transport is not configured for github.list_dependabot_alerts; required env: APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN
live-draft transport is not configured for gmail.create_draft; required env: APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET
live-production transport is not configured for telegram.send_message; required env: APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN
```

Unknown actions keep the existing generic issue.

## Testing

Update direct preflight tests for:

- `github.list_dependabot_alerts`
- `gmail.create_draft`
- `telegram.send_message`

The assertions must prove the report includes only env variable names, never token values.

