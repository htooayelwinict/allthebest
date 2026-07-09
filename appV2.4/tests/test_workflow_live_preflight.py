from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.bundle_export import write_workflow_bundle
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


def test_live_read_preflight_accepts_external_connector_with_configured_transport(tmp_path: Path) -> None:
    package = _write_external_crm_read_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("crm.lookup_contact",),
        ),
        credential_resolver=CredentialRefResolver({"crm": CredentialScope(available=True, scopes=("crm:read",))}),
        available_transports=("crm.lookup_contact",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("crm.lookup_contact",)
    assert report.issues == ()


def test_live_read_preflight_treats_local_transform_after_read_prefix_as_phase_boundary(tmp_path: Path) -> None:
    package = _write_source_then_llm_package(tmp_path)

    report = preflight_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("source.fetch_items",),
        ),
        credential_resolver=CredentialRefResolver(
            {
                "source": CredentialScope(available=True, scopes=("source:read",)),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        available_transports=("source.fetch_items",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("source.fetch_items",)
    assert report.issues == ()


def test_live_read_preflight_blocks_stale_bundle_before_transport_check(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    write_workflow_bundle(package)
    (package / "rollback.md").write_text("# Rollback\n\nChanged after bundle export.\n", encoding="utf-8")

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

    assert report.status == "blocked"
    assert report.checked_actions == ()
    assert any(issue.startswith("workflow bundle check failed: source package fingerprint changed") for issue in report.issues)


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
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "passed"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ()
    assert "Live-production preflight: passed" in format_live_production_preflight_report(report)


def test_live_production_preflight_starts_at_approved_action_after_read_prefix(tmp_path: Path) -> None:
    package = _write_read_then_telegram_send_package(tmp_path)
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
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        credential_resolver=CredentialRefResolver(
            {
                "github": CredentialScope(available=True, scopes=("dependabot:read",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
            }
        ),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ()


def test_live_production_preflight_resolves_broadcast_recipients_from_approval_run_outputs(tmp_path: Path) -> None:
    package = _write_ai_news_telegram_broadcast_package(tmp_path)
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
            connector_allowlist=("telegram.broadcast_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "local-telegram-subscriber"},
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(
                    available=True,
                    scopes=("telegram:send", "telegram:subscribers:read"),
                ),
            }
        ),
        available_transports=("telegram.broadcast_message",),
    )

    assert report.status == "passed"
    assert report.checked_actions == ("telegram.broadcast_message",)
    assert report.issues == ()


def test_live_production_preflight_accepts_qa_passed_custom_connector_after_read_prefix(tmp_path: Path) -> None:
    package = _write_read_then_external_crm_send_package(tmp_path)
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
            connector_allowlist=("crm.send_customer_update",),
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "crm": CredentialScope(available=True, scopes=("crm:send",)),
            }
        ),
        available_transports=("crm.send_customer_update",),
    )

    assert report.status == "passed"
    assert report.workflow_name == "read-then-external-crm-send"
    assert report.checked_actions == ("crm.send_customer_update",)
    assert report.issues == ()


def test_live_production_preflight_blocks_missing_telegram_allowed_chat_ids(tmp_path: Path) -> None:
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

    assert report.status == "blocked"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ("telegram.send_message requires APPV24_TELEGRAM_ALLOWED_CHAT_IDS",)


def test_live_production_preflight_blocks_disallowed_telegram_target_from_operator_env(tmp_path: Path) -> None:
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
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "other-admin"},
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
        available_transports=("telegram.send_message",),
    )

    assert report.status == "blocked"
    assert report.workflow_name == "telegram-production-alert"
    assert report.checked_actions == ("telegram.send_message",)
    assert report.issues == ("telegram.send_message chat_id is not allowlisted: admin-chat",)


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


def test_live_production_preflight_blocks_stale_payload_hash_approval(tmp_path: Path) -> None:
    package = _write_telegram_send_package(tmp_path)
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    request = WorkflowRunStore(package / ".workflow-runs").read_approval_requests(approval_report.run_id)[0]
    _append_raw_approval_decision(
        package,
        approval_report.run_id,
        request,
        package_fingerprint=approval_report.package_fingerprint,
        payload_hash="payload:stale",
    )

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
    assert report.checked_actions == ()
    assert report.issues == ("approval payload hash is stale for notify_admin",)


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
        "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
        "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
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


