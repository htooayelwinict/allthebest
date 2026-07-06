from __future__ import annotations

from pathlib import Path

import yaml

from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_readiness import format_workflow_live_readiness_report, inspect_workflow_live_readiness


def test_live_readiness_reports_missing_live_read_setup_without_leaking_env_values(tmp_path: Path) -> None:
    package = _write_live_read_workflow_package(tmp_path)

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        env={"APPV24_GITHUB_TOKEN": "ghp_secret_value"},
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "blocked"
    assert "Workflow live readiness: blocked" in rendered
    assert "mode: live-read" in rendered
    assert "- missing credential ref for github; required env: APPV24_GITHUB_CREDENTIAL_REF, APPV24_GITHUB_SCOPES=dependabot:read" in rendered
    assert "- APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN" in rendered
    assert "ghp_secret_value" not in rendered
    assert not (package / ".workflow-runs").exists()


def test_live_readiness_passes_with_configured_live_read_setup(tmp_path: Path) -> None:
    package = _write_live_read_workflow_package(tmp_path)

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        env={
            "APPV24_GITHUB_CREDENTIAL_REF": "github-prod",
            "APPV24_GITHUB_SCOPES": "dependabot:read",
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_GITHUB_TOKEN": "ghp_secret_value",
        },
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "ready"
    assert report.configured_transports == ("github.list_dependabot_alerts",)
    assert "Workflow live readiness: ready" in rendered
    assert "configured transports:" in rendered
    assert "- github.list_dependabot_alerts" in rendered
    assert "ghp_secret_value" not in rendered
    assert not (package / ".workflow-runs").exists()


def test_live_readiness_passes_with_configured_source_live_read_setup(tmp_path: Path) -> None:
    package = _write_live_read_workflow_package(
        tmp_path,
        name="source-read",
        connector_id="source",
        action_id="fetch_items",
        scopes=("source:read",),
        action_ref="source.fetch_items",
        outputs=("items", "source_count"),
    )

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("source.fetch_items",),
        ),
        env={
            "APPV24_SOURCE_CREDENTIAL_REF": "source-prod",
            "APPV24_SOURCE_SCOPES": "source:read",
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_SOURCE_BASE_URL": "https://source.test/api/items",
        },
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "ready"
    assert report.configured_transports == ("source.fetch_document", "source.fetch_items")
    assert "- APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL" in rendered
    assert "https://source.test" not in rendered
    assert not (package / ".workflow-runs").exists()


def test_live_readiness_reports_production_approval_and_transport_setup(tmp_path: Path) -> None:
    package = _write_live_production_workflow_package(tmp_path)

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            connector_allowlist=("telegram.send_message",),
        ),
        env={},
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "blocked"
    assert "mode: live-production" in rendered
    assert "- live-production requires approval_run_id" in rendered
    assert "- missing credential ref for telegram; required env: APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send" in rendered
    assert "- APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN" in rendered
    assert "telegram-token" not in rendered
    assert not (package / ".workflow-runs").exists()


def _write_live_read_workflow_package(
    root: Path,
    *,
    name: str = "dependency-alert-read",
    connector_id: str = "github",
    action_id: str = "list_dependabot_alerts",
    scopes: tuple[str, ...] = ("dependabot:read",),
    action_ref: str = "github.list_dependabot_alerts",
    outputs: tuple[str, ...] = ("alerts",),
) -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": name,
                "description": "Read items through a live-read connector gate.",
                "design": {
                    "intent": "Read public items for operator review.",
                    "trigger": "Manual operator run.",
                    "inputs": ["source"],
                    "outputs": ["items"],
                    "assumptions": ["Credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["Source API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "inspect_alerts",
                "steps": [
                    {"id": "inspect_alerts", "uses": action_ref, "next": "done"},
                    {"id": "done", "type": "terminal"},
                ],
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
                        "id": connector_id,
                        "adapter": "openapi",
                        "actions": [
                            {
                                "id": action_id,
                                "operationId": action_id,
                                "side_effect": False,
                                "outputs": {"allowed": list(outputs)},
                                "required_scopes": list(scopes),
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
                "permissions": [
                    {
                        "connector": connector_id,
                        "actions": [action_id],
                        "scopes": list(scopes),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_live_production_workflow_package(root: Path) -> Path:
    package = root / "telegram-production-alert"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "telegram-production-alert",
                "description": "Send one approved Telegram alert.",
                "design": {
                    "intent": "Send one approved Telegram alert to an admin chat.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Telegram bot token is resolved outside the package."],
                    "side_effects": ["send Telegram message"],
                    "approval_gates": ["admin_approval"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Delete the Telegram message manually if required."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "notify_admin",
                "steps": [
                    {
                        "id": "notify_admin",
                        "uses": "telegram.send_message",
                        "side_effect_class": "send",
                        "approval": "admin_approval",
                        "with": {"chat_id": {"inputRef": "chat_id"}, "text": {"inputRef": "text"}},
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
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
                                "id": "send_message",
                                "operationId": "telegram_send_message",
                                "execution_mode": "brokered",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["message_id", "chat_id"]},
                                "idempotency": {"key_fields": ["chat_id", "text"]},
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
                "permissions": [{"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]}],
                "approvals": [{"id": "admin_approval", "mode": "explicit", "required_for": ["notify_admin"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: Production alert.\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nDelete the Telegram message manually if required.\n", encoding="utf-8")
    return package
