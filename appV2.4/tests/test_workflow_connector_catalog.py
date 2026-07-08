from __future__ import annotations

from appv24.workflows.catalog import is_builtin_workflow_action
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.prompt_fragments import render_capability_catalog_fragment


def test_connector_pack_v0_classifies_draft_and_send_actions() -> None:
    catalog = builtin_connector_catalog()

    assert catalog.action("source.fetch_document").live_read_eligible is True
    assert catalog.action("source.fetch_document").side_effect is False
    assert "source_count" in catalog.action("source.fetch_items").allowed_outputs
    assert catalog.action("telegram.create_message_draft").side_effect_class == "create"
    assert catalog.action("telegram.create_message_draft").live_production_enabled is False
    assert catalog.action("telegram.send_message").live_read_eligible is False
    assert catalog.action("telegram.send_message").side_effect_class == "send"
    assert catalog.action("telegram.send_message").live_production_enabled is True
    assert catalog.action("gmail.create_draft").side_effect_class == "create"
    assert catalog.action("gmail.create_draft").live_production_enabled is False
    assert catalog.action("github.create_issue").live_production_enabled is False


def test_catalog_blocks_unknown_read_only_looking_action() -> None:
    assert is_builtin_workflow_action("telegram.create_message_draft")
    assert is_builtin_workflow_action("telegram.send_message")
    assert not is_builtin_workflow_action("telegram.receive_webhook")


def test_capability_prompt_prefers_draft_actions_and_blocks_invented_vocabulary() -> None:
    fragment = render_capability_catalog_fragment()

    assert (
        "Use draft actions only when the user asks for a draft/review-only workflow: "
        "telegram.create_message_draft, gmail.create_draft, notification.create_draft."
    ) in fragment
    assert "Do not invent receive_webhook, send_live, close_ticket, post_chat, or cloud mutation actions." in fragment
