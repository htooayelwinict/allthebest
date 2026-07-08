from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.live_runner import run_live_read_workflow
from appv24.workflows.runner import run_workflow, run_workflow_from_handoff


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


def _write_audit_log_middleware(package: Path) -> None:
    (package / "middlewares.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.middlewares/v1",
                "middlewares": [
                    {
                        "id": "audit.log",
                        "adapter": "builtin",
                        "source": "catalog://audit.log@v1",
                        "config": {"events": True},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_intelligence_package(root: Path) -> Path:
    from test_workflow_packages import write_intelligence_package

    package = root / "financial-catalyst-alerts"
    write_intelligence_package(package)
    return package


def _write_policy_decision_package(root: Path) -> Path:
    package = root / "policy-decision-package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "policy-decision-package",
                "description": "Policy gate followed by a decision branch.",
                "design": {
                    "intent": "Check a policy gate before selecting an allowed or rejected branch.",
                    "trigger": "Manual dry-run fixture.",
                    "inputs": ["fixture input"],
                    "outputs": ["policy trace"],
                    "assumptions": ["No external connectors run."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["policy rejection"],
                    "rollback": ["No external state changes."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "policy_gate",
                "steps": [
                    {
                        "id": "policy_gate",
                        "uses": "policy.check",
                        "side_effect_class": "read",
                        "with": {"checks": {"tenant_allowlist": True}},
                        "next": "approve_path",
                    },
                    {
                        "id": "approve_path",
                        "type": "decision",
                        "branches": {"allowed": "done", "rejected": "done"},
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
                        "id": "policy",
                        "adapter": "manual",
                        "actions": [
                            {
                                "id": "check",
                                "side_effect": False,
                                "side_effect_class": "read",
                                "execution_mode": "brokered",
                                "dry_run_supported": True,
                                "outputs": {"allowed": ["approved", "allowed", "reason", "matched_rule"]},
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
                "permissions": [{"connector": "policy", "actions": ["check"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.dry-run/v1",
                "decision": {"approve_path": "allowed"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (fixtures / "rejection_path.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.dry-run/v1",
                "decision": {"approve_path": "rejected"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nNo external state changes.\n", encoding="utf-8")
    return package


def test_local_run_happy_path_writes_report_trace_events_and_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert [step.step_id for step in report.step_trace] == [
        "check_authorized_customer",
        "create_ticket_draft",
        "notify_admin",
        "done",
    ]
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "trace.json").is_file()
    assert (run_dir / "approvals.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert sorted(path.name for path in (run_dir / "artifacts").glob("*.json")) == [
        "local-telegram-notify_admin.json",
        "local-ticket-create_ticket_draft.json",
    ]

    saved_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    saved_run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert saved_report["status"] == "passed"
    assert saved_report["terminal_step"] == "done"
    assert saved_run["workflow_name"] == "telegram_support_ticket"
    assert saved_run["mode"] == "local"
    assert saved_run["fixture_name"] == "happy_path.yaml"
    assert saved_run["package_path"] == str(package)
    assert saved_run["approve_all"] is True
    assert saved_run["approval_mode"] == "auto-approved"
    assert saved_run["activation_id"] is None


def test_local_run_blocks_missing_required_middleware_env_before_execution(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    _write_audit_log_middleware(package)
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.run_id == ""
    assert report.issues == ("missing middleware env for audit.log; required env: APPV24_WORKFLOW_AUDIT_LOG_PATH",)
    assert not run_root.exists()


def test_local_run_executes_audit_log_middleware_without_mutating_connector_outputs(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    _write_audit_log_middleware(package)
    audit_path = tmp_path / "middleware-audit.jsonl"
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
            operator_env={"APPV24_WORKFLOW_AUDIT_LOG_PATH": str(audit_path)},
        )
    )

    assert report.status == "passed"
    assert report.brokered_actions[0].simulated_outputs["ticket_id"] == "local-ticket-create_ticket_draft"
    event_types = [event.event_type for event in report.events]
    assert "middleware_runtime_configured" in event_types
    assert "middleware_hook_failed" not in event_types

    audit_records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["hook"] for record in audit_records] == [
        "run_started",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "run_completed",
    ]
    assert {record["middleware_id"] for record in audit_records} == {"audit.log"}
    assert audit_records[0]["workflow_name"] == "telegram_support_ticket"
    assert audit_records[1]["step_id"] == "check_authorized_customer"
    assert audit_records[4]["connector_action"] == "ticket.create_draft"
    assert str(audit_path) not in "\n".join(json.dumps(event.to_dict(), sort_keys=True) for event in report.events)


def test_local_run_records_stable_idempotency_keys_for_brokered_actions(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")

    first = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "first-runs",
            approve_all=True,
        )
    )
    second = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "second-runs",
            approve_all=True,
        )
    )

    first_keys = [action.idempotency_key for action in first.brokered_actions]
    second_keys = [action.idempotency_key for action in second.brokered_actions]
    assert first_keys == second_keys
    assert all(key and key.startswith("idemp:") for key in first_keys)
    assert len(set(first_keys)) == 2

    started_events = [
        event
        for event in first.events
        if event.event_type == "broker_action_started" and event.connector_action in {"ticket.create_draft", "telegram.send_message"}
    ]
    assert [event.details["idempotency_key"] for event in started_events] == first_keys


def test_local_run_rejection_path_reaches_terminal_without_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.terminal_step == "rejected"
    assert [step.step_id for step in report.step_trace] == ["check_authorized_customer", "rejected"]
    assert list((run_dir / "artifacts").glob("*.json")) == []


def test_local_policy_outputs_reflect_downstream_rejection_decision(tmp_path: Path) -> None:
    package = _write_policy_decision_package(tmp_path)

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    policy_action = report.brokered_actions[0]
    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert policy_action.connector_action == "policy.check"
    assert policy_action.broker_status == "simulated"
    assert policy_action.simulated_outputs["approved"] is False
    assert policy_action.simulated_outputs["allowed"] is False
    assert policy_action.simulated_outputs["matched_rule"] == "decision.approve_path.rejected"
    assert policy_action.simulated_outputs["reason"] == "policy rejected by fixture decision approve_path=rejected"
    assert [step.selected_branch for step in report.step_trace if step.step_id == "approve_path"] == ["rejected"]


def test_local_run_unauthorized_user_reaches_rejected_without_broker_actions(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="unauthorized_user.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.terminal_step == "rejected"
    assert [step.step_id for step in report.step_trace] == ["check_authorized_customer", "rejected"]
    assert report.brokered_actions == ()
    assert report.approvals == ()
    assert list((run_dir / "artifacts").glob("*.json")) == []


def test_local_run_ticket_draft_timeout_blocks_before_admin_notification(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="ticket_draft_timeout.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "blocked"
    assert report.terminal_step is None
    assert [(action.step_id, action.connector_action, action.broker_status) for action in report.brokered_actions] == [
        ("create_ticket_draft", "ticket.create_draft", "timeout"),
    ]
    assert report.issues == ("ticket draft connector timed out after 30s",)
    assert list((run_dir / "artifacts").glob("*.json")) == []


def test_local_run_gmail_draft_missing_recipient_blocks_without_artifact(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "gmail_draft_reply")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="missing_recipient.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        )
    )

    run_dir = run_root / report.run_id
    assert report.status == "blocked"
    assert report.terminal_step is None
    assert [(action.step_id, action.connector_action, action.broker_status) for action in report.brokered_actions] == [
        ("create_gmail_draft", "gmail.create_draft", "blocked"),
    ]
    assert report.issues == ("connector sandbox missing required provider fields for create_gmail_draft: recipient",)
    assert list((run_dir / "artifacts").glob("*.json")) == []


def test_local_run_without_approval_blocks_before_broker_action(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=False,
        )
    )

    run_dir = Path(str(report.events[0].details["run_dir"]))
    assert report.status == "blocked"
    assert report.terminal_step is None
    assert "approval required for create_ticket_draft" in report.issues
    assert list((run_dir / "artifacts").glob("*.json")) == []
    assert any(event.event_type == "approval_requested" for event in report.events)
    assert not any(event.event_type == "broker_action_started" for event in report.events)

    approvals_payload = json.loads((run_dir / "approvals.json").read_text(encoding="utf-8"))
    approval_requests = approvals_payload["approval_requests"]
    assert len(approval_requests) == 1
    assert approval_requests[0]["approval_request_id"].startswith("approval-")
    assert approval_requests[0]["run_id"] == report.run_id
    assert approval_requests[0]["package_fingerprint"] == report.package_fingerprint
    assert approval_requests[0]["step_id"] == "create_ticket_draft"
    assert approval_requests[0]["connector_action"] == "ticket.create_draft"
    assert approval_requests[0]["approval_id"] == "admin_approval"
    assert approval_requests[0]["allowed_outputs"] == ["ticket_id"]
    assert approval_requests[0]["idempotency_key_fields"] == ["telegram_user_id", "message_text"]
    assert approval_requests[0]["idempotency_key"].startswith("idemp:")
    assert approval_requests[0]["payload_hash"].startswith("payload:")
    assert approval_requests[0]["payload_preview_path"].startswith("artifacts/approvals/")
    assert approval_requests[0]["status"] == "pending"
    payload_preview = json.loads((run_dir / approval_requests[0]["payload_preview_path"]).read_text(encoding="utf-8"))
    assert payload_preview["version"] == "appv24.approval-payload-preview/v1"
    assert payload_preview["approval_request_id"] == approval_requests[0]["approval_request_id"]
    assert payload_preview["payload_hash"] == approval_requests[0]["payload_hash"]
    assert payload_preview["step_id"] == "create_ticket_draft"
    assert payload_preview["connector_action"] == "ticket.create_draft"
    assert payload_preview["idempotency_key"] == approval_requests[0]["idempotency_key"]


def test_live_production_run_is_blocked_without_creating_run_artifacts(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "telegram_support_ticket")
    run_root = tmp_path / ".workflow-runs"

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.run_id == ""
    assert report.step_trace == ()
    assert report.brokered_actions == ()
    assert report.issues == ("workflow run mode is not enabled: live-production",)
    assert not run_root.exists()


def test_local_run_intelligence_nodes_happy_path(tmp_path: Path) -> None:
    package = _write_intelligence_package(tmp_path)

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert [(action.step_id, action.connector_action, action.broker_status) for action in report.brokered_actions] == [
        ("fetch_news", "source.fetch_items", "simulated"),
        ("extract_catalysts", "llm.extract_structured", "simulated"),
        ("check_policy", "policy.check", "simulated"),
        ("create_alert_draft", "notification.create_draft", "simulated"),
    ]


def test_local_run_intelligence_nodes_rejection_path_blocks_before_notification(tmp_path: Path) -> None:
    package = _write_intelligence_package(tmp_path)

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.terminal_step is None
    assert "policy rejected for check_policy" in report.issues
    assert [(action.step_id, action.connector_action, action.broker_status) for action in report.brokered_actions] == [
        ("fetch_news", "source.fetch_items", "simulated"),
        ("extract_catalysts", "llm.extract_structured", "simulated"),
        ("check_policy", "policy.check", "blocked"),
    ]
    assert not any(action.step_id == "create_alert_draft" for action in report.brokered_actions)


def test_local_run_financial_catalyst_example_fixtures(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/financial-catalyst-alerts"

    happy = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "happy-runs",
            approve_all=True,
        )
    )
    rejection = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "rejection-runs",
            approve_all=True,
        )
    )

    assert happy.status == "passed"
    assert happy.terminal_step == "done"
    assert rejection.status == "blocked"
    assert "policy rejected for check_policy" in rejection.issues
    assert not any(action.step_id == "create_alert_draft" for action in rejection.brokered_actions)

    timeout = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="connector_timeout.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "timeout-runs",
            approve_all=True,
        )
    )
    bad_output = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="bad_llm_output.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "bad-output-runs",
            approve_all=True,
        )
    )
    missing_field = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="missing_field.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "missing-field-runs",
            approve_all=True,
        )
    )

    assert timeout.status == "blocked"
    assert timeout.brokered_actions[0].broker_status == "timeout"
    assert bad_output.status == "blocked"
    assert "do not match declared allowed_outputs" in bad_output.issues[0]
    assert missing_field.status == "blocked"
    assert missing_field.issues == ("connector sandbox missing required provider fields for fetch_news: items",)


def test_local_run_can_resume_from_paused_live_read_handoff(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/financial-catalyst-alerts"
    run_root = tmp_path / ".workflow-runs"
    seen_handoff_inputs: list[object] = []

    live_report = run_live_read_workflow(
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

    def extract_structured(request: BrokerRequest) -> BrokerResult:
        seen_handoff_inputs.append(request.fixture["handoff"]["outputs"]["fetch_news"]["items"])
        return BrokerResult(
            status="simulated",
            provider_id="resume-test",
            outputs={
                "alerts": [{"title": "Rate decision", "source_urls": ["https://example.test/rate"]}],
                "confidence_score": "medium",
                "raw_response": {"provider": "resume-test"},
            },
        )

    broker = LocalWorkflowBroker(
        {
            "llm.extract_structured": extract_structured,
            "policy.check": lambda request: BrokerResult(
                status="simulated",
                provider_id="resume-test",
                outputs={"approved": True, "reason": "policy passed"},
            ),
            "notification.create_draft": lambda request: BrokerResult(
                status="simulated",
                provider_id="resume-test",
                outputs={"draft_id": "draft-1", "artifact_path": "local://draft-1"},
            ),
        }
    )

    resumed = run_workflow_from_handoff(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=run_root,
            approve_all=True,
        ),
        handoff_run_id=live_report.run_id,
        broker=broker,
    )

    assert live_report.status == "paused"
    assert resumed.status == "passed"
    assert resumed.terminal_step == "done"
    assert [step.step_id for step in resumed.step_trace] == [
        "extract_catalysts",
        "check_policy",
        "create_alert_draft",
        "done",
    ]
    assert seen_handoff_inputs == [[{"title": "Rate decision"}]]
    saved_run = json.loads((run_root / resumed.run_id / "run.json").read_text(encoding="utf-8"))
    assert saved_run["handoff_run_id"] == live_report.run_id
    assert saved_run["handoff_next_step"] == "extract_catalysts"


def test_local_run_public_news_digest_example_fixtures(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/public-news-digest"

    happy = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "happy-runs",
            approve_all=True,
        )
    )
    rejection = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="rejection_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "rejection-runs",
            approve_all=True,
        )
    )
    timeout = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="source_timeout.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "timeout-runs",
            approve_all=True,
        )
    )
    bad_source_output = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="bad_source_output.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "bad-source-runs",
            approve_all=True,
        )
    )

    assert happy.status == "passed"
    assert happy.terminal_step == "done"
    assert [(action.step_id, action.connector_action, action.broker_status) for action in happy.brokered_actions] == [
        ("fetch_public_news", "source.fetch_document", "simulated"),
        ("summarize_news", "llm.summarize", "simulated"),
        ("check_policy", "policy.check", "simulated"),
        ("create_digest_draft", "notification.create_draft", "simulated"),
    ]
    assert rejection.status == "blocked"
    assert "policy rejected for check_policy" in rejection.issues
    assert not any(action.step_id == "create_digest_draft" for action in rejection.brokered_actions)
    assert timeout.status == "blocked"
    assert timeout.brokered_actions[0].broker_status == "timeout"
    assert timeout.issues == ("public news source timed out after 30s",)
    assert bad_source_output.status == "blocked"
    assert "do not match declared allowed_outputs" in bad_source_output.issues[0]


