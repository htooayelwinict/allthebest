from __future__ import annotations

from pathlib import Path

import yaml

from appv24.workflows.connector_registry import inspect_workflow_connector_registry


def test_connector_registry_joins_package_catalog_and_env_gates(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path)

    report = inspect_workflow_connector_registry(
        package,
        env={
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "secret-token",
            "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat",
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-prod",
            "APPV24_TELEGRAM_SCOPES": "telegram:send",
        },
    )

    assert report.workflow_name == "telegram-production-alert"
    assert report.issues == ()
    assert len(report.actions) == 1
    action = report.actions[0]
    assert action.action_ref == "telegram.send_message"
    assert action.connector_id == "telegram"
    assert action.action_id == "send_message"
    assert action.cataloged is True
    assert action.side_effect is True
    assert action.side_effect_class == "send"
    assert action.required_scopes == ("telegram:send",)
    assert action.allowed_outputs == ("message_id", "chat_id")
    assert action.live_modes == ("live-production",)
    assert action.configured_live_modes == ("live-production",)
    assert action.required_credential_env == ("APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send",)
    assert action.required_transport_env == (
        "live-production: APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
        "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
    )
    assert "secret-token" not in report.to_text()


def test_connector_registry_flags_unknown_declared_action(tmp_path: Path) -> None:
    package = _write_telegram_package(tmp_path, action_ref="telegram.receive_webhook", action_id="receive_webhook")

    report = inspect_workflow_connector_registry(package, env={})

    assert report.workflow_name == "telegram-production-alert"
    assert report.issues == ("action is not cataloged: telegram.receive_webhook",)
    assert len(report.actions) == 1
    action = report.actions[0]
    assert action.action_ref == "telegram.receive_webhook"
    assert action.cataloged is False
    assert action.live_modes == ()
    assert action.configured_live_modes == ()
    assert action.required_transport_env == ()


def test_connector_registry_reports_external_connector_interface(tmp_path: Path) -> None:
    package = _write_external_crm_package(tmp_path)

    report = inspect_workflow_connector_registry(
        package,
        env={
            "APPV24_CRM_MCP_SERVER": "mcp://crm",
            "APPV24_CRM_CREDENTIAL_REF": "crm-prod",
            "APPV24_CRM_SCOPES": "crm:read",
        },
    )

    assert report.issues == ()
    assert len(report.actions) == 1
    action = report.actions[0]
    assert action.action_ref == "crm.lookup_contact"
    assert action.cataloged is True
    assert action.external is True
    assert action.source_ref == "mcp://crm.lookup_contact@v1"
    assert action.execution_mode == "brokered"
    assert action.required_connector_env == ("APPV24_CRM_MCP_SERVER",)
    assert action.required_scopes == ("crm:read",)
    assert action.allowed_outputs == ("contact", "artifact_path")
    assert "crm-prod" not in report.to_text()


def _write_telegram_package(
    root: Path,
    *,
    action_ref: str = "telegram.send_message",
    action_id: str = "send_message",
) -> Path:
    package = root / "telegram-production-alert"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "telegram-production-alert",
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "notify_admin",
                "steps": [
                    {
                        "id": "notify_admin",
                        "uses": action_ref,
                        "side_effect_class": "send",
                        "approval": "admin_approval",
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
                "design": {
                    "intent": "Send one approved Telegram alert.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Telegram credentials are resolved outside the package."],
                    "side_effects": ["send Telegram message"],
                    "approval_gates": ["admin_approval"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Send a corrective message manually."],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": action_id,
                                "operationId": action_id,
                                "side_effect": True,
                                "side_effect_class": "send",
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["message_id", "chat_id"]},
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "telegram", "actions": [action_id], "scopes": ["telegram:send"]}],
                "approvals": [{"id": "admin_approval", "mode": "explicit", "required_for": ["notify_admin"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: alert\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective message manually.\n", encoding="utf-8")
    return package


def _write_external_crm_package(root: Path) -> Path:
    package = root / "external-crm-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "external-crm-read",
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
                "design": {
                    "intent": "Look up CRM contact data.",
                    "trigger": "Manual operator run.",
                    "inputs": ["email"],
                    "outputs": ["contact"],
                    "assumptions": ["CRM MCP server is configured externally."],
                    "side_effects": ["Read-only CRM lookup."],
                    "approval_gates": ["None."],
                    "failure_modes": ["CRM connector unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
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
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  email: customer@example.com\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package
