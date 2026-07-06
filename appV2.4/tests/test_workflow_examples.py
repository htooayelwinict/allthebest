from __future__ import annotations

from pathlib import Path

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.fixtures import fixture_names, require_fixture
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.live_preflight import preflight_live_production_workflow
from appv24.workflows.live_runner import run_live_production_workflow
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.reports import build_enablement_report
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import run_workflow
from appv24.workflows.simulator import simulate_workflow_fixture
from appv24.workflows.validator import validate_workflow_package


class _RecordingProductionTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={
                "message_id": "msg-1",
                "chat_id": "admin-chat",
                "secret": "must-not-leak",
            },
        )


def test_golden_examples_compile_and_pass_policy() -> None:
    for example in golden_workflow_examples().values():
        result = compile_workflow_documents(example.documents)
        assert result.ok, example.name
        assert result.ir is not None
        assert evaluate_policy(result.ir).ok, example.name


def test_gmail_draft_qa_example_runs_happy_path() -> None:
    example = golden_workflow_examples()["gmail_draft_reply"]
    result = compile_workflow_documents(example.documents)
    assert result.ok
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "happy_path.yaml",
        example.fixtures["happy_path.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "done"
    assert ledger.simulated_side_effects == ("create_gmail_draft",)
    assert ledger.trace[1].connector_action == "gmail.create_draft"
    assert ledger.trace[1].simulated_outputs == {"draft_id": "draft-1"}


def test_telegram_support_ticket_happy_path_is_deterministic() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    first = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)
    second = simulate_workflow_fixture(result.ir, "happy_path.yaml", example.fixtures["happy_path.yaml"], broker=example.broker)

    assert first.to_dict() == second.to_dict()
    assert first.status == "passed"
    assert first.terminal_step == "done"
    assert first.simulated_side_effects == ("create_ticket_draft", "notify_admin")


def test_telegram_support_ticket_rejection_path_has_no_side_effects() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "rejection_path.yaml",
        example.fixtures["rejection_path.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "rejected"
    assert ledger.simulated_side_effects == ()


def test_telegram_support_ticket_unauthorized_user_fixture_has_no_side_effects() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    result = compile_workflow_documents(example.documents)
    assert result.ir is not None

    ledger = simulate_workflow_fixture(
        result.ir,
        "unauthorized_user.yaml",
        example.fixtures["unauthorized_user.yaml"],
        broker=example.broker,
    )

    assert ledger.status == "passed"
    assert ledger.terminal_step == "rejected"
    assert ledger.simulated_side_effects == ()


def test_enablement_report_summarizes_golden_example() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    report = build_enablement_report(
        example.documents,
        "happy_path.yaml",
        example.fixtures["happy_path.yaml"],
        broker=example.broker,
    )

    assert report.workflow_name == "telegram_support_ticket"
    assert report.validation_ok is True
    assert report.compile_ok is True
    assert report.policy_ok is True
    assert report.simulation_status == "passed"
    assert report.terminal_step == "done"
    assert report.simulated_side_effects == ("create_ticket_draft", "notify_admin")
    assert report.issues == ()


def test_fixture_helpers_return_sorted_names_and_required_fixture() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fixture_names(example.fixtures) == (
        "happy_path.yaml",
        "rejection_path.yaml",
        "ticket_draft_timeout.yaml",
        "unauthorized_user.yaml",
    )
    assert require_fixture(example.fixtures, "happy_path.yaml") is example.fixtures["happy_path.yaml"]


def test_gmail_fixture_helpers_include_missing_recipient_scenario() -> None:
    example = golden_workflow_examples()["gmail_draft_reply"]

    assert fixture_names(example.fixtures) == ("happy_path.yaml", "missing_recipient.yaml", "rejection_path.yaml")


def test_require_fixture_reports_available_names() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    try:
        require_fixture(example.fixtures, "missing.yaml")
    except WorkflowPackageError as error:
        assert (
            str(error)
            == "missing dry-run fixture: missing.yaml; available: "
            "happy_path.yaml, rejection_path.yaml, ticket_draft_timeout.yaml, unauthorized_user.yaml"
        )
    else:
        raise AssertionError("expected missing fixture to raise")


def test_telegram_production_alert_example_is_valid_and_live_ready(tmp_path: Path) -> None:
    package = Path("examples/appv24/workflows/telegram-production-alert")
    validation = validate_workflow_package(package)
    assert validation.ok, [issue.message for issue in validation.issues]

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
    assert approval_report.status == "blocked"
    assert approval_report.issues == ("approval required for notify_admin",)
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    request = WorkflowRunRequest(
        package_path=package,
        fixture_name="happy_path.yaml",
        mode=WorkflowRunMode.LIVE_PRODUCTION,
        run_root=run_root,
        approval_run_id=approval_report.run_id,
        connector_allowlist=("telegram.send_message",),
    )
    credentials = CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))})

    preflight = preflight_live_production_workflow(
        request,
        credential_resolver=credentials,
        available_transports=("telegram.send_message",),
    )
    assert preflight.status == "passed"
    assert preflight.checked_actions == ("telegram.send_message",)

    transport = _RecordingProductionTransport()
    report = run_live_production_workflow(
        request,
        broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=credentials,
    )

    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert report.brokered_actions[0].connector_action == "telegram.send_message"
    assert report.brokered_actions[0].simulated_outputs == {"message_id": "msg-1", "chat_id": "admin-chat"}
    assert transport.requests[0].dry_run is False


def test_telegram_production_alert_runbook_contains_manual_gate_flow() -> None:
    text = Path("docs/appv24/workflow-live-production-telegram-qa.md").read_text(encoding="utf-8")

    assert "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true" in text
    assert "APPV24_TELEGRAM_BOT_TOKEN" in text
    assert "/workflow-run-local examples/appv24/workflows/telegram-production-alert happy_path.yaml" in text
    assert "/workflow-approvals examples/appv24/workflows/telegram-production-alert <approval-run-id>" in text
    assert "/workflow-approve examples/appv24/workflows/telegram-production-alert <approval-run-id>" in text
    assert "/workflow-live-production-preflight examples/appv24/workflows/telegram-production-alert" in text
    assert "/workflow-run-live-production examples/appv24/workflows/telegram-production-alert" in text
    assert "--allow telegram.send_message" in text


def _approve_first_request(run_root: Path, run_id: str, *, package_fingerprint: str) -> None:
    store = WorkflowRunStore(run_root)
    requests = store.read_approval_requests(run_id)
    assert requests
    store.record_approval_decision(
        run_id,
        requests[0].approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=package_fingerprint, decided_by="test"),
    )
