from __future__ import annotations

from pathlib import Path
import shutil

import yaml

from appv24.workflows.approval_gate import ApprovalDecision, ApprovalRequest
from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.explain import (
    explain_workflow_activation_run,
    explain_workflow_package,
    explain_workflow_run,
    format_workflow_package_explanation,
    format_workflow_run_explanation,
)
from appv24.workflows import WorkflowActivationStore
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.live_runner import run_live_production_workflow, run_live_read_workflow
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import run_workflow


class _RecordingProductionTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={
                "message_id": "msg-1",
                "chat_id": "admin-chat",
                "secret": "must-not-render",
            },
        )


class _RecordingReadTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(
            status="live-read",
            provider_id="fake-source",
            outputs={"items": [{"title": "Rate decision"}], "source_count": 1},
        )


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


def test_package_explanation_reports_validation_compile_policy_and_actions(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")

    explanation = explain_workflow_package(package)

    assert explanation.package_path == package.resolve()
    assert explanation.workflow_name == "telegram_support_ticket"
    assert explanation.validation_status == "passed"
    assert explanation.compile_status == "passed"
    assert explanation.policy_status == "passed"
    assert explanation.steps == (
        "check_authorized_customer",
        "create_ticket_draft",
        "notify_admin",
        "rejected",
        "done",
    )
    assert explanation.connector_actions == ("ticket.create_draft", "telegram.send_message")
    assert explanation.approvals == ("admin_approval",)
    assert explanation.issues == ()

    rendered = format_workflow_package_explanation(explanation)
    assert "Package: telegram_support_ticket" in rendered
    assert "Validation: passed" in rendered
    assert "ticket.create_draft" in rendered


def test_package_explanation_stops_at_validation_failure(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    workflow = yaml.safe_load((package / "workflow.yaml").read_text(encoding="utf-8"))
    workflow["steps"][1]["uses"] = "telegram.receive_webhook"
    (package / "workflow.yaml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    explanation = explain_workflow_package(package)

    assert explanation.validation_status == "failed"
    assert explanation.compile_status == "skipped"
    assert explanation.policy_status == "skipped"
    assert any("telegram.receive_webhook" in issue for issue in explanation.issues)


def test_run_explanation_reads_trace_report_and_events(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=True,
        )
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.run_id == report.run_id
    assert explanation.status == "passed"
    assert explanation.terminal_step == "done"
    assert explanation.trace == (
        "check_authorized_customer",
        "create_ticket_draft",
        "notify_admin",
        "done",
    )
    assert len(explanation.brokered_actions) == 2
    assert explanation.brokered_actions[0].startswith(
        "create_ticket_draft: ticket.create_draft -> simulated idempotency=idemp:"
    )
    assert explanation.brokered_actions[1].startswith(
        "notify_admin: telegram.send_message -> simulated idempotency=idemp:"
    )
    assert explanation.approvals == ("admin_approval: approved",)
    assert explanation.issues == ()
    assert explanation.provenance == (
        "mode: local",
        "fixture: happy_path.yaml",
        f"package: {package.resolve()}",
        "approval: auto-approved",
    )

    rendered = format_workflow_run_explanation(explanation)
    assert "Workflow run: passed" in rendered
    assert "Provenance:" in rendered
    assert "mode: local" in rendered
    assert "terminal: done" in rendered
    assert "notify_admin: telegram.send_message -> simulated idempotency=idemp:" in rendered


def test_run_explanation_requires_run_id_when_multiple_runs_exist(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    for _ in range(2):
        run_workflow(
            WorkflowRunRequest(
                package_path=package,
                fixture_name="happy_path.yaml",
                mode=WorkflowRunMode.LOCAL,
                approve_all=True,
            )
        )

    explanation = explain_workflow_run(package)

    assert explanation.status == "unavailable"
    assert explanation.issues
    assert "multiple workflow runs found" in explanation.issues[0]


def test_run_explanation_reports_pending_approval_request_details(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.status == "blocked"
    assert len(explanation.approvals) == 1
    assert explanation.approvals[0].startswith(
        "admin_approval: pending request=approval-"
    )
    assert "step=create_ticket_draft" in explanation.approvals[0]
    assert "action=ticket.create_draft" in explanation.approvals[0]
    assert "idempotency=idemp:" in explanation.approvals[0]


def test_run_explanation_recommends_safe_commands_for_pending_approval(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.next_steps[0] == f"/workflow-approvals {package.resolve()} {report.run_id}"
    assert explanation.next_steps[1].startswith(f"/workflow-approve {package.resolve()} {report.run_id} approval-")
    assert explanation.next_steps[2] == (
        f"/workflow-live-ready live-draft {package.resolve()} happy_path.yaml {report.run_id} "
        "--allow ticket.create_draft"
    )
    rendered = format_workflow_run_explanation(explanation)
    assert "Next safe commands:" in rendered
    assert f"/workflow-approvals {package.resolve()} {report.run_id}" in rendered


def test_run_explanation_reports_handoff_for_paused_live_read_prefix(tmp_path: Path) -> None:
    package = tmp_path / "financial-catalyst-alerts"
    shutil.copytree(Path("examples/appv24/workflows/financial-catalyst-alerts"), package)
    run_root = package / ".workflow-runs"

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=run_root,
            connector_allowlist=("source.fetch_items", "llm.extract_structured"),
        ),
        broker=LiveWorkflowBroker({"source.fetch_items": _RecordingReadTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "source": CredentialScope(available=True, scopes=("source:read",)),
                "llm": CredentialScope(available=True, scopes=()),
                "policy": CredentialScope(available=True, scopes=()),
                "notification": CredentialScope(available=True, scopes=("notification:draft",)),
            }
        ),
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.status == "paused"
    assert explanation.handoff == (
        "live-read -> local at extract_catalysts",
        "available outputs: fetch_news.items, fetch_news.source_count",
    )
    assert explanation.next_steps == (
        f"/workflow-live-plan {package.resolve()} happy_path.yaml",
        f"/workflow-run-approval {package.resolve()} happy_path.yaml",
    )
    rendered = format_workflow_run_explanation(explanation)
    assert "Handoff:" in rendered
    assert "live-read -> local at extract_catalysts" in rendered


def test_run_explanation_reports_recorded_approval_decisions(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            approve_all=False,
        )
    )
    run_root = package / ".workflow-runs"
    approvals = yaml.safe_load((run_root / report.run_id / "approvals.json").read_text(encoding="utf-8"))
    request = ApprovalRequest(**approvals["approval_requests"][0])
    store = WorkflowRunStore(run_root)

    store.append_approval_decision(
        request,
        ApprovalDecision(approved=True, package_fingerprint=report.package_fingerprint, decided_by="Lewis"),
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert len(explanation.approval_decisions) == 1
    assert explanation.approval_decisions[0].startswith(f"{request.approval_request_id}: approved by Lewis")
    assert "action=ticket.create_draft" in explanation.approval_decisions[0]
    assert "idempotency=idemp:" in explanation.approval_decisions[0]


def test_activation_run_explanation_reads_activation_run_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    store = WorkflowActivationStore(tmp_path / ".appv24" / "workflow-activations")
    activation = store.create_activation(package, fixture_name="happy_path.yaml", approve_all=True)
    report = store.run_activation(activation.activation_id)

    explanation = explain_workflow_activation_run(store, activation.activation_id, report.run_id)

    assert explanation.package_path == package.resolve()
    assert explanation.run_id == report.run_id
    assert explanation.status == "passed"
    assert explanation.terminal_step == "done"
    assert len(explanation.brokered_actions) == 2
    assert explanation.brokered_actions[0].startswith(
        "create_ticket_draft: ticket.create_draft -> simulated idempotency=idemp:"
    )
    assert explanation.brokered_actions[1].startswith(
        "notify_admin: telegram.send_message -> simulated idempotency=idemp:"
    )
    assert f"activation: {activation.activation_id}" in explanation.provenance
    assert "trigger: manual" in explanation.provenance

    rendered = format_workflow_run_explanation(explanation)
    assert "Workflow run: passed" in rendered
    assert f"activation: {activation.activation_id}" in rendered
    assert "notify_admin: telegram.send_message -> simulated idempotency=idemp:" in rendered


def test_run_explanation_reports_live_production_provenance_without_filtered_outputs(tmp_path: Path) -> None:
    package = _write_live_production_telegram_package(tmp_path)
    run_root = package / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": _RecordingProductionTransport()},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.status == "passed"
    assert explanation.terminal_step == "done"
    assert "mode: live-production" in explanation.provenance
    assert f"approval run: {approval_report.run_id}" in explanation.provenance
    assert "allowlist: telegram.send_message" in explanation.provenance
    assert explanation.gate == ("live_gate: passed actions=telegram.send_message",)
    assert explanation.brokered_actions[0].startswith("notify_admin: telegram.send_message -> live-production idempotency=idemp:")
    rendered = format_workflow_run_explanation(explanation)
    assert "Gate:" in rendered
    assert "live_gate: passed actions=telegram.send_message" in rendered
    assert "must-not-render" not in rendered
    assert "secret" not in rendered


def test_run_explanation_recommends_safe_commands_for_blocked_live_run(tmp_path: Path) -> None:
    package = _write_live_production_telegram_package(tmp_path)
    run_root = package / ".workflow-runs"
    approval_report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=False,
        )
    )
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=(),
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": _RecordingProductionTransport()},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    explanation = explain_workflow_run(package, report.run_id)

    assert explanation.status == "blocked"
    assert explanation.gate == ("live_gate: blocked actions=telegram.send_message",)
    assert explanation.next_steps == (
        f"/workflow-live-env-template live-production {package.resolve()} --allow telegram.send_message",
        (
            f"/workflow-live-ready live-production {package.resolve()} happy_path.yaml {approval_report.run_id} "
            "--allow telegram.send_message"
        ),
        (
            f"/workflow-run-live-production {package.resolve()} happy_path.yaml {approval_report.run_id} "
            "--allow telegram.send_message"
        ),
    )


def _write_live_production_telegram_package(root: Path) -> Path:
    package = root / "telegram-production-alert"
    package.mkdir(parents=True)
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
                        "uses": "telegram.send_message",
                        "side_effect_class": "send",
                        "approval": "admin-review",
                        "with": {"chat_id": "admin-chat", "text": "appv24 production alert"},
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
                "design": {
                    "intent": "Send an approved Telegram production alert.",
                    "trigger": "Manual operator run.",
                    "inputs": ["chat id", "message text"],
                    "outputs": ["message id", "chat id"],
                    "assumptions": ["Telegram credentials are resolved outside the package."],
                    "side_effects": ["Send one Telegram message after approval."],
                    "approval_gates": ["admin-review"],
                    "failure_modes": ["missing approval", "transport failure"],
                    "rollback": ["Send a corrective message manually if needed."],
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
                                "id": "send_message",
                                "operationId": "telegram_send_message",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "execution_mode": "brokered",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "idempotency": {"key_fields": ["chat_id", "text"]},
                                "outputs": {"allowed": ["message_id", "chat_id"]},
                                "required_scopes": ["telegram:send"],
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
                "approvals": [{"id": "admin-review", "mode": "explicit", "required_for": ["notify_admin"]}],
                "permissions": [{"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: appv24 production alert\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("input:\n  chat_id: admin-chat\n  text: rejected alert\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective message manually if needed.\n", encoding="utf-8")
    return package


def _approve_first_request(run_root: Path, run_id: str, *, package_fingerprint: str) -> None:
    store = WorkflowRunStore(run_root)
    request = store.read_approval_requests(run_id)[0]
    store.record_approval_decision(
        run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=package_fingerprint, decided_by="test"),
    )
