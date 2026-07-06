# appv24 Workflow Connector Pack v0

The built-in connector pack is a conservative catalog for workflow design, validation, policy checks, and local dry-run planning.

## Supported Action Classes

Read-oriented examples:

- `source.fetch_document`
- `source.fetch_items`
- `github.list_dependabot_alerts`
- `llm.classify`
- `llm.extract_structured`
- `llm.summarize`
- `policy.check`

Draft/create examples:

- `gmail.create_draft`
- `telegram.create_message_draft`
- `notification.create_draft`
- `ticket.create_draft`

Compatibility examples:

- `telegram.send_message`
- `github.create_issue`

## Registry View

`appv24.workflows.connector_registry.inspect_workflow_connector_registry`
provides the first read-only connector registry view. It joins:

- declared package connector actions from `connectors.yaml`
- built-in catalog metadata from the connector pack
- required scopes and safe credential env hints
- live-mode eligibility
- configured env-gated live transport modes
- uncataloged action issues

The registry does not validate the package, invoke connector transports, call
providers, create workflow run artifacts, or print raw env values. It is the
platform-facing metadata layer for future connector SDK and connector-pack work.

Operators can inspect the same registry from CLI or TUI:

```text
/workflow-connectors <package>
```

The command shows declared actions, catalog status, live-mode eligibility,
configured live transport modes, safe credential env hints, and safe transport
env hints. It must not print raw API keys, OAuth tokens, Telegram bot tokens,
credential-reference values, or provider responses.

## External Effects

Telegram `sendMessage` is mutating. It is represented by `telegram.send_message`, requires approval policy, and live production remains disabled.

Gmail `drafts.create` creates drafts. It maps to `gmail.create_draft` and is draft-eligible only after broker and credential policy are proven.

Gmail `drafts.send` sends externally and remains future-only.

Live-draft v0 only allows catalog entries marked `live_draft_eligible` after
credential preflight, connector allowlisting, current package approval, and
idempotency gates. Compatibility actions such as `telegram.send_message` and
`github.create_issue` remain blocked from live-draft because they are final
external sends/creates, not draft-only actions.

Live production remains disabled by default for connector pack v0. The only
production-enabled action is `telegram.send_message`, and it remains gated by
explicit approval, idempotency, allowlist, credential-reference preflight,
configured transport env, and an explicitly production-enabled broker.

## Vocabulary Boundary

Models must not invent read-only-looking connector actions such as `telegram.receive_webhook`. Incoming webhooks and external events are trigger metadata or fixture input, not executable workflow steps unless the action is explicitly listed in the catalog.
