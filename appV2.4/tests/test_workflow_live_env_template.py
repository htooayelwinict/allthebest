from __future__ import annotations

from pathlib import Path

import yaml

from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_env_template import build_workflow_live_env_template, format_workflow_live_env_template


def test_live_env_template_renders_github_live_read_placeholders(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="dependency-alert-read",
        connector_id="github",
        action_id="list_dependabot_alerts",
        scopes=("dependabot:read",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "Workflow live env template: live-read" in rendered
    assert "workflow: dependency-alert-read" in rendered
    assert "APPV24_GITHUB_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_GITHUB_SCOPES=dependabot:read" in rendered
    assert "APPV24_WORKFLOW_LIVE_READ_ENABLED=true" in rendered
    assert "APPV24_GITHUB_TOKEN=<github-token>" in rendered
    assert not (package / ".workflow-runs").exists()


def test_live_env_template_renders_source_live_read_placeholders(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="public-source-read",
        connector_id="source",
        action_id="fetch_items",
        scopes=("source:read",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("source.fetch_items",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "APPV24_SOURCE_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_SOURCE_SCOPES=source:read" in rendered
    assert "APPV24_WORKFLOW_LIVE_READ_ENABLED=true" in rendered
    assert "APPV24_SOURCE_BASE_URL=<https-source-base-url>" in rendered


def test_live_env_template_renders_brave_search_live_read_placeholders(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="public-brave-search",
        connector_id="brave",
        action_id="search",
        scopes=("brave:search",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("brave.search",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "APPV24_BRAVE_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_BRAVE_SCOPES=brave:search" in rendered
    assert "APPV24_WORKFLOW_LIVE_READ_ENABLED=true" in rendered
    assert "APPV24_BRAVE_SEARCH_API_KEY=<brave-search-api-key>" in rendered
    assert "APPV24_BRAVE_SEARCH_BASE_URL=https://api.search.brave.com/res/v1/web/search" in rendered


def test_live_env_template_renders_gmail_live_draft_token_alternatives(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="gmail-draft",
        connector_id="gmail",
        action_id="create_draft",
        scopes=("gmail.compose",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_DRAFT,
            connector_allowlist=("gmail.create_draft",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "APPV24_GMAIL_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_GMAIL_SCOPES=gmail.compose" in rendered
    assert "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true" in rendered
    assert "APPV24_GMAIL_ACCESS_TOKEN=<oauth-access-token>" in rendered
    assert "APPV24_GMAIL_REFRESH_TOKEN=<oauth-refresh-token>" in rendered
    assert "APPV24_GMAIL_CLIENT_ID=<oauth-client-id>" in rendered
    assert "APPV24_GMAIL_CLIENT_SECRET=<oauth-client-secret>" in rendered


def test_live_env_template_renders_telegram_live_production_placeholders(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="telegram-production-alert",
        connector_id="telegram",
        action_id="send_message",
        scopes=("telegram:send",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            connector_allowlist=("telegram.send_message",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "Workflow live env template: live-production" in rendered
    assert "APPV24_TELEGRAM_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_TELEGRAM_SCOPES=telegram:send" in rendered
    assert "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true" in rendered
    assert "APPV24_TELEGRAM_BOT_TOKEN=<telegram-bot-token>" in rendered


def test_live_env_template_notes_unknown_allowlisted_transport_action(tmp_path: Path) -> None:
    package = _write_package(
        tmp_path,
        name="custom-live-action",
        connector_id="custom",
        action_id="read_items",
        scopes=("custom:read",),
    )

    template = build_workflow_live_env_template(
        WorkflowRunRequest(
            package_path=package,
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("custom.read_items",),
        )
    )

    rendered = format_workflow_live_env_template(template)
    assert "APPV24_CUSTOM_CREDENTIAL_REF=<credential-ref>" in rendered
    assert "APPV24_CUSTOM_SCOPES=custom:read" in rendered
    assert "no built-in live-read transport env template for custom.read_items" in rendered


def _write_package(
    root: Path,
    *,
    name: str,
    connector_id: str,
    action_id: str,
    scopes: tuple[str, ...],
) -> Path:
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": name,
                "description": "Env template test workflow.",
                "design": {
                    "intent": "Render safe live env template.",
                    "trigger": "Manual operator run.",
                    "inputs": [],
                    "outputs": [],
                    "assumptions": ["Credentials are configured outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["Provider unavailable."],
                    "rollback": ["No setup mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "done",
                "steps": [{"id": "done", "type": "terminal"}],
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
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": action_id,
                                "operationId": action_id,
                                "side_effect": False,
                                "outputs": {"allowed": []},
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
                "permissions": [{"connector": connector_id, "actions": [action_id], "scopes": list(scopes)}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input: {}\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo setup mutation occurs.\n", encoding="utf-8")
    return package
