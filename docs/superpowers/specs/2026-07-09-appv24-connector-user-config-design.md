# appv24 Connector User Config MVP Design

## Status

Approved for design by Lewis on 2026-07-09.

## Purpose

Connector credentials and connector runtime variables should not require users to
edit `.env` for normal appv24 workflow use. The current workflow MVP can run real
connectors, but Brave, Telegram, and related connector values are configured
through `APPV24_*` environment variables. That is usable for manual QA and CI, but
it is not comfortable or secure as the main user experience.

The MVP introduces a connector-owned user config interface. Workflow packages
continue to declare connector actions and required scopes. Connector credentials,
target policy values, and transport settings are supplied by approved local app
configuration at execution time.

## Non-Negotiable Boundaries

- Work stays in appv24.
- No appv24 kernel/red-zone changes.
- No workflow package raw secrets.
- No connector values printed in reports, logs, run artifacts, or validation
  messages.
- No GHCR or npm release work.
- `.env` remains supported as a fallback for CI, manual QA, and existing tests.
- Connector interfaces stay separate from workflow semantics. Workflow depends on
  connector capabilities; connectors own credential/config resolution.

## Current State

The workflow live path already has the right execution gates:

- package validation
- connector contract validation
- credential-reference preflight
- live-read/live-draft/live-production mode gates
- approval ledger
- idempotency
- target policy checks
- safe readiness reports

The weak point is configuration UX and ownership. Today, `credential_refs.py` reads
credential refs and scopes from `APPV24_<CONNECTOR>_CREDENTIAL_REF` and
`APPV24_<CONNECTOR>_SCOPES`. `live_transports.py` builds live transports from raw
environment variables such as Brave API keys, Telegram bot tokens, base URLs, and
Telegram allowed chat IDs.

The app already has a better precedent for LLM providers: TUI `/login` prompts for
credentials, persists them under the app agent directory, and sets restrictive file
permissions. Connector config should follow that mental model.

## MVP Outcome

Users can configure supported connectors through appv24 commands instead of
manually editing `.env`.

For the MVP:

- Brave Search can be configured with an API key, optional base URL, credential
  ref, and granted scopes.
- Telegram can be configured with a bot token, allowed chat IDs/subscriber list,
  optional base URL, credential ref, and granted scopes.
- Workflow readiness and guided automation use connector user config first.
- `.env` fallback remains available and produces backward-compatible behavior.
- Reports show connector IDs, configured profile names, required scopes, and safe
  setup hints only.

## Architecture

```text
TUI/CLI connector setup
  -> ConnectorConfigStore
  -> ConnectorConfigResolver
  -> credential preflight adapter
  -> live transport factory
  -> workflow readiness / guided run / live run
```

### ConnectorConfigStore

Stores connector profiles in the appv24 user agent directory:

```text
<agent-dir>/connectors.json
```

The agent directory is resolved through the existing appv24 config path helpers.
The store must create parent directories with mode `0700` and write the config
file with mode `0600`.

The initial JSON shape is explicit and connector-owned:

```json
{
  "version": 1,
  "connectors": {
    "brave": {
      "profile": "default",
      "credential_ref": "brave-local",
      "scopes": ["brave:search"],
      "config": {
        "api_key": "REDACTED_BRAVE_API_KEY",
        "base_url": "https://api.search.brave.com/res/v1/web/search"
      }
    },
    "telegram": {
      "profile": "default",
      "credential_ref": "telegram-local",
      "scopes": ["telegram:subscribers:read", "telegram:send"],
      "config": {
        "bot_token": "REDACTED_TELEGRAM_BOT_TOKEN",
        "allowed_chat_ids": ["CONFIGURED_CHAT_ID"],
        "base_url": "https://api.telegram.org"
      }
    }
  }
}
```

The store API should support:

- load all configured connectors
- read one connector profile
- upsert one connector profile
- remove one connector profile
- render a redacted list view

Malformed config should fail closed for live execution and produce a safe,
actionable setup issue.

### ConnectorConfigResolver

The resolver is the only object workflow commands use to obtain connector runtime
configuration. It resolves in this order:

1. appv24 connector user config
2. environment fallback

User config wins over environment fallback when both exist. This prevents stale
`.env` values from silently overriding a user-configured connector.

The resolver returns structured connector capability objects, not raw
environment dictionaries. Example shape:

```python
ConnectorRuntimeConfig(
    connector_id="telegram",
    credential_ref="telegram-local",
    scopes=("telegram:subscribers:read", "telegram:send"),
    config={...},
    source="user-config",
)
```

Secrets may exist in memory while building transports, but must not be persisted
to workflow run artifacts or returned by report formatters.

### Credential Preflight Adapter

Existing workflow credential preflight should continue to depend on
`CredentialRefResolver`. The MVP adds an adapter that converts resolved connector
runtime config into the current credential preflight shape:

```text
ConnectorConfigResolver -> CredentialRefResolver
```

