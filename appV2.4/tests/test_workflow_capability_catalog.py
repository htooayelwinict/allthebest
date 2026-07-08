from __future__ import annotations

from appv24.workflows.capability_catalog import (
    build_workflow_capability_catalog,
    format_workflow_capability_catalog,
)


def test_capability_catalog_is_derived_from_connector_contracts() -> None:
    catalog = build_workflow_capability_catalog()
    by_action = {item.action_ref: item for item in catalog.capabilities}

    assert "brave.search" in by_action
    assert "telegram.list_subscribers" in by_action
    assert "telegram.broadcast_message" in by_action

    broadcast = by_action["telegram.broadcast_message"]
    assert broadcast.group == "Draft or send"
    assert broadcast.mode_labels == ("live-production",)
    assert broadcast.side_effect_class == "send"
    assert broadcast.approval_required is True
    assert broadcast.required_env_names == (
        "APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
        "APPV24_TELEGRAM_BOT_TOKEN",
    )
    assert "recipients" in broadcast.input_fields
    assert "sent_count" in broadcast.output_fields


def test_capability_catalog_format_is_user_facing_and_redacted() -> None:
    rendered = format_workflow_capability_catalog(build_workflow_capability_catalog())

    assert "Workflow capability catalog" in rendered
    assert "Search web/news with Brave Search" in rendered
    assert "Broadcast Telegram message to registered subscribers" in rendered
    assert "telegram.broadcast_message" in rendered
    assert "approval: required" in rendered
    assert "env: APPV24_TELEGRAM_ALLOWED_CHAT_IDS, APPV24_TELEGRAM_BOT_TOKEN" in rendered
    assert "telegram-token" not in rendered
    assert "secret" not in rendered.lower()
