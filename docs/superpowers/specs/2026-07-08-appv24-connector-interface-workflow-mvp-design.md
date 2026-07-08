# appv24 Connector Interface Workflow MVP Design

## Summary

appv24 workflow MVP must let a user drive the workflow profile from the TUI as an end user:

1. Type a natural-language workflow request.
2. Generate a valid workflow package using connector actions shown in the catalog.
3. Save and inspect the package.
4. Run live-read steps.
5. Resume locally to an approval gate.
6. Approve the external side effect.
7. Run approved live-production through connector adapters.

The critical design rule is that workflow runtime owns orchestration only. Connectors own connector-specific interfaces, contracts, target policy, and transport behavior. Workflow must not inherit or embed connector semantics.

## Current Evidence

The AI-news Telegram scenario proved the current TUI path can generate and save a package, but it also exposed the MVP gap:

- The generated package passed validation while using abstract local actions such as `fetch_user_list` and `format_news_digest`.
- `registered users` was modeled as workflow-local behavior instead of a connector interface.
- `brave.search` live-read executed, but generated `connectors.yaml` omitted allowed outputs, so useful provider output was stripped.
- Approval was attached to a manual approval step instead of the production connector step, so live-production readiness did not start at `telegram.send_message`.

The MVP must fix this by making connector interfaces explicit and catalog-driven.

## Architecture Pattern

Use Ports and Adapters with Dependency Inversion.

```text
+---------------------------+
| Workflow Runtime          |
|---------------------------|
| - parse workflow.yaml     |
| - compile step graph      |
| - validate catalog refs   |
| - evaluate approvals      |
| - route BrokerRequest     |
+-------------+-------------+
              |
              v
      <<Connector Port>>
      ConnectorExecutor
      execute(BrokerRequest) -> BrokerResult
              ^
              |
+-------------+-------------+
| Connector Interface Layer |
|---------------------------|
| - action contracts        |
| - input/output schemas    |
| - scopes and secrets      |
| - live mode eligibility   |
| - target policy           |
+-------------+-------------+
              |
              v
+---------------------------+
| Connector Adapters        |
|---------------------------|
| BraveSearchAdapter        |
| LlmAdapter                |
| TelegramAdapter           |
| future adapters           |
+---------------------------+
```

Workflow depends on the connector port and action contracts. Connectors do not inherit from workflow behavior. New connector behavior is added by extending the connector catalog and adapter registry, not by changing workflow semantics.

## Design Principles

### Dependency Inversion

Workflow steps use logical action names such as `brave.search` or `telegram.broadcast_message`. The runner dispatches those names through `BrokerRequest` and `BrokerResult`. It must not call concrete Telegram, Brave, Gmail, or HTTP implementation details directly.

### Interface Segregation

Each connector action has its own contract:

- Input fields.
- Allowed output fields.
- Required scopes.
- Secret refs.
- Live modes.
- Side-effect and approval class.
- Idempotency key fields.
- Target policy.

The workflow runtime validates these contracts but does not own their domain meaning.

### Adapter Pattern

Each live transport adapts a connector contract to a provider API. The adapter validates and marshals provider payloads into the action output contract before returning `BrokerResult`.

### Open/Closed Connector Growth

Adding a connector must require:

1. A connector action contract.
2. Prompt catalog text.
3. Local dry-run artifact behavior if needed.
4. A live adapter if the action is live-enabled.
5. Tests for contract, generation, validation, live gate, and transport.

It must not require workflow runtime special cases for that connector's domain.

## MVP Connector Interfaces

The first smooth path is AI news to Telegram registered users.

The generated workflow should use cataloged connector actions only:

```text
brave.search
-> llm.summarize
-> telegram.list_subscribers
-> telegram.broadcast_message
-> done
```

### `brave.search`

Existing live-read connector.

Required behavior:

- Generated `connectors.yaml` must include allowed top-level outputs: `items`, `source_count`, `query`, and optionally `artifact_path`.
- Workflow generation must not invent nested output fields as top-level allowed outputs.

### `llm.summarize`

Existing local transform connector.

Required behavior:

- Produces a digest text suitable for Telegram.
- Uses `with.output_contract: summary_v1` or a catalog-approved equivalent.
- Does not call external tools or provider APIs from workflow runtime.

### `telegram.list_subscribers`

New connector interface.

Purpose:

- Exposes the subscriber/registered-user list as a Telegram connector action.
- Keeps subscriber semantics out of workflow runtime.

MVP backing:

- The adapter may resolve subscribers from `APPV24_TELEGRAM_ALLOWED_CHAT_IDS`.
- This is adapter implementation detail, not workflow semantics.

Contract:

```text
action: telegram.list_subscribers
side_effect_class: read
live_modes: live-read
inputs: audience, limit
outputs: recipients, recipient_count, audience_hash
required_scopes: telegram:subscribers:read
secrets: APPV24_TELEGRAM_ALLOWED_CHAT_IDS
```

### `telegram.broadcast_message`

New connector interface.

Purpose:

- Sends one approved message to a connector-owned recipient list.
- Keeps fan-out, per-recipient allowlist checks, and provider result shaping inside the Telegram connector.

Contract:

```text
action: telegram.broadcast_message
side_effect_class: send
approval_class: external_send
live_modes: live-production
inputs: recipients, text, parse_mode, disable_notification, protect_content
outputs: sent_count, message_ids, chat_ids
required_scopes: telegram:send
idempotency_key_fields: audience_hash, text
target_policy: every chat id must be allowed by APPV24_TELEGRAM_ALLOWED_CHAT_IDS
```

The workflow must approve `telegram.broadcast_message` directly. It must not approve a separate manual step and then expect production to infer approval for Telegram.

## Workflow Package Shape

