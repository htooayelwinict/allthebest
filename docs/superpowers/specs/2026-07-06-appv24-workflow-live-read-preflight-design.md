# appv24 Workflow Live-Read Preflight Design

Date: 2026-07-06
Scope: appV2.4/appv24 only
Status: approved by standing instruction to proceed

## Goal

Add a no-side-effect live-read readiness check so operators can verify a saved workflow package before creating a `live-read` run artifact.

## Command

```text
/workflow-live-read-preflight <package> <fixture> --allow <connector.action>[,<connector.action>]
```

## Behavior

The command runs the same passive gates as `live-read` execution without invoking a broker transport and without creating `.workflow-runs`:

- package load
- workflow validation
- IR compile
- policy evaluation
- fixture selection
- credential reference preflight from `APPV24_<CONNECTOR>_CREDENTIAL_REF`
- credential scope preflight from `APPV24_<CONNECTOR>_SCOPES`
- connector action allowlist
- connector catalog lookup
- read-only eligibility

It reports checked actions and blocking issues. It never calls the model, external APIs, MCP tools, shell, browser, Gmail, Telegram, GitHub, or cloud services.

## Design Choice

Implement this in `appV2.4/appv24/workflows/live_preflight.py` beside the existing live-draft preflight. Keep CLI/TUI changes as thin command adapters.

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/live_preflight.py`
- workflow tests
- docs

Yellow-zone, narrow and tested:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`

Red-zone files remain untouched.
