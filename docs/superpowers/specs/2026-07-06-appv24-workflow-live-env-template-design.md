# appv24 Workflow Live Env Template Design

## Goal

Make actual live workflow runs easier to prepare by generating a safe `.env`
template from a saved workflow package, live mode, and connector allowlist.

The template must show exact variable names and placeholder values only. It
must not read, print, validate, or persist raw secrets.

## Zone Scope

Green-zone primary work:

- `appV2.4/appv24/workflows/live_env_template.py`
- workflow tests and docs

Yellow-zone optional wiring:

- narrow CLI/TUI command handlers and help/autocomplete entries

Red-zone files are out of scope.

## Command Shape

```text
/workflow-live-env-template <live-read|live-draft|live-production> <package> --allow <action>[,<action>]
```

The command does not need a fixture or approval run because it only renders
operator setup variables.

## Behavior

For package permissions, render:

```text
APPV24_<CONNECTOR>_CREDENTIAL_REF=<credential-ref>
APPV24_<CONNECTOR>_SCOPES=<required-scope-list>
```

For built-in live transports, render mode-specific env placeholders:

- GitHub live-read: `APPV24_WORKFLOW_LIVE_READ_ENABLED=true`, `APPV24_GITHUB_TOKEN=<token>`
- Gmail live-draft: `APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true`, access-token mode, and refresh-token alternative
- Telegram live-production: `APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true`, `APPV24_TELEGRAM_BOT_TOKEN=<token>`

Unknown allowlisted actions should render a note instead of inventing variable
names.

## Tests

Add tests proving:

- live-read template includes GitHub credential and transport placeholders
- live-draft template includes Gmail access-token and refresh-token alternatives
- live-production template includes Telegram credential and transport placeholders
- unknown actions produce a safe note
- CLI/TUI command renders without invoking the model or creating run artifacts

## Rollback

Remove the env-template module, command wiring, tests, and docs. Existing
readiness, preflight, and run behavior remain unchanged.
