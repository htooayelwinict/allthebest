# appv24 Live-Production Telegram V0 Design

## Context

appv24 can already validate workflow packages, compile them to IR, run local and sandbox workflows, persist run artifacts, record approval decisions, run live-read actions, and run approval-gated live-draft actions. `live-production` is still hard-blocked.

The next useful bridge to actual workflow execution is one narrow production connector, not a general production switch.

Official Telegram Bot API source used for this design:

- https://core.telegram.org/bots/api
- `sendMessage` sends text messages and returns the sent message on success.
- Required `sendMessage` parameters are `chat_id` and `text`.
- Bot API requests can use HTTP POST and JSON.

## Goal

Enable a first live-production path for `telegram.send_message` only.

## Non-Goals

- Do not enable general live production.
- Do not enable Gmail sending.
- Do not enable GitHub issue creation.
- Do not execute unknown connector actions.
- Do not bypass approval ledger, credential reference, connector allowlist, catalog, policy, output allowlist, or idempotency gates.
- Do not touch appv24 red-zone/kernel files.

## Design

### Transport

Add `TelegramSendMessageTransport` to `appV2.4/appv24/workflows/live_transports.py`.

It posts to:

```text
{base_url}/bot{token}/sendMessage
```

Required fields:

```text
chat_id
text
```

Field resolution:

1. Step `with` mapping.
2. Fixture `input` mapping.

Accepted aliases:

```text
chat_id, admin_chat_id, recipient
text, message_text, body
```

Allowed optional fields:

```text
parse_mode
disable_notification
protect_content
```

The transport returns only:

```text
message_id
chat_id
```

The broker still filters outputs against the workflow package's declared `outputs.allowed`.

### Environment Gates

Production transports are disabled unless the operator sets:

```text
APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true
APPV24_TELEGRAM_BOT_TOKEN=<token>
```

Optional:

```text
APPV24_TELEGRAM_API_BASE_URL=https://api.telegram.org
```

Token values are never written to run artifacts, terminal output, reports, issues, or events.

### Broker Gate

`LiveWorkflowBroker` keeps production blocked by default. It only routes production transports when constructed with an explicit production flag.

Default behavior remains:

```text
live-production broker is disabled by default
```

### Catalog Gate

Only `telegram.send_message` becomes `live_production_enabled=True`.

No other cataloged mutation becomes production enabled in this slice.

### Production Preflight

Add:

```python
preflight_live_production_workflow(...)
format_live_production_preflight_report(...)
```

The preflight checks:

- package validation
- IR compile
- policy evaluation
- fixture availability
- credential refs
- approval ledger for exact package fingerprint, step id, action, and idempotency key
- connector allowlist
- catalog `live_production_enabled`
- configured live-production transport

It never invokes the transport and never creates a run.

### Production Runner

Add:

```python
run_live_production_workflow(...)
```

It writes normal run artifacts only after validation/compile/policy/fixture setup, then blocks before transport invocation if any gate fails.

Production mutating steps require:

- explicit allowlist
- catalog `live_production_enabled=True`
- exact approved prior local run
- idempotency key
- approval id on the step
- matching configured transport

Read-only steps in a production workflow may run only when they are `live_read_eligible` and allowlisted.

### CLI and TUI

Add read-like commands:

```text
/workflow-live-production-preflight <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
/workflow-run-live-production <package> <fixture> <approval-run-id> --allow <connector.action>[,<connector.action>]
```

These commands do not invoke the model.

### Safety

This is still not a platform-wide production mode. It is one exact connector action behind:

- env gate
- credential ref gate
- human approval gate
- package fingerprint gate
- idempotency gate
- allowlist gate
- catalog gate
- transport registry gate
- output filtering

## Testing

Add tests for:

- production transport disabled by default
- production transport registers only with env gate and token
- Telegram transport posts sanitized JSON to `/bot<TOKEN>/sendMessage`
- missing `chat_id` or `text` blocks without HTTP
- HTTP and malformed response failures are sanitized
- broker keeps production blocked by default
- broker routes production only with explicit flag
- catalog enables only `telegram.send_message`
- live-production preflight passes for approved Telegram send with configured transport
- live-production preflight blocks missing transport
- live-production runner executes approved Telegram send with fake transport
- live-production runner blocks missing approval
- CLI and TUI commands do not invoke the model

## Zone Scope

Green:

- `appV2.4/appv24/workflows/*`
- workflow tests
- docs

Yellow:

- `appV2.4/appv24/cli.py`
- `appV2.4/appv24/tui/interactive_mode.py`

Red:

- no changes.
