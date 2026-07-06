# appv24 Live-Read Transport Registry Design

## Intent

Make `/workflow-run-live-read` capable of a real read-only connector call when an operator explicitly enables live-read transports, while preserving the current default blocked behavior.

## Boundary

This is a live-read v0 slice only.

- Enabled only when `APPV24_WORKFLOW_LIVE_READ_ENABLED=true`.
- Starts with `github.list_dependabot_alerts`, already present in the built-in connector catalog.
- Uses read-only HTTP GET behavior only.
- Does not add live-draft transports.
- Does not add live-production transports.
- Does not change workflow schemas, agent loop, provider streaming, compaction, session storage, or app kernel files.

GitHub's current REST docs describe the repository Dependabot alerts endpoint as `GET /repos/{owner}/{repo}/dependabot/alerts`, recommend `Accept: application/vnd.github+json`, use REST API versioning, and document read permission requirements for Dependabot alerts.

## Operator Configuration

Required for live-read transport registration:

```text
APPV24_WORKFLOW_LIVE_READ_ENABLED=true
APPV24_GITHUB_TOKEN=<token>
```

Existing credential preflight remains separate and still requires:

```text
APPV24_GITHUB_CREDENTIAL_REF=<opaque-reference>
APPV24_GITHUB_SCOPES=github:security_events:read
```

The token is read only inside the transport registry. It is never included in run metadata, events, traces, reports, errors, or docs output.

## Workflow Input Contract

For `github.list_dependabot_alerts`, the transport resolves the target repo from:

1. `steps[].with.repository`
2. `steps[].with.repo`
3. `dry-run-fixtures/<fixture>.yaml` `input.repository`
4. `dry-run-fixtures/<fixture>.yaml` `input.repo`

The value must be `owner/repo`.

Optional pass-through filters are read from `steps[].with`:

```text
state
severity
ecosystem
package
manifest
scope
sort
direction
per_page
```

`per_page` is clamped to GitHub's documented maximum of 100.

## Components

Create `appV2.4/appv24/workflows/live_transports.py`.

Responsibilities:

- Build a live-read transport map from environment variables.
- Keep disabled-by-default behavior.
- Provide `GitHubDependabotAlertsTransport`.
- Slim Dependabot alert JSON to declared safe outputs:
  - `alerts`
  - `count`
- Return `BrokerResult(status="blocked")` for missing repo, bad repo shape, HTTP failures, malformed JSON, or unexpected response shape.

Update CLI/TUI live-read command adapters only:

- Replace `LiveWorkflowBroker({}, mode="live-read")` with `LiveWorkflowBroker(build_live_read_transports_from_env(os.environ), mode="live-read")`.
- Leave live-draft commands on the empty registry.

## Testing

Automated tests must not call GitHub.

Tests cover:

- registry disabled by default
- registry enabled only with flag and token
- GitHub transport URL, headers, query params, and output slimming through a fake HTTP client
- blocked missing repo without HTTP invocation
- CLI live-read command uses the registry and can pass with a fake transport
- TUI live-read command uses the registry and can pass with a fake transport
- existing no-transport tests still pass when the enable flag is absent

## Zone Impact

Green-zone:

- `appV2.4/appv24/workflows/live_transports.py`
- workflow tests
- docs/specs/plans

Yellow-zone:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`

Red-zone:

- No changes.
