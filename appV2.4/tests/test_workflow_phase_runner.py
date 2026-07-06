from __future__ import annotations

import json
from pathlib import Path

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.phase_runner import run_workflow_approved_live_production, run_workflow_live_read_to_approval
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
    assert report.phase_statuses == (
        "live-read: paused",
        "local-resume: blocked",
    )
    assert report.next_commands == (
        f"/workflow-approvals {package.resolve()} {report.local_run_id}",
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
