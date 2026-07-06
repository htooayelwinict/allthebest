from __future__ import annotations

import json

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.deployment_intent import deployment_intent_to_dict, deployment_intent_to_json
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent, format_deployment_intent


def test_deployment_intent_includes_runtime_targets_state_and_audit_requirements() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, example.documents)

    assert intent.package_fingerprint.startswith("sha256:")
    assert intent.runtime_modes == ("local", "live-draft", "live-production")
    assert "APPV24_TELEGRAM_BOT_TOKEN" in intent.env_requirements
    assert intent.secret_refs == ("APPV24_TELEGRAM_BOT_TOKEN", "telegram.credential", "ticket.credential")
    assert intent.connector_targets["telegram.send_message"] == ("APPV24_TELEGRAM_ALLOWED_CHAT_IDS",)
    assert intent.idempotency_required_for == ("create_ticket_draft:ticket.create_draft", "notify_admin:telegram.send_message")
    assert "webhook_replay_store" in intent.state_requirements
    assert "events.jsonl" in intent.audit_requirements


def test_deployment_intent_json_shape_is_export_only() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, example.documents)
    payload = deployment_intent_to_dict(intent)
    encoded = deployment_intent_to_json(intent)

    assert payload["version"] == "appv24.deployment_intent/v1"
    assert payload["workflow_name"] == "telegram_support_ticket"
    assert payload["package_fingerprint"] == intent.package_fingerprint
    assert payload["runtime_modes"] == ["local", "live-draft", "live-production"]
    assert payload["connector_targets"]["telegram.send_message"] == ["APPV24_TELEGRAM_ALLOWED_CHAT_IDS"]
    assert json.loads(encoded) == payload
    assert "terraform" not in encoded.lower()
    assert "kubectl" not in encoded.lower()


def test_deployment_intent_text_includes_new_operator_sections() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    compiled = compile_workflow_documents(example.documents)
    assert compiled.ir is not None

    text = format_deployment_intent(export_deployment_intent(compiled.ir, example.documents))

    assert "runtime modes:" in text
    assert "- live-production" in text
    assert "connector targets:" in text
    assert "- telegram.send_message: APPV24_TELEGRAM_ALLOWED_CHAT_IDS" in text
    assert "state requirements:" in text
    assert "audit requirements:" in text