This keeps the live runner and phase runner contracts stable.

Credential preflight still enforces exact scopes and rejects wildcard scopes.

### Live Transport Factory

Live transport construction moves behind connector config resolution:

```text
build_live_read_transports(resolver)
build_live_draft_transports(resolver)
build_live_production_transports(resolver)
```

The existing `build_*_from_env()` functions remain as compatibility wrappers.
They can call into the new factory with an env-backed resolver, or stay as thin
fallback code during migration.

MVP transport coverage:

- `brave.search` live-read from Brave connector config.
- `telegram.list_subscribers` live-read from Telegram allowed chat IDs.
- `telegram.send_message` live-production from Telegram bot config.
- `telegram.broadcast_message` live-production from Telegram bot config.

### TUI and CLI Commands

The MVP should expose connector setup without requiring users to edit files.

MVP commands:

```text
/workflow-connector-list
/workflow-connector-configure <connector>
/workflow-connector-remove <connector>
```

CLI equivalents should exist for automation:

```text
appv24 workflow-connector-list
appv24 workflow-connector-configure <connector>
appv24 workflow-connector-remove <connector>
```

Interactive configuration uses secret prompts for API keys and bot tokens.
List output is always redacted.

Example list output:

```text
Configured workflow connectors:
- brave: configured via user-config, scopes: brave:search, secrets: present
- telegram: configured via user-config, scopes: telegram:subscribers:read, telegram:send, secrets: present
```

### Readiness Reports

Readiness should prefer user-facing connector setup hints:

```text
missing connector config for telegram; run /workflow-connector-configure telegram
```

When env fallback is relevant, reports may include safe compatibility hints:

```text
env fallback: APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES, APPV24_TELEGRAM_BOT_TOKEN
```

Reports must not include raw tokens, chat IDs, API keys, OAuth tokens, or full
connector config payloads.

## Security Model

The MVP improves local secret handling but does not claim OS keychain-grade
storage. It creates the boundary needed to add keychain storage later.

Rules:

- Connector config directory mode: `0700`.
- Connector config file mode: `0600`.
- Secret prompts must be masked.
- Secret fields must be redacted in list/status/report output.
- Run artifacts may persist:
  - connector id
  - credential ref
  - scope names
  - config source label
  - safe env variable names
- Run artifacts must not persist:
  - API keys
  - bot tokens
  - OAuth tokens
  - raw chat IDs from connector config
  - target-policy evidence should use counts, hashes, or allowlist-match labels
    unless an approved workflow input already contains the target value
- Telegram target-policy checks remain mandatory for sends and broadcasts.

## Error Handling

- Missing user config and missing env fallback blocks readiness and live execution.
- Malformed connector config blocks readiness and live execution.
- Unsupported connector id in configure/remove commands returns a safe usage
  error.
- Missing required scopes produces the same credential-preflight class of issue,
  but setup hints reference connector configuration first.
- Incomplete Telegram config blocks production transports.
- Incomplete Brave config blocks live-read transport for `brave.search`.

## Migration Strategy

This is an additive migration.

1. Add connector config store and resolver.
2. Teach credential preflight and transport factories to use the resolver.
3. Keep env fallback and current env-based tests passing.
4. Add TUI/CLI setup commands.
5. Update docs/readiness text to prefer connector config setup.

Existing `.env` users should not break.

## Testing Plan

Unit tests:

- connector config store creates parent directory and file with restrictive
  permissions
- store can write, read, list, and remove connector profiles
- malformed JSON fails closed with safe issues
- list/report output redacts secret fields
- resolver precedence: user config wins over env fallback
- resolver env fallback preserves current behavior
- wildcard scopes remain blocked

Workflow tests:

- credential preflight passes from user-config connector scopes
- credential preflight falls back to env when no user config exists
- Brave live-read readiness detects configured `brave.search`
- Telegram subscriber readiness detects configured `telegram.list_subscribers`
- Telegram production readiness detects configured send/broadcast transports
- missing Telegram allowed targets blocks send/broadcast readiness

Transport tests:

- Brave transport is built from resolver config using fake HTTP client
- Telegram list/send/broadcast transports are built from resolver config using
  fake HTTP client
- no transport test performs real network calls

TUI/CLI tests:

- configure command writes redacted connector config through secret input
- list command never prints raw secrets
- remove command deletes connector profile
- guided workflow run can use user-config connectors without connector env vars

## Out of Scope

- OS keychain storage.
- Multi-profile connector accounts.
- Cloud secret managers.
- Hosted worker credential sync.
- New connector package schema.
- Arbitrary browser scraping.
- Connector marketplace UX.
- Releasing npm/GHCR artifacts.

## Future Direction

After the MVP, add a `SecretBackend` abstraction behind the same resolver:

```text
local-json backend -> OS keychain backend -> cloud secret backend
```

That lets appv24 improve secret storage without changing workflow packages,
connector contracts, readiness checks, or live runners.
