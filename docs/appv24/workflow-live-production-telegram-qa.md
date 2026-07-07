# Telegram Live-Production QA

This runbook proves the appv24 workflow path from package validation to a real, approval-gated Telegram send. It keeps production execution behind local approval, credential preflight, operator allowlist, and the live-production environment gate.

## Package

Use the repeatable example package:

```text
examples/appv24/workflows/telegram-production-alert
```

Before a real send, copy or edit the sample `chat_id` and `text` in `workflow.yaml`. Do not commit real chat ids if they are sensitive.

## Environment

Set these only in the operator shell that will run the live-production test:

```bash
export APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true
export APPV24_TELEGRAM_BOT_TOKEN='<telegram-bot-token>'
export APPV24_TELEGRAM_ALLOWED_CHAT_IDS='<admin-or-test-chat-id>'
export APPV24_TELEGRAM_CREDENTIAL_REF='telegram-bot-prod'
export APPV24_TELEGRAM_SCOPES='telegram:send'
```

## Manual Gate Flow

Start with a local run. This creates an approval request and no external side effects:

```text
/workflow-run-local examples/appv24/workflows/telegram-production-alert happy_path.yaml
```

List pending approvals from the local approval run:

```text
/workflow-approvals examples/appv24/workflows/telegram-production-alert <approval-run-id>
```

Approve the specific request after checking the step, connector action, idempotency key, and allowed outputs:

```text
/workflow-approve examples/appv24/workflows/telegram-production-alert <approval-run-id> <approval-request-id>
```

Preflight the production send. This still performs no external API call:

```text
/workflow-live-production-preflight examples/appv24/workflows/telegram-production-alert happy_path.yaml <approval-run-id> --allow telegram.send_message
```

Run the production send only after preflight passes:

```text
/workflow-run-live-production examples/appv24/workflows/telegram-production-alert happy_path.yaml <approval-run-id> --allow telegram.send_message
```

Inspect the saved run afterward:

```text
/workflow-run-explain examples/appv24/workflows/telegram-production-alert <live-run-id>
```

## Expected Boundary

- Local run writes artifacts and approval ledger only.
- Preflight blocks missing credentials, missing approval, missing transport, non-production-enabled actions, and omitted allowlist actions.
- Production run sends only `telegram.send_message`.
- Saved report exposes only allowed outputs: `message_id` and `chat_id`.
- Bot token values must never appear in package files, run reports, approval ledgers, or terminal output.