def test_local_run_telegram_support_ticket_example_fixtures(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = repo_root / "examples/appv24/workflows/telegram-support-ticket"

    happy = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "happy-runs",
            approve_all=True,
        )
    )
    unauthorized = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="unauthorized_user.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "unauthorized-runs",
            approve_all=True,
        )
    )
    timeout = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="ticket_draft_timeout.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / "timeout-runs",
            approve_all=True,
        )
    )

    assert happy.status == "passed"
    assert happy.terminal_step == "done"
    assert [(action.step_id, action.connector_action, action.broker_status) for action in happy.brokered_actions] == [
        ("create_ticket_draft", "ticket.create_draft", "simulated"),
        ("notify_admin", "telegram.send_message", "simulated"),
    ]
    assert unauthorized.status == "passed"
    assert unauthorized.terminal_step == "rejected"
    assert unauthorized.brokered_actions == ()
    assert timeout.status == "blocked"
    assert timeout.brokered_actions[0].broker_status == "timeout"
    assert timeout.issues == ("ticket draft connector timed out after 30s",)


def test_local_run_connector_timeout_fixture_blocks_at_requested_step(tmp_path: Path) -> None:
    package = _write_intelligence_package(tmp_path)
    fixture_path = package / "dry_run_fixtures" / "connector_timeout.yaml"
    fixture_path.write_text(
        yaml.safe_dump(
            {
                "connector_sandbox": {
                    "failures": {
                        "fetch_news": {
                            "status": "timeout",
                            "message": "news connector timed out after 30s",
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="connector_timeout.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.terminal_step is None
    assert report.brokered_actions[0].step_id == "fetch_news"
    assert report.brokered_actions[0].broker_status == "timeout"
    assert report.brokered_actions[0].broker_provider_id == "connector_sandbox"
    assert report.issues == ("news connector timed out after 30s",)


def test_local_run_bad_llm_output_fixture_blocks_on_output_contract(tmp_path: Path) -> None:
    package = _write_intelligence_package(tmp_path)
    fixture_path = package / "dry_run_fixtures" / "bad_llm_output.yaml"
    fixture_path.write_text(
        yaml.safe_dump(
            {
                "connector_sandbox": {
                    "outputs": {
                        "extract_catalysts": {
                            "unexpected": "not allowed by declared output contract",
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="bad_llm_output.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.brokered_actions[-1].step_id == "extract_catalysts"
    assert report.brokered_actions[-1].broker_status == "blocked"
    assert report.brokered_actions[-1].broker_provider_id == "connector_sandbox"
    assert "do not match declared allowed_outputs" in report.issues[0]


def test_local_run_missing_field_fixture_blocks_before_next_step(tmp_path: Path) -> None:
    package = _write_intelligence_package(tmp_path)
    fixture_path = package / "dry_run_fixtures" / "missing_field.yaml"
    fixture_path.write_text(
        yaml.safe_dump(
            {
                "connector_sandbox": {
                    "missing_fields": {
                        "fetch_news": ["items"],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = run_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="missing_field.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.brokered_actions[0].step_id == "fetch_news"
    assert report.brokered_actions[0].broker_status == "blocked"
    assert report.brokered_actions[0].broker_provider_id == "connector_sandbox"
    assert report.issues == ("connector sandbox missing required provider fields for fetch_news: items",)
