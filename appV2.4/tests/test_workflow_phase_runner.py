from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.phase_runner import (
    run_workflow_approved_live_draft,
    run_workflow_approved_live_production,
    run_workflow_live_read_to_approval,
)
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import run_workflow


class _RecordingReadTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(
            status="live-read",
            provider_id="fake-source",
            outputs={"items": [{"title": "Rate decision"}], "source_count": 1},
        )


class _RecordingProductionTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={"message_id": "msg-1", "chat_id": "admin-chat"},
        )


class _RecordingDraftTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-draft",
            provider_id="fake-gmail",
            outputs={"draft_id": "draft-1", "thread_id": "thread-1"},
        )


def test_phase_runner_chains_live_read_to_local_resume_pending_approval(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/financial-catalyst-alerts"
    run_root = tmp_path / ".workflow-runs"
    seen_handoff_inputs: list[object] = []

    def extract_structured(request: BrokerRequest) -> BrokerResult:
        seen_handoff_inputs.append(request.fixture["handoff"]["outputs"]["fetch_news"]["items"])
        return BrokerResult(
            status="simulated",
            provider_id="phase-test",
            outputs={
                "alerts": [{"title": "Rate decision", "source_urls": ["https://example.test/rate"]}],
                "confidence_score": "medium",
                "raw_response": {"provider": "phase-test"},
            },
        )

    report = run_workflow_live_read_to_approval(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("source.fetch_items", "llm.extract_structured"),
        ),
        live_read_broker=LiveWorkflowBroker({"source.fetch_items": _RecordingReadTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "source": CredentialScope(available=True, scopes=("source:read",)),
                "llm": CredentialScope(available=True, scopes=()),
                "policy": CredentialScope(available=True, scopes=()),
                "notification": CredentialScope(available=True, scopes=("notification:draft",)),
            }
        ),
        local_broker=LocalWorkflowBroker(
            {
                "llm.extract_structured": extract_structured,
                "policy.check": lambda request: BrokerResult(
                    status="simulated",
                    provider_id="phase-test",
                    outputs={"approved": True, "reason": "policy passed"},
                ),
            }
        ),
    )

    assert report.status == "approval-required"
    assert report.live_read_run_id.startswith("run-")
    assert report.local_run_id.startswith("run-")
    assert report.approval_run_id == report.local_run_id
    assert len(report.approval_request_ids) == 1
    assert report.phase_statuses == (
        "live-read: paused",
        "local-resume: blocked",
    )
    assert report.next_commands == (
        f"/workflow-approvals {package.resolve()} {report.local_run_id}",
        f"/workflow-approve {package.resolve()} {report.local_run_id} {report.approval_request_ids[0]}",
        f"/workflow-live-ready live-draft {package.resolve()} happy_path.yaml {report.local_run_id} --allow notification.create_draft",
        f"/workflow-run-live-draft {package.resolve()} happy_path.yaml {report.local_run_id} --allow notification.create_draft",
        f"/workflow-live-plan {package.resolve()} happy_path.yaml",
    )
    assert seen_handoff_inputs == [[{"title": "Rate decision"}]]

    live_report = json.loads((run_root / report.live_read_run_id / "report.json").read_text(encoding="utf-8"))
    local_report = json.loads((run_root / report.local_run_id / "report.json").read_text(encoding="utf-8"))
    local_approvals = json.loads((run_root / report.local_run_id / "approvals.json").read_text(encoding="utf-8"))
    assert live_report["status"] == "paused"
    assert local_report["status"] == "blocked"
    assert [step["step_id"] for step in local_report["step_trace"]] == [
        "extract_catalysts",
        "check_policy",
        "create_alert_draft",
    ]
    assert local_approvals["approval_requests"][0]["step_id"] == "create_alert_draft"
    assert local_approvals["approval_requests"][0]["status"] == "pending"


