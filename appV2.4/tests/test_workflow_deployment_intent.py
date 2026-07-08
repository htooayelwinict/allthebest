from __future__ import annotations

import json
from pathlib import Path

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.deployment_intent import deployment_intent_to_dict, deployment_intent_to_json
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.iac_export import export_deployment_intent, format_deployment_intent
from appv24.workflows.loader import PackageDocuments


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
    assert intent.local_runtime_commands == ("/workflow-webhook-serve <max-requests> [host] [port]",)


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
    assert payload["local_runtime_commands"] == ["/workflow-webhook-serve <max-requests> [host] [port]"]
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
    assert "local runtime commands:" in text
    assert "- /workflow-webhook-serve <max-requests> [host] [port]" in text


def test_deployment_intent_includes_scheduler_runtime_command_for_schedule_triggers() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    workflow["triggers"] = [{"id": "daily_check", "type": "schedule"}]
    documents = PackageDocuments(
        root=example.documents.root,
        workflow=workflow,
        connectors=example.documents.connectors,
        permissions=example.documents.permissions,
        dry_run_fixtures=example.documents.dry_run_fixtures,
        rollback_text=example.documents.rollback_text,
    )
    compiled = compile_workflow_documents(documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, documents)

    assert intent.scheduler_triggers == ("daily_check",)
    assert intent.local_runtime_commands == ("/workflow-activation-loop <ticks> [interval-seconds]",)


def test_deployment_intent_includes_middleware_runtime_requirements() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    documents = PackageDocuments(
        root=example.documents.root,
        workflow=example.documents.workflow,
        connectors=example.documents.connectors,
        permissions=example.documents.permissions,
        dry_run_fixtures=example.documents.dry_run_fixtures,
        rollback_text=example.documents.rollback_text,
        middlewares={
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "audit.log",
                    "adapter": "builtin",
                    "source": "catalog://audit.log@v1",
                    "config": {"events": True},
                }
            ],
        },
    )
    compiled = compile_workflow_documents(documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, documents)
    payload = deployment_intent_to_dict(intent)
    text = format_deployment_intent(intent)

    assert intent.middleware_ids == ("audit.log",)
    assert intent.middleware_env_requirements == ("APPV24_WORKFLOW_AUDIT_LOG_PATH",)
    assert "APPV24_WORKFLOW_AUDIT_LOG_PATH" in intent.env_requirements
    assert "middleware:audit.log" in intent.audit_requirements
    assert payload["middleware_ids"] == ["audit.log"]
    assert payload["middleware_env_requirements"] == ["APPV24_WORKFLOW_AUDIT_LOG_PATH"]
    assert "middlewares:" in text
    assert "- audit.log" in text
    assert "middleware env requirements:" in text


def test_deployment_intent_includes_external_connector_env_requirements() -> None:
    documents = _external_crm_documents()
    compiled = compile_workflow_documents(documents)
    assert compiled.ir is not None

    intent = export_deployment_intent(compiled.ir, documents)
    payload = deployment_intent_to_dict(intent)
    text = format_deployment_intent(intent)

    assert intent.connector_env_requirements == ("APPV24_CRM_MCP_SERVER",)
    assert "APPV24_CRM_MCP_SERVER" in intent.env_requirements
    assert payload["connector_env_requirements"] == ["APPV24_CRM_MCP_SERVER"]
    assert "connector env requirements:" in text
    assert "- APPV24_CRM_MCP_SERVER" in text


def _external_crm_documents() -> PackageDocuments:
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "crm_lookup",
            "description": "Look up a CRM contact through an external connector interface.",
            "design": {
                "intent": "Look up a CRM contact by email.",
                "trigger": "Manual support run.",
                "inputs": ["Customer email address."],
                "outputs": ["CRM contact summary."],
                "assumptions": ["The CRM connector interface is versioned and operator configured."],
                "side_effects": ["Read-only CRM lookup."],
                "approval_gates": ["None."],
                "failure_modes": ["CRM connector unavailable."],
                "rollback": ["No external mutation occurs."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "lookup_contact",
            "steps": [
                {
                    "id": "lookup_contact",
                    "uses": "crm.lookup_contact",
                    "with": {"email": {"inputRef": "email"}},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "crm",
                    "adapter": "mcp",
                    "source": "mcp://crm@v1",
                    "env": [
                        {
                            "name": "APPV24_CRM_MCP_SERVER",
                            "purpose": "CRM MCP server endpoint",
                            "required": True,
                        }
                    ],
                    "actions": [
                        {
                            "id": "lookup_contact",
                            "custom": True,
                            "source": "mcp://crm.lookup_contact@v1",
                            "tool": "crm.lookup_contact",
                            "side_effect": False,
                            "side_effect_class": "read",
                            "execution_mode": "brokered",
                            "dry_run_supported": True,
                            "required_scopes": ["crm:read"],
                            "outputs": {"allowed": ["contact", "artifact_path"]},
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"email": "customer@example.com"}}},
        rollback_text=None,
    )
