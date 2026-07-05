# appv24 Workflow Run Preview QA

Date: 2026-07-05
Scope: appV2.4 workflow-designer and dry-run workflow preview only

## Boundary

These scenarios are repeatable QA inputs for appv24 workflow package generation and dry-run preview.

They are not live-provider automation tests. Live model/TUI checks remain manual-gated because they require provider keys and cost money. Connector execution remains dry-run only: no Gmail, Telegram, ticketing, GitHub, cloud, shell, MCP, or webhook side effects are allowed by this QA layer.

## Scenario 1: Gmail Draft Reply

Prompt:

```text
Create a workflow that reviews a customer escalation email, decides whether a reply is needed, and creates a Gmail draft reply for human approval. It must not send the email. Include happy_path.yaml and rejection_path.yaml fixtures.
```

Expected package intent:

```text
manual customer escalation review
  -> decide whether a reply is needed
  -> create Gmail draft only when needed
  -> require human_approval
  -> terminal done
```

Expected dry-run preview:

```text
happy_path.yaml
  terminal_step: done
  brokered action: gmail.create_draft
  approval required: human_approval
  idempotency key fields: source_message_id, recipient
  allowed outputs: draft_id
  simulated outputs: draft_id

rejection_path.yaml
  terminal_step: done
  no brokered actions
  no approval ledger events
```

Golden fixture:

```text
appV2.4/appv24/workflows/examples.py
  gmail_draft_reply
```

## Scenario 2: Authorized Telegram Support Ticket

Prompt:

```text
Create a workflow for a Telegram bot that accepts a customer support message, checks that the customer id is authorized, creates a ticket draft, and sends an admin notification. Unauthorized customers must be rejected. Include happy_path.yaml and rejection_path.yaml fixtures.
```

Expected package intent:

```text
Telegram inbound message
  -> check authorized customer id
  -> create ticket draft
  -> notify admin
  -> terminal done
```

Expected dry-run preview:

```text
happy_path.yaml
  terminal_step: done
  brokered actions: ticket.create_draft, telegram.send_message
  approval required: admin_approval
  idempotency key fields:
    ticket.create_draft: telegram_user_id, message_text
    telegram.send_message: ticket_id, admin_chat_id
  allowed outputs:
    ticket.create_draft: ticket_id
    telegram.send_message: message_id
  simulated outputs:
    ticket_id
    message_id

rejection_path.yaml
  terminal_step: rejected
  no brokered actions
  no approval ledger events
```

Golden fixture:

```text
appV2.4/appv24/workflows/examples.py
  telegram_support_ticket
```

## Offline Verification

Focused tests:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=appV2.4 .venv/bin/python -m pytest -p no:cacheprovider \
  appV2.4/tests/test_workflow_examples.py \
  appV2.4/tests/test_workflow_run_preview.py \
  appV2.4/tests/test_workflow_ir.py::test_policy_blocks_unknown_catalog_action_even_when_read_only
```

The preview output must include:

```text
step trace
terminal step
brokered actions
approvals required
idempotency key fields
allowed outputs
approval ledger preview events
simulated outputs
```