def test_phase_runner_runs_approved_live_production_from_local_ledger(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/telegram-production-alert"
    run_root = tmp_path / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(run_root)
    approval_request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        approval_request.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=approval_report.package_fingerprint,
            decided_by="Lewis",
        ),
    )
    transport = _RecordingProductionTransport()

    report = run_workflow_approved_live_production(
        WorkflowRunRequest(
                package_path=package,
                fixture_name="happy_path.yaml",
                run_root=run_root,
                connector_allowlist=("telegram.send_message",),
                operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "123456789"},
            ),
        approval_run_id=approval_report.run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {"telegram": CredentialScope(available=True, scopes=("telegram:send",))}
        ),
    )

    assert report.status == "passed"
    assert report.live_production_run_id.startswith("run-")
    assert report.live_production_report is not None
    assert report.live_production_report.run_id == report.live_production_run_id
    assert report.live_production_report.status == "passed"
    assert report.live_production_report.brokered_actions[0].connector_action == "telegram.send_message"
    assert report.approval_run_id == approval_report.run_id
    assert report.phase_statuses == ("live-production: passed",)
    assert report.next_commands == ()
    assert transport.requests[0].connector_action == "telegram.send_message"

    production_report = json.loads(
        (run_root / report.live_production_run_id / "report.json").read_text(encoding="utf-8")
    )
    assert production_report["mode"] == "live-production"
    assert production_report["status"] == "passed"
    assert production_report["brokered_actions"][0]["connector_action"] == "telegram.send_message"


def test_phase_runner_runs_approved_live_draft_from_local_ledger(tmp_path: Path) -> None:
    package = _write_golden_package(tmp_path, "gmail_draft_reply")
    run_root = tmp_path / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(run_root)
    approval_request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        approval_request.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=approval_report.package_fingerprint,
            decided_by="Lewis",
        ),
    )
    transport = _RecordingDraftTransport()

    report = run_workflow_approved_live_draft(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("gmail.create_draft",),
        ),
        approval_run_id=approval_report.run_id,
        live_draft_broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "passed"
    assert report.live_draft_run_id.startswith("run-")
    assert report.live_draft_report is not None
    assert report.live_draft_report.run_id == report.live_draft_run_id
    assert report.live_draft_report.status == "passed"
    assert report.live_draft_report.brokered_actions[0].connector_action == "gmail.create_draft"
    assert report.approval_run_id == approval_report.run_id
    assert report.phase_statuses == ("live-draft: passed",)
    assert report.next_commands == ()
    assert transport.requests[0].connector_action == "gmail.create_draft"

    draft_report = json.loads((run_root / report.live_draft_run_id / "report.json").read_text(encoding="utf-8"))
    assert draft_report["mode"] == "live-draft"
    assert draft_report["status"] == "passed"
    assert draft_report["brokered_actions"][0]["connector_action"] == "gmail.create_draft"


def test_phase_runner_preserves_operator_env_for_live_production_target_policy(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/telegram-production-alert"
    run_root = tmp_path / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    store = WorkflowRunStore(run_root)
    approval_request = store.read_approval_requests(approval_report.run_id)[0]
    store.record_approval_decision(
        approval_report.run_id,
        approval_request.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=approval_report.package_fingerprint,
            decided_by="Lewis",
        ),
    )
    transport = _RecordingProductionTransport()

    report = run_workflow_approved_live_production(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("telegram.send_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "other-admin"},
        ),
        approval_run_id=approval_report.run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {"telegram": CredentialScope(available=True, scopes=("telegram:send",))}
        ),
    )

    assert report.status == "blocked"
    assert report.live_production_report is not None
    assert report.live_production_report.issues == (
        "telegram.send_message chat_id is not allowlisted: 123456789",
    )
    assert transport.requests == []


