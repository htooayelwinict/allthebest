from __future__ import annotations

from pathlib import Path

from appv24.workflows.capability_catalog import (
    build_workflow_capability_catalog,
    format_workflow_capability_catalog,
    format_workflow_extension_capability_context,
)
from appv24.workflows.loader import PackageDocuments


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


def test_capability_catalog_includes_custom_connector_hints_from_package() -> None:
    catalog = build_workflow_capability_catalog(documents=_custom_crm_documents())
    by_action = {item.action_ref: item for item in catalog.capabilities}

    assert "crm.lookup_contact" in by_action
    lookup = by_action["crm.lookup_contact"]
    assert lookup.group == "Read data"
    assert lookup.label == "Lookup CRM contact"
    assert lookup.side_effect_class == "read"
    assert lookup.approval_required is False
    assert lookup.output_fields == ("contact", "artifact_path")
    assert lookup.required_env_names == ("APPV24_CRM_MCP_SERVER",)


def test_extension_capability_context_includes_required_input_any_hints() -> None:
    catalog = build_workflow_capability_catalog(documents=_custom_crm_documents())

    rendered = format_workflow_extension_capability_context(catalog)

    assert "required inputs: none; one of: email or customer_id" in rendered


def test_capability_catalog_uses_safe_fallback_for_custom_connector_without_hints() -> None:
    documents = _custom_crm_documents(with_workflow_hints=False)

    catalog = build_workflow_capability_catalog(documents=documents)
    by_action = {item.action_ref: item for item in catalog.capabilities}

    assert by_action["crm.lookup_contact"].group == "Other"
    assert by_action["crm.lookup_contact"].label == "crm.lookup_contact"


def test_capability_catalog_includes_middleware_interfaces() -> None:
    catalog = build_workflow_capability_catalog(documents=_custom_middleware_documents())
    by_middleware = {item.middleware_id: item for item in catalog.middleware_capabilities}

    assert "audit.log" in by_middleware
    assert "team.redactor" in by_middleware
    assert by_middleware["audit.log"].label == "Audit log"
    assert by_middleware["audit.log"].adapter == "builtin"
    assert by_middleware["audit.log"].required_env_names == ("APPV24_WORKFLOW_AUDIT_LOG_PATH",)
    assert by_middleware["team.redactor"].label == "Team redactor"
    assert by_middleware["team.redactor"].adapter == "local"
    assert by_middleware["team.redactor"].required_env_names == ("APPV24_TEAM_REDACTOR_LEVEL",)

    rendered = format_workflow_capability_catalog(catalog)
    assert "Middleware" in rendered
    assert "Team redactor (team.redactor)" in rendered
    assert "env: APPV24_TEAM_REDACTOR_LEVEL" in rendered


def test_extension_capability_context_includes_middleware_interfaces() -> None:
    catalog = build_workflow_capability_catalog(documents=_custom_middleware_documents())

    rendered = format_workflow_extension_capability_context(catalog)

    assert "Middleware interfaces:" in rendered
    assert "Team redactor (team.redactor)" in rendered
    assert "adapter: local" in rendered
    assert "env: APPV24_TEAM_REDACTOR_LEVEL" in rendered


def _custom_crm_documents(*, with_workflow_hints: bool = True) -> PackageDocuments:
    action: dict[str, object] = {
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
    if with_workflow_hints:
        action["workflow_hints"] = {
            "catalog_group": "Read data",
            "catalog_label": "Lookup CRM contact",
            "catalog_description": "Find a customer record by email.",
            "input_fields": ["email", "customer_id"],
            "required_input_any": [["email", "customer_id"]],
        }
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "crm_lookup",
            "description": "Look up CRM contacts.",
            "design": {
                "intent": "Look up CRM contacts.",
                "trigger": "Manual run.",
                "inputs": ["email"],
                "outputs": ["contact"],
                "assumptions": ["CRM connector is configured."],
                "side_effects": [],
                "approval_gates": [],
                "failure_modes": ["CRM unavailable."],
                "rollback": ["No external mutation occurs."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "lookup_contact",
            "steps": [
                {"id": "lookup_contact", "uses": "crm.lookup_contact", "with": {"email": "user@example.com"}, "next": "done"},
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "crm",
                    "adapter": "mcp",
                    "custom": True,
                    "source": "mcp://crm@v1",
                    "env": [
                        {"name": "APPV24_CRM_MCP_SERVER", "purpose": "CRM MCP server endpoint", "required": True}
                    ],
                    "actions": [action],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"email": "user@example.com"}}},
        rollback_text=None,
    )


def _custom_middleware_documents() -> PackageDocuments:
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "redacted_audit",
            "description": "Audit workflow runs with custom redaction middleware.",
            "design": {
                "intent": "Record redacted workflow audit events.",
                "trigger": "Manual run.",
                "inputs": [],
                "outputs": ["audit entry"],
                "assumptions": ["The redactor middleware is installed."],
                "side_effects": ["Local audit log write."],
                "approval_gates": [],
                "failure_modes": ["Middleware unavailable."],
                "rollback": ["Disable the middleware."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "done",
            "steps": [{"id": "done", "type": "terminal"}],
        },
        connectors={"version": "appv24.connectors/v1", "connectors": []},
        permissions={"version": "appv24.permissions/v1", "permissions": []},
        dry_run_fixtures={"happy_path.yaml": {"input": {}}},
        rollback_text=None,
        middlewares={
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "local",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "env": [
                        {
                            "name": "APPV24_TEAM_REDACTOR_LEVEL",
                            "purpose": "redaction level",
                            "required": True,
                        }
                    ],
                    "workflow_hints": {
                        "catalog_label": "Team redactor",
                    },
                }
            ],
        },
    )
