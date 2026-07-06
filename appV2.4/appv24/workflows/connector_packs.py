"""Built-in appv24 workflow connector pack definitions."""

from __future__ import annotations

from appv24.workflows.connector_catalog import ConnectorActionMetadata, ConnectorCatalog


def builtin_connector_catalog() -> ConnectorCatalog:
    actions = (
        ConnectorActionMetadata(
            "source.fetch_document",
            side_effect=False,
            required_scopes=("source:read",),
            allowed_outputs=("document", "title", "url", "metadata"),
            live_read_eligible=True,
        ),
        ConnectorActionMetadata(
            "source.fetch_items",
            side_effect=False,
            required_scopes=("source:read",),
            allowed_outputs=("items", "source_count", "metadata"),
            live_read_eligible=True,
        ),
        ConnectorActionMetadata(
            "llm.classify",
            side_effect=False,
            allowed_outputs=("label", "confidence", "reason"),
        ),
        ConnectorActionMetadata(
            "llm.extract_structured",
            side_effect=False,
            allowed_outputs=("fields", "confidence", "issues"),
        ),
        ConnectorActionMetadata(
            "llm.summarize",
            side_effect=False,
            allowed_outputs=("summary", "citations", "confidence"),
        ),
        ConnectorActionMetadata(
            "policy.check",
            side_effect=False,
            allowed_outputs=("allowed", "reason", "matched_rule"),
        ),
        ConnectorActionMetadata(
            "ticket.create_draft",
            side_effect=True,
            side_effect_class="create",
            required_scopes=("ticket:draft",),
            allowed_outputs=("draft_id", "url", "title"),
            live_draft_eligible=True,
        ),
        ConnectorActionMetadata(
            "notification.create_draft",
            side_effect=True,
            side_effect_class="create",
            required_scopes=("notification:draft",),
            allowed_outputs=("draft_id", "channel", "recipient"),
            live_draft_eligible=True,
        ),
        ConnectorActionMetadata(
            "gmail.create_draft",
            side_effect=True,
            side_effect_class="create",
            required_scopes=("gmail.compose",),
            allowed_outputs=("draft_id", "thread_id"),
            live_draft_eligible=True,
        ),
        ConnectorActionMetadata(
            "telegram.create_message_draft",
            side_effect=True,
            side_effect_class="create",
            required_scopes=("telegram:draft",),
            allowed_outputs=("draft_id", "chat_id", "text"),
            live_draft_eligible=True,
            live_production_enabled=False,
        ),
        ConnectorActionMetadata(
            "telegram.send_message",
            side_effect=True,
            side_effect_class="send",
            required_scopes=("telegram:send",),
            allowed_outputs=("message_id", "chat_id"),
            live_read_eligible=False,
            live_production_enabled=True,
        ),
        ConnectorActionMetadata(
            "github.create_issue",
            side_effect=True,
            side_effect_class="create",
            required_scopes=("github:issues:write",),
            allowed_outputs=("issue_id", "issue_url", "number"),
            live_draft_eligible=False,
            live_production_enabled=False,
        ),
        ConnectorActionMetadata(
            "github.list_dependabot_alerts",
            side_effect=False,
            required_scopes=("github:security_events:read",),
            allowed_outputs=("alerts", "count"),
            live_read_eligible=True,
        ),
    )
    return ConnectorCatalog(actions={action.action_ref: action for action in actions})
