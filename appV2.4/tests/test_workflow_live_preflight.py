from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.live_preflight import (
    format_live_draft_preflight_report,
    format_live_production_preflight_report,
    format_live_read_preflight_report,
    preflight_live_draft_workflow,
    preflight_live_production_workflow,
    preflight_live_read_workflow,
)
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import run_workflow


def test_live_draft_preflight_passes_for_approved_draft_action(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "passed"
    assert report.workflow_name == "gmail_draft_reply"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == ()
    assert "Live-draft preflight: passed" in format_live_draft_preflight_report(report)


def test_live_draft_preflight_blocks_non_draft_eligible_action(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=True,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("ticket.create_draft", "telegram.send_message"),
        ),
        credential_resolver=CredentialRefResolver(
            {
                "ticket": CredentialScope(available=True, scopes=("ticket:draft",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
            }
        ),
    )

    assert report.status == "blocked"
    assert "live-draft action is not draft-eligible: telegram.send_message" in report.issues
    assert report.checked_actions == ("ticket.create_draft", "telegram.send_message")


def test_live_draft_preflight_blocks_missing_approval_run_id(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-draft requires approval_run_id",)


def test_live_draft_preflight_blocks_missing_configured_transport(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == (
        "live-draft transport is not configured for gmail.create_draft; required env: "
        "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or "
        "APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET",
    )


def test_live_draft_preflight_passes_with_configured_transport(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
        available_transports=("gmail.create_draft",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == ()


def test_live_read_preflight_passes_for_allowlisted_read_action(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "passed"
    assert report.workflow_name == "dependency-alert-read"
    assert report.checked_actions == ("github.list_dependabot_alerts",)
    assert report.issues == ()
    assert "Live-read preflight: passed" in format_live_read_preflight_report(report)


def test_live_read_preflight_blocks_missing_configured_transport(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("github.list_dependabot_alerts",)
    assert report.issues == (
        "live-read transport is not configured for github.list_dependabot_alerts; required env: "
        "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
    )


def test_live_read_preflight_reports_source_transport_env_hint(tmp_path: Path) -> None:
    package = _write_source_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("source.fetch_items",),
        ),
        credential_resolver=CredentialRefResolver({"source": CredentialScope(available=True, scopes=("source:read",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("source.fetch_items",)
    assert report.issues == (
        "live-read transport is not configured for source.fetch_items; required env: "
        "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
    )


def test_live_read_preflight_passes_with_configured_transport(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
        available_transports=("github.list_dependabot_alerts",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("github.list_dependabot_alerts",)
    assert report.issues == ()


def test_live_read_preflight_blocks_mutating_steps(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == ("live-read cannot execute mutating step create_gmail_draft",)


def test_live_read_preflight_blocks_missing_allowlist(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("github.other",),
        ),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("github.list_dependabot_alerts",)
    assert report.issues == ("live-read action is not allowlisted: github.list_dependabot_alerts",)


def test_live_production_preflight_passes_for_approved_telegram_send(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "passed"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ()
    assert "Live-production preflight: passed" in format_live_production_preflight_report(report)


def test_live_production_preflight_blocks_non_local_approval_run(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    _set_package_run_mode(package, approval_report.run_id, "preview")

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "blocked"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ()
    assert report.issues == ("live-production requires approval_run_id from a local run",)


def test_live_production_preflight_blocks_approval_for_different_resolved_inputref_fixture(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="alternate_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "blocked"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ("approval not granted for notify_admin",)


def test_live_production_preflight_blocks_missing_configured_transport(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=(),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == (
        "live-production transport is not configured for telegram.send_message; required env: "
        "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, APPV24_TELEGRAM_BOT_TOKEN",
    )


def test_live_production_preflight_blocks_missing_approval_run_id(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            connector_allowlist=("telegram.send_message",),
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-production requires approval_run_id",)


def test_live_production_preflight_blocks_non_production_enabled_mutation(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    _approve_all_requests(package, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = preflight_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
        available_transports=("gmail.create_draft",),
    )

    assert report.status == "blocked"
    assert report.checked_actions == ("gmail.create_draft",)
    assert report.issues == ("live-production action is not production-enabled: gmail.create_draft",)


def _write_package(root: Path, example_name: str) -> Path:
    example = golden_workflow_examples()[example_name]
    package = root / example_name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    if example.documents.rollback_text is not None:
        (package / "rollback.md").write_text(example.documents.rollback_text, encoding="utf-8")
    return package


def _write_read_only_package(root: Path) -> Path:
    package = root / "dependency-alert-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "dependency-alert-read",
                "description": "Read GitHub dependency alerts through a live-read connector gate.",
                "design": {
                    "intent": "Read dependency alerts for operator review.",
                    "trigger": "Manual operator run.",
                    "inputs": ["repo"],
                    "outputs": ["alerts"],
                    "assumptions": ["GitHub credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["GitHub API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "inspect_alerts",
                "steps": [
                    {"id": "inspect_alerts", "uses": "github.list_dependabot_alerts", "next": "done"},
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
                        "id": "github",
                        "adapter": "openapi",
                        "actions": [
                            {
                                "id": "list_dependabot_alerts",
                                "operationId": "dependabot_list_alerts",
                                "side_effect": False,
                                "outputs": {"allowed": ["alerts"]},
                                "required_scopes": ["dependabot:read"],
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
                "permissions": [{"connector": "github", "actions": ["list_dependabot_alerts"], "scopes": ["dependabot:read"]}],
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


def _write_source_read_only_package(root: Path) -> Path:
    package = root / "source-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "source-read",
                "description": "Read public source items through a live-read connector gate.",
                "design": {
                    "intent": "Read public source items for review.",
                    "trigger": "Manual operator run.",
                    "inputs": ["query"],
                    "outputs": ["items"],
                    "assumptions": ["Source credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["Source API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "fetch_items",
                "steps": [
                    {"id": "fetch_items", "uses": "source.fetch_items", "with": {"query": "public catalysts"}, "next": "done"},
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
                        "id": "source",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "fetch_items",
                                "operationId": "source_fetch_items",
                                "side_effect": False,
                                "outputs": {"allowed": ["items", "source_count"]},
                                "required_scopes": ["source:read"],
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
                "permissions": [{"connector": "source", "actions": ["fetch_items"], "scopes": ["source:read"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input: {}\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_telegram_send_package(root: Path) -> Path:
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
                        "with": {
                            "chat_id": {"inputRef": "chat_id"},
                            "text": {"inputRef": "text"},
                        },
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
    (fixtures / "happy_path.yaml").write_text(
        "input:\n  chat_id: admin-chat\n  text: Production alert.\n",
        encoding="utf-8",
    )
    (fixtures / "alternate_path.yaml").write_text(
        "input:\n  chat_id: alternate-admin-chat\n  text: Production alert for another chat.\n",
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        "expected:\n  status: rejected\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nDelete the Telegram message manually if required.\n", encoding="utf-8")
    return package


def _approve_all_requests(package: Path, run_id: str, *, package_fingerprint: str) -> None:
    store = WorkflowRunStore(package / ".workflow-runs")
    for request in store.read_approval_requests(run_id):
        store.record_approval_decision(
            run_id,
            request.approval_request_id,
            ApprovalDecision(approved=True, package_fingerprint=package_fingerprint, decided_by="test"),
        )


def _set_package_run_mode(package: Path, run_id: str, mode: str) -> None:
    path = package / ".workflow-runs" / run_id / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mode"] = mode
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