Generated workflow packages for the MVP should follow this shape:

```yaml
version: appv24.workflow/v1
name: ai-news-telegram-digest
description: Search recent AI news, summarize it, and send the digest to Telegram subscribers.
triggers:
  - id: manual
    type: manual
entrypoint: search_ai_news
steps:
  - id: search_ai_news
    uses: brave.search
    with:
      query: "artificial intelligence news today"
      freshness: day
      count: 10
    next: summarize_digest
  - id: summarize_digest
    uses: llm.summarize
    with:
      text: "{{steps.search_ai_news.outputs.items}}"
      output_contract: summary_v1
    next: list_subscribers
  - id: list_subscribers
    uses: telegram.list_subscribers
    with:
      audience: default
    next: send_digest
  - id: send_digest
    uses: telegram.broadcast_message
    side_effect_class: send
    approval: approve_telegram_broadcast
    with:
      recipients: "{{steps.list_subscribers.outputs.recipients}}"
      audience_hash: "{{steps.list_subscribers.outputs.audience_hash}}"
      text: "{{steps.summarize_digest.outputs.summary}}"
    next: done
  - id: done
    type: terminal
```

`permissions.yaml` must require approval for `send_digest`, not for an artificial manual approval step.

## TUI MVP Flow

The TUI should guide the operator through one coherent flow:

1. User enters a natural prompt.
2. TUI stages and repairs the workflow draft.
3. `/workflow-save <path>` persists the package.
4. `/workflow-live-plan <path> happy_path.yaml` shows phase sequence and exact next commands.
5. `/workflow-run-live-read-to-approval <path> happy_path.yaml --allow brave.search,telegram.list_subscribers`
6. `/workflow-approvals <path> <approval-run-id>`
7. `/workflow-approve <path> <approval-run-id> <approval-request-id>`
8. `/workflow-live-ready live-production <path> happy_path.yaml <approval-run-id> --allow telegram.broadcast_message`
9. `/workflow-run-live-production <path> happy_path.yaml <approval-run-id> --allow telegram.broadcast_message`

The operator should not need to understand internal handoff files. Run IDs may still be visible in MVP, but reports must clearly identify the next command and why a command is blocked.

## Catalog Scope

The workflow profile prompt must show users the connector scope available in the catalog. When a user asks for behavior outside the catalog, the generated package must either:

- Use a cataloged connector action.
- Represent the need as trigger metadata or fixture input.
- Mark it as a future connector requirement in the design notes.

It must not invent executable local actions that look valid but cannot run live.

## Error Handling

The MVP must produce actionable errors:

- Unknown connector action: name the missing catalog action.
- Missing allowed output: name the connector and expected allowed outputs.
- Missing approval: name the mutating connector step requiring approval.
- Missing transport: name required env keys without printing secret values.
- Target policy failure: say which connector policy blocked execution without exposing tokens.
- Subscriber list empty: block before production send.
- Partial broadcast failure: report sent and failed counts; do not leak raw provider responses.

## Testing Strategy

Add tests before implementation.

Required coverage:

- Connector contract catalog includes `telegram.list_subscribers` and `telegram.broadcast_message`.
- Prompt catalog renders these actions and discourages abstract `fetch_user_list` or `format_news_digest` actions.
- Generated AI-news Telegram draft validates with approval attached to `telegram.broadcast_message`.
- Local dry-run can simulate subscriber listing and broadcast output.
- Live-read-to-approval can execute `brave.search`, resume through `llm.summarize`, run `telegram.list_subscribers`, and stop at broadcast approval.
- Production readiness starts at `telegram.broadcast_message`, not at the workflow entrypoint.
- Telegram broadcast transport blocks empty recipients and recipients outside the allowed target policy.
- Telegram broadcast transport records idempotency by audience hash and message text.
- TUI command output includes copy-pasteable next commands for the golden path.

## Non-Goals

This MVP does not include:

- Telegram `/start` webhook persistence.
- Arbitrary browser scraping.
- A generic workflow `foreach` primitive.
- Cloud deployment.
- npm or GHCR release.
- Kernel/red-zone changes.

The `/start` subscriber registry can be added later as connector actions such as `telegram.register_subscriber` and `telegram.unsubscribe_subscriber`.

## Files Expected To Change Later

Implementation should stay mostly in green-zone workflow code:

- `appV2.4/appv24/workflows/connector_contracts.py`
- `appV2.4/appv24/workflows/prompt_fragments.py`
- `appV2.4/appv24/workflows/live_transports.py`
- `appV2.4/appv24/workflows/local_artifacts.py`
- `appV2.4/appv24/workflows/live_gate.py`
- `appV2.4/appv24/workflows/live_plan.py`
- focused tests under `appV2.4/tests/`

TUI/CLI changes should be limited to command/report ergonomics if the workflow layer cannot produce enough guidance on its own.

## Acceptance Criteria

The MVP is accepted when a user can run the TUI as a human and perform this scenario:

```text
Build me a workflow that scrapes today's AI news, summarizes it, and sends it from my Telegram bot to registered users.
```

The generated package must:

- Use only cataloged executable connector actions.
- Validate and pass quality gate.
- Save from TUI.
- Show a correct live plan.
- Run live-read for real read connectors.
- Produce an approval request for the Telegram production connector step.
- Approve from TUI.
- Run live-production through the Telegram connector interface.
- Either send successfully or block with one clear, actionable connector-interface reason.

## MCP Architecture Check

Open Bridge was consulted for an architecture sanity check. The advice kept for this design:

- Use Ports and Adapters.
- Enforce Dependency Inversion.
- Keep connector-specific behavior behind connector adapters.
- Treat local generation or formatting as explicit catalog interfaces, not implicit workflow behavior.
