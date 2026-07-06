# appv24 Live Transport Readiness Preflight Design

## Intent

Make live-read and live-draft preflight detect whether the requested live connector actions have configured live transports before the operator starts a run. This closes the current usability gap where structural preflight can pass and the later run immediately blocks with `live transport is not configured`.

## Boundary

This is a no-side-effect readiness check only.

- Does not call GitHub, Gmail, or any external provider.
- Does not validate OAuth tokens against provider APIs.
- Does not add new connectors.
- Does not enable live-production.
- Does not change workflow schemas, agent loop, provider streaming, compaction, session storage, or app kernel files.

## Design

Add an optional `available_transports` parameter to:

```python
preflight_live_read_workflow(...)
preflight_live_draft_workflow(...)
```

When the parameter is provided, preflight checks every live action reached by the selected fixture path:

```text
action must pass existing validation/policy/credential/approval/catalog gates
action must be in connector allowlist
action must be present in available_transports
```

If missing, preflight returns blocked:

```text
live-read transport is not configured for github.list_dependabot_alerts
live-draft transport is not configured for gmail.create_draft
```

When `available_transports` is omitted, direct unit callers preserve the existing structural-only preflight behavior.

CLI and TUI preflight commands pass environment-built registry keys:

```python
build_live_read_transports_from_env(os.environ).keys()
build_live_draft_transports_from_env(os.environ).keys()
```

Creating registry objects is allowed in preflight because it only reads environment variables and prepares transport instances. It does not invoke the transports.

## Testing

Automated tests must not call providers.

Tests cover:

- direct live-read preflight blocks when `available_transports=()` omits the allowlisted action
- direct live-draft preflight blocks when `available_transports=()` omits the approved draft action
- direct preflight passes when the matching action is present in `available_transports`
- CLI live-read preflight blocks without live-read env transport registry
- CLI live-draft preflight blocks without live-draft env transport registry
- TUI live-read and live-draft preflight pass only when registry builders are monkeypatched or env-enabled

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/live_preflight.py`
- workflow tests
- docs/specs/plans

Yellow-zone:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`
- command tests

Red-zone:

- No changes.