def _write_source_then_llm_package(root: Path) -> Path:
    package = root / "source-then-llm"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "source-then-llm",
                "description": "Read source items live, then transform them locally.",
                "design": {
                    "intent": "Read source items and summarize them locally.",
                    "trigger": "Manual operator run.",
                    "inputs": ["query"],
                    "outputs": ["summary"],
                    "assumptions": ["Source credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["Source API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "fetch_items",
                "steps": [
                    {"id": "fetch_items", "uses": "source.fetch_items", "with": {"query": "public catalysts"}, "next": "summarize"},
                    {
                        "id": "summarize",
                        "uses": "llm.summarize",
                        "with": {"text": "{{ steps.fetch_items.outputs.items }}", "output_contract": "summary_v1"},
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
                    },
                    {
                        "id": "llm",
                        "adapter": "llm",
                        "actions": [
                            {
                                "id": "summarize",
                                "side_effect": False,
                                "outputs": {"allowed": ["summary", "raw_response"]},
                            }
                        ],
                    },
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
                    {"connector": "source", "actions": ["fetch_items"], "scopes": ["source:read"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                ],
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


def _write_external_crm_read_package(root: Path) -> Path:
    package = root / "external-crm-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "external-crm-read",
                "description": "Read CRM contact data through an external connector interface.",
                "design": {
                    "intent": "Look up one CRM contact by email.",
                    "trigger": "Manual operator run.",
                    "inputs": ["Customer email address."],
                    "outputs": ["CRM contact fields."],
                    "assumptions": ["CRM MCP server is configured by the operator."],
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
                                "tool": "crm.lookup_contact",
                                "side_effect": False,
                                "side_effect_class": "read",
                                "execution_mode": "brokered",
                                "dry_run_supported": True,
                                "outputs": {"allowed": ["contact", "artifact_path"]},
                                "required_scopes": ["crm:read"],
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
    (fixtures / "happy_path.yaml").write_text(
        yaml.safe_dump({"input": {"email": "customer@example.com"}}, sort_keys=False),
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_read_then_telegram_send_package(root: Path) -> Path:
    package = root / "read-then-telegram-production"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "read-then-telegram-production",
                "description": "Read live context, then send one approved Telegram message.",
                "design": {
                    "intent": "Read upstream context before an approved Telegram send.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Live-read already handled the upstream read connector."],
                    "side_effects": ["send Telegram message"],
                    "approval_gates": ["admin_approval"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Delete the Telegram message manually if required."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "inspect_alerts",
                "steps": [
                    {"id": "inspect_alerts", "uses": "github.list_dependabot_alerts", "next": "notify_admin"},
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
                    },
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
                    },
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
                    {"connector": "github", "actions": ["list_dependabot_alerts"], "scopes": ["dependabot:read"]},
                    {"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]},
                ],
                "approvals": [{"id": "admin_approval", "mode": "explicit", "required_for": ["notify_admin"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        "input:\n  chat_id: admin-chat\n  text: Production alert after read.\n",
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        "expected:\n  status: rejected\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nDelete the Telegram message manually if required.\n", encoding="utf-8")
    return package


def _write_ai_news_telegram_broadcast_package(root: Path) -> Path:
    package = root / "ai-news-telegram-broadcast"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "ai-news-telegram-broadcast",
                "description": "Search AI news and broadcast a digest to registered Telegram subscribers.",
                "design": {
                    "intent": "Broadcast an AI news digest to registered users.",
                    "trigger": "Manual operator run.",
                    "inputs": [],
                    "outputs": ["sent_count", "message_ids", "chat_ids"],
                    "assumptions": ["Telegram subscriber registry is connector-owned."],
                    "side_effects": ["send Telegram messages"],
                    "approval_gates": ["approve_telegram_broadcast"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Send a corrective Telegram message manually if required."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_ai_news",
                "steps": [
                    {
                        "id": "search_ai_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 5},
                        "next": "summarize_news",
                    },
                    {
                        "id": "summarize_news",
                        "uses": "llm.summarize",
                        "with": {
                            "items": "{{ steps.search_ai_news.outputs.items }}",
                            "output_contract": "summary_v1",
                        },
                        "next": "list_subscribers",
                    },
                    {
                        "id": "list_subscribers",
                        "uses": "telegram.list_subscribers",
                        "with": {"audience": "registered_users"},
                        "next": "send_digest",
                    },
                    {
                        "id": "send_digest",
                        "uses": "telegram.broadcast_message",
                        "side_effect_class": "send",
                        "approval": "approve_telegram_broadcast",
                        "with": {
                            "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                            "audience_hash": "{{ steps.list_subscribers.outputs.audience_hash }}",
                            "text": "{{ steps.summarize_news.outputs.summary }}",
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
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query", "artifact_path"]},
                            }
                        ],
                    },
                    {
                        "id": "llm",
                        "adapter": "llm",
                        "actions": [
                            {
                                "id": "summarize",
                                "side_effect": False,
                                "outputs": {"allowed": ["summary", "citations", "confidence", "raw_response", "artifact_path"]},
                            }
                        ],
                    },
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "list_subscribers",
                                "side_effect": False,
                                "required_scopes": ["telegram:subscribers:read"],
                                "outputs": {"allowed": ["recipients", "recipient_count", "audience_hash", "artifact_path"]},
                            },
                            {
                                "id": "broadcast_message",
                                "operationId": "telegram_broadcast_message",
                                "execution_mode": "brokered",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids", "artifact_path"]},
                                "idempotency": {"key_fields": ["audience_hash", "text"]},
                            },
                        ],
                    },
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
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                    {
                        "connector": "telegram",
                        "actions": ["list_subscribers", "broadcast_message"],
                        "scopes": ["telegram:subscribers:read", "telegram:send"],
                    },
                ],
                "approvals": [
                    {
                        "id": "approve_telegram_broadcast",
                        "mode": "explicit",
                        "required_for": ["send_digest"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("expected:\n  status: rejected\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective Telegram message if required.\n", encoding="utf-8")
    return package


def _write_read_then_external_crm_send_package(root: Path) -> Path:
    package = root / "read-then-external-crm-send"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "read-then-external-crm-send",
                "description": "Read live context, then send one approved CRM customer update.",
                "design": {
                    "intent": "Read current context before an approved CRM customer send.",
                    "trigger": "Manual operator run.",
                    "inputs": ["customer_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["The CRM send extension has passed QA."],
                    "side_effects": ["send CRM customer update"],
                    "approval_gates": ["approve_crm_send"],
                    "failure_modes": ["CRM connector unavailable."],
                    "rollback": ["Send a corrective CRM update manually."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_news",
                "steps": [
                    {
                        "id": "search_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 3},
                        "next": "send_customer_update",
                    },
                    {
                        "id": "send_customer_update",
                        "uses": "crm.send_customer_update",
                        "side_effect_class": "send",
                        "approval": "approve_crm_send",
                        "with": {
                            "customer_id": {"inputRef": "customer_id"},
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
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query", "artifact_path"]},
                            }
                        ],
                    },
                    {
                        "id": "crm",
                        "adapter": "mcp",
                        "custom": True,
                        "source": "mcp://crm@v1",
                        "actions": [
                            {
                                "id": "send_customer_update",
                                "custom": True,
                                "source": "mcp://crm.send_customer_update@v1",
                                "tool": "crm.send_customer_update",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "live_modes": ["live-production"],
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["crm:send"],
                                "idempotency": {"key_fields": ["customer_id", "text"]},
                                "outputs": {"allowed": ["message_id", "artifact_path"]},
                                "workflow_hints": {
                                    "business_goal": "Send customer updates through CRM.",
                                    "catalog_group": "Draft or send",
                                    "catalog_label": "Send CRM customer update",
                                    "generated_by": "coding_profile",
                                    "input_fields": ["customer_id", "text"],
                                    "qa_required": True,
                                    "required_input_fields": ["customer_id", "text"],
                                    "required_target_fields": ["customer_id"],
                                },
                                "qa": {
                                    "version": "appv24.extension_qa/v1",
                                    "kind": "connector",
                                    "extension_id": "crm.send_customer_update",
                                    "source": "mcp://crm.send_customer_update@v1",
                                    "status": "passed",
                                    "qa": {
                                        "schema_validation": "passed",
                                        "tests": [
                                            {
                                                "command": "PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_crm_send_connector.py",
                                                "status": "passed",
                                            }
                                        ],
                                        "side_effect_policy": "passed",
                                        "runtime_boundary": "passed",
                                        "adapter_contract": "passed",
                                    },
                                    "contract": {
                                        "required_scopes": ["crm:send"],
                                        "allowed_outputs": ["message_id", "artifact_path"],
                                    },
                                    "fingerprints": {
                                        "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                                        "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                                        "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
                                        "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
                                    },
                                },
                            }
                        ],
                    },
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
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "crm", "actions": ["send_customer_update"], "scopes": ["crm:send"]},
                ],
                "approvals": [{"id": "approve_crm_send", "mode": "explicit", "required_for": ["send_customer_update"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        "input:\n  customer_id: customer-1\n  text: Customer update.\n",
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text("expected:\n  status: rejected\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective CRM update manually.\n", encoding="utf-8")
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


def _append_raw_approval_decision(
    package: Path,
    run_id: str,
    request: object,
    *,
    package_fingerprint: str,
    payload_hash: str,
) -> None:
    path = package / ".workflow-runs" / run_id / "approval_decisions.jsonl"
    payload = {
        "approval_request_id": request.approval_request_id,
        "run_id": run_id,
        "approval_id": request.approval_id,
        "step_id": request.step_id,
        "connector_action": request.connector_action,
        "approved": True,
        "status": "approved",
        "package_fingerprint": package_fingerprint,
        "decided_by": "test",
        "idempotency_key": request.idempotency_key,
        "payload_hash": payload_hash,
        "payload_preview_path": getattr(request, "payload_preview_path", None),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _set_package_run_mode(package: Path, run_id: str, mode: str) -> None:
    path = package / ".workflow-runs" / run_id / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mode"] = mode
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
