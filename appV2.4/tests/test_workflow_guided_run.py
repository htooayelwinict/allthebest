from __future__ import annotations

from pathlib import Path

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.guided_run import (
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    prepare_guided_workflow_run,
)
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.run_store import WorkflowRunStore


class _ReadTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        if request.connector_action == "brave.search":
            return BrokerResult(
                status="live-read",
                provider_id="fake-brave",
                outputs={"items": [{"title": "AI model news"}], "source_count": 1, "query": "AI news"},
            )
        if request.connector_action == "telegram.list_subscribers":
            return BrokerResult(
                status="live-read",
                provider_id="fake-telegram",
                outputs={"recipients": ["admin-chat"], "recipient_count": 1, "audience_hash": "aud-1"},
            )
        raise AssertionError(f"unexpected read action {request.connector_action}")


class _ProductionTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={"sent_count": 1, "message_ids": ["msg-1"], "chat_ids": ["admin-chat"]},
        )


def test_prepare_guided_workflow_run_stops_at_approval(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    report = _prepare_package_to_approval(package)

    assert report.status == "approval-required"
    assert report.live_plan_status == "partial-live"
    assert report.live_read_run_id.startswith("run-")
    assert report.approval_run_id == report.local_run_id
    assert len(report.approval_request_ids) == 1
    rendered = format_guided_workflow_run_report(report)
    assert "Workflow guided run: approval-required" in rendered
    assert "next action: approve external side effect" in rendered


def test_continue_guided_workflow_run_refuses_unapproved_request(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    prepare_report = _prepare_package_to_approval(package)

    report = continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
        ),
        approval_run_id=prepare_report.approval_run_id or "",
        live_production_broker=LiveWorkflowBroker({}, mode="live-production", production_enabled=True),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        env={},
    )

    assert report.status == "approval-required"
    assert report.live_production_run_id == ""
    assert report.next_action == "approve external side effect"


def test_continue_guided_workflow_run_runs_readiness_then_production(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    prepare_report = _prepare_package_to_approval(package)
    assert prepare_report.approval_run_id is not None

    store = WorkflowRunStore(package / ".workflow-runs")
    approval = store.read_approval_requests(prepare_report.approval_run_id)[0]
    manifest = store.read_run_manifest(prepare_report.approval_run_id)
    store.record_approval_decision(
        prepare_report.approval_run_id,
        approval.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=str(manifest["package_fingerprint"]),
            decided_by="test",
        ),
    )
    transport = _ProductionTransport()

    report = continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        approval_run_id=prepare_report.approval_run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.broadcast_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        env={
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "redacted-token",
            "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat",
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-ref",
            "APPV24_TELEGRAM_SCOPES": "telegram:send",
        },
        readiness_inspector=lambda request, env: "ready",
    )

    assert report.status == "passed"
    assert report.readiness_status == "ready"
    assert report.live_production_run_id.startswith("run-")
    assert transport.requests[0].connector_action == "telegram.broadcast_message"


def _prepare_package_to_approval(package: Path):
    return prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": _ReadTransport(),
                "telegram.list_subscribers": _ReadTransport(),
            },
            mode="live-read",
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
        local_broker=LocalWorkflowBroker(
            {
                "llm.summarize": lambda request: BrokerResult(
                    status="simulated",
                    provider_id="fake-llm",
                    outputs={"summary": "AI digest"},
                )
            }
        ),
    )


def _write_ai_news_broadcast_package(root: Path) -> Path:
    package = root / "ai-news-telegram"
    (package / "dry_run_fixtures").mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        """version: appv24.workflow/v1
name: ai-news-telegram
description: Search AI news and broadcast digest to registered Telegram subscribers.
design:
  intent: AI news Telegram digest
  trigger: manual
  inputs: []
  outputs: [telegram digest]
  assumptions: []
  side_effects: [telegram broadcast]
  approval_gates: [approve_telegram_broadcast]
  failure_modes: []
  rollback:
    - Send a corrective Telegram message if the digest was sent to the wrong audience.
triggers:
  - id: manual
    type: manual
entrypoint: search_ai_news
steps:
  - id: search_ai_news
    uses: brave.search
    with:
      query: artificial intelligence news today
      freshness: day
      count: 5
    next: list_subscribers
  - id: list_subscribers
    uses: telegram.list_subscribers
    with:
      audience: default
    next: summarize_digest
  - id: summarize_digest
    uses: llm.summarize
    with:
      items: "{{ steps.search_ai_news.outputs.items }}"
      output_contract: summary_v1
    next: send_digest
  - id: send_digest
    uses: telegram.broadcast_message
    side_effect_class: send
    approval: approve_telegram_broadcast
    with:
      recipients: "{{ steps.list_subscribers.outputs.recipients }}"
      audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"
      text: "{{ steps.summarize_digest.outputs.summary }}"
    next: done
  - id: done
    type: terminal
""",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """version: appv24.connectors/v1
connectors:
  - id: brave
    adapter: direct_api
    actions:
      - id: search
        side_effect: false
        required_scopes: [brave:search]
        outputs:
          allowed: [items, source_count, query, artifact_path]
  - id: telegram
    adapter: direct_api
    actions:
      - id: list_subscribers
        side_effect: false
        required_scopes: [telegram:subscribers:read]
        outputs:
          allowed: [recipients, recipient_count, audience_hash, artifact_path]
      - id: broadcast_message
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        required_scopes: [telegram:send]
        idempotency:
          key_fields: [audience_hash, text]
        outputs:
          allowed: [sent_count, message_ids, chat_ids, artifact_path]
  - id: llm
    adapter: llm
    actions:
      - id: summarize
        side_effect: false
        outputs:
          allowed: [summary, citations, confidence, raw_response, artifact_path]
""",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
  - connector: telegram
    actions: [list_subscribers, broadcast_message]
    scopes: [telegram:subscribers:read, telegram:send]
  - connector: llm
    actions: [summarize]
    scopes: []
approvals:
  - id: approve_telegram_broadcast
    mode: explicit
    required_for: [send_digest]
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (package / "dry_run_fixtures" / "rejection_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (package / "rollback.md").write_text("Manual operator follow-up if Telegram delivery fails.\n", encoding="utf-8")
    return package