def test_phase_runner_runs_mixed_brave_local_and_live_production_from_approval_step(tmp_path: Path) -> None:
    package = _write_mixed_brave_llm_telegram_package(tmp_path)
    run_root = tmp_path / ".workflow-runs"
    seen_live_read_requests: list[BrokerRequest] = []

    class BraveTransport(LiveWorkflowTransport):
        def invoke(self, request: BrokerRequest) -> BrokerResult:
            seen_live_read_requests.append(request)
            return BrokerResult(
                status="live-read",
                provider_id="fake-brave",
                outputs={
                    "items": [{"title": "Fed minutes move stocks", "url": "https://example.test/fed"}],
                    "source_count": 1,
                    "query": "market catalysts",
                },
            )

    def summarize(request: BrokerRequest) -> BrokerResult:
        assert request.fixture["handoff"]["outputs"]["search_news"]["items"] == [
            {"title": "Fed minutes move stocks", "url": "https://example.test/fed"}
        ]
        return BrokerResult(
            status="simulated",
            provider_id="phase-test",
            outputs={"summary": "Fed minutes are a market catalyst.", "raw_response": {"ok": True}},
        )

    approval_report = run_workflow_live_read_to_approval(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("brave.search",),
        ),
        live_read_broker=LiveWorkflowBroker({"brave.search": BraveTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "llm": CredentialScope(available=True, scopes=()),
                "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
            }
        ),
        local_broker=LocalWorkflowBroker({"llm.summarize": summarize}),
    )

    assert approval_report.status == "approval-required"
    assert approval_report.approval_run_id == approval_report.local_run_id
    assert approval_report.approval_request_ids
    assert seen_live_read_requests[0].connector_action == "brave.search"

    store = WorkflowRunStore(run_root)
    request = store.read_approval_requests(approval_report.local_run_id)[0]
    store.record_approval_decision(
        approval_report.local_run_id,
        request.approval_request_id,
        ApprovalDecision(
            approved=True,
            package_fingerprint=request.package_fingerprint,
            decided_by="Lewis",
        ),
    )
    production_transport = _RecordingProductionTransport()

    production_report = run_workflow_approved_live_production(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            run_root=run_root,
            connector_allowlist=("telegram.send_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        approval_run_id=approval_report.local_run_id,
        live_production_broker=LiveWorkflowBroker(
            {"telegram.send_message": production_transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "llm": CredentialScope(available=True, scopes=()),
                "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
            }
        ),
    )

    assert production_report.status == "passed"
    assert production_report.live_production_report is not None
    assert [trace.step_id for trace in production_report.live_production_report.step_trace] == [
        "notify_chat",
        "done",
    ]
    assert production_report.live_production_report.brokered_actions[0].connector_action == "telegram.send_message"
    assert [request.connector_action for request in production_transport.requests] == ["telegram.send_message"]
    assert production_transport.requests[0].fixture["steps"]["extract_news"]["outputs"]["summary"] == (
        "Fed minutes are a market catalyst."
    )


def test_workflows_package_exports_live_draft_phase_runner() -> None:
    from appv24.workflows import run_workflow_approved_live_draft as exported

    assert exported is run_workflow_approved_live_draft


def _write_golden_package(root: Path, example_name: str) -> Path:
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


def _write_mixed_brave_llm_telegram_package(root: Path) -> Path:
    package = root / "mixed-brave-telegram"
    package.mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "mixed-brave-telegram",
                "description": "Read Brave news, summarize locally, and send approved Telegram message.",
                "design": {
                    "intent": "Send a market catalyst update.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["Operator configured Brave and Telegram credentials."],
                    "side_effects": ["Send one Telegram message after approval."],
                    "approval_gates": ["approve_send_message"],
                    "failure_modes": ["Search, extraction, or send failure."],
                    "rollback": ["Delete the Telegram message manually if needed."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_news",
                "steps": [
                    {
                        "id": "search_news",
                        "uses": "brave.search",
                        "with": {"query": "market catalysts", "count": 5},
                        "next": "extract_news",
                    },
                    {
                        "id": "extract_news",
                        "uses": "llm.summarize",
                        "with": {"output_contract": "summary_v1"},
                        "next": "notify_chat",
                    },
                    {
                        "id": "notify_chat",
                        "uses": "telegram.send_message",
                        "side_effect_class": "send",
                        "approval": "approve_send_message",
                        "with": {
                            "chat_id": {"inputRef": "chat_id"},
                            "text": "{{ steps.extract_news.outputs.summary }}",
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
                                "operationId": "brave_search",
                                "side_effect": False,
                                "outputs": {"allowed": ["items", "source_count", "query"]},
                                "required_scopes": ["brave:search"],
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
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                    {"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]},
                ],
                "approvals": [
                    {
                        "id": "approve_send_message",
                        "mode": "explicit",
                        "required_for": ["notify_chat"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    fixture_text = "input:\n  chat_id: admin-chat\n  text: Fed minutes are a market catalyst.\n"
    (fixtures / "happy_path.yaml").write_text(fixture_text, encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text(fixture_text, encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nDelete the Telegram message manually if needed.\n", encoding="utf-8")
    return package
