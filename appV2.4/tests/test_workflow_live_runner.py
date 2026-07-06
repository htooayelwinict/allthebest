from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.bundle_export import write_workflow_bundle
from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.live_runner import run_live_draft_workflow, run_live_production_workflow, run_live_read_workflow
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import run_workflow


class RecordingTransport(LiveWorkflowTransport):
    def __init__(self, outputs: dict[str, object] | None = None, *, status: str = "live-read") -> None:
        self.requests: list[BrokerRequest] = []
        self.outputs = outputs if outputs is not None else {"alerts": [], "secret": "blocked"}
        self.status = status

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(status=self.status, provider_id="fake-live", outputs=self.outputs)


def test_live_read_runner_executes_allowlisted_read_action_and_writes_report(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    transport = RecordingTransport()
    run_root = tmp_path / ".workflow-runs"

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=run_root,
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        broker=LiveWorkflowBroker({"github.list_dependabot_alerts": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.mode is WorkflowRunMode.LIVE_READ
    assert report.terminal_step == "done"
    assert [trace.step_id for trace in report.step_trace] == ["inspect_alerts", "done"]
    assert report.brokered_actions[0].connector_action == "github.list_dependabot_alerts"
    assert report.brokered_actions[0].broker_status == "live-read"
    assert report.brokered_actions[0].simulated_outputs == {"alerts": []}
    assert transport.requests[0].dry_run is False
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "trace.json").is_file()
    assert (run_dir / "events.jsonl").is_file()
    saved_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved_report["mode"] == "live-read"
    assert saved_report["status"] == "passed"
    assert any(event.event_type == "credential_preflight_passed" for event in report.events)
    assert any(event.event_type == "live_gate_passed" for event in report.events)
    assert _event_names(report.events)[:8] == (
        "run_created",
        "package_validation_passed",
        "compile_passed",
        "policy_passed",
        "bundle_guard_passed",
        "credential_preflight_passed",
        "live_gate_passed",
        "broker_preflight_passed",
    )


def test_live_read_runner_blocks_missing_configured_transport_before_broker_dispatch(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        broker=LiveWorkflowBroker({}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "blocked"
    assert report.issues == (
        "live-read transport is not configured for github.list_dependabot_alerts; required env: "
        "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
    )
    assert any(event.event_type == "live_gate_blocked" for event in report.events)


def test_live_read_runner_blocks_stale_bundle_before_transport(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    write_workflow_bundle(package)
    (package / "rollback.md").write_text("# Rollback\n\nChanged after bundle export.\n", encoding="utf-8")
    transport = RecordingTransport()

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        broker=LiveWorkflowBroker({"github.list_dependabot_alerts": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "blocked"
    assert any(issue.startswith("workflow bundle check failed: source package fingerprint changed") for issue in report.issues)
    assert transport.requests == []


def test_live_read_runner_blocks_wrong_broker_mode_before_transport(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    transport = RecordingTransport()

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        broker=LiveWorkflowBroker({"github.list_dependabot_alerts": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-read requires live-read broker mode",)
    assert transport.requests == []


def test_live_read_runner_blocks_missing_credential_before_transport_invocation(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    transport = RecordingTransport()

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.list_dependabot_alerts",),
        ),
        broker=LiveWorkflowBroker({"github.list_dependabot_alerts": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({}),
    )

    assert report.status == "blocked"
    assert report.issues == (
        "missing credential ref for github; required env: APPV24_GITHUB_CREDENTIAL_REF, APPV24_GITHUB_SCOPES=dependabot:read",
    )
    assert transport.requests == []
    assert any(event.event_type == "credential_preflight_blocked" for event in report.events)


def test_live_read_runner_blocks_mutating_steps(tmp_path: Path) -> None:
    package = _write_mutating_package(tmp_path)
    transport = RecordingTransport(outputs={"issue_url": "https://example.invalid/1"})

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.create_issue",),
            approve_all=True,
        ),
        broker=LiveWorkflowBroker({"github.create_issue": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("issues:write",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-read cannot execute mutating step draft_issue",)
    assert transport.requests == []


def test_live_read_runner_blocks_actions_missing_from_request_allowlist(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    transport = RecordingTransport()

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("github.other",),
        ),
        broker=LiveWorkflowBroker({"github.list_dependabot_alerts": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("dependabot:read",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-read action is not allowlisted: github.list_dependabot_alerts",)
    assert transport.requests == []


def test_live_read_runner_blocks_catalog_actions_not_live_read_eligible(tmp_path: Path) -> None:
    package = _write_non_live_read_catalog_action_package(tmp_path)
    transport = RecordingTransport({"items": [], "count": 0, "raw_response": {}}, status="live-read")

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("llm.extract_structured",),
        ),
        broker=LiveWorkflowBroker({"llm.extract_structured": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"llm": CredentialScope(available=True, scopes=())}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-read action is not live-read eligible: llm.extract_structured",)
    assert transport.requests == []


def test_live_read_runner_pauses_after_live_read_prefix_before_non_live_read_step(tmp_path: Path) -> None:
    package = _write_source_then_llm_package(tmp_path)
    transport = RecordingTransport({"items": [{"title": "Rate decision"}], "source_count": 1}, status="live-read")

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("source.fetch_items", "llm.extract_structured"),
        ),
        broker=LiveWorkflowBroker({"source.fetch_items": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "source": CredentialScope(available=True, scopes=("source:read",)),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
    )

    assert report.status == "paused"
    assert report.terminal_step is None
    assert report.issues == ("live-read prefix completed before non-live-read step extract_catalysts",)
    assert [trace.step_id for trace in report.step_trace] == ["fetch_news", "extract_catalysts"]
    assert report.step_trace[0].status == "completed"
    assert report.step_trace[1].status == "paused"
    assert report.brokered_actions[0].connector_action == "source.fetch_items"
    assert report.brokered_actions[0].simulated_outputs == {"items": [{"title": "Rate decision"}], "source_count": 1}
    assert transport.requests[0].dry_run is False
    assert any(event.event_type == "step_paused" for event in report.events)
    handoff = json.loads(((tmp_path / ".workflow-runs") / report.run_id / "handoff.json").read_text(encoding="utf-8"))
    assert handoff == {
        "version": "appv24.workflow-handoff/v1",
        "run_id": report.run_id,
        "workflow_name": "financial-catalyst-alerts",
        "package_fingerprint": report.package_fingerprint,
        "from_mode": "live-read",
        "status": "ready",
        "completed_steps": ["fetch_news"],
        "next_step": "extract_catalysts",
        "next_phase": "local",
        "available_outputs": [
            {
                "step_id": "fetch_news",
                "connector_action": "source.fetch_items",
                "output_keys": ["items", "source_count"],
                "trace_ref": "trace.json#fetch_news",
            }
        ],
    }


def test_live_read_runner_pauses_after_live_read_prefix_before_mutating_step(tmp_path: Path) -> None:
    package = _write_source_then_mutating_package(tmp_path)
    transport = RecordingTransport({"items": [{"title": "Unauthorized login"}], "source_count": 1}, status="live-read")

    report = run_live_read_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("source.fetch_items", "telegram.send_message"),
        ),
        broker=LiveWorkflowBroker({"source.fetch_items": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "source": CredentialScope(available=True, scopes=("source:read",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
            }
        ),
    )

    assert report.status == "paused"
    assert report.issues == ("live-read prefix completed before non-live-read step notify_admin",)
    assert [trace.step_id for trace in report.step_trace] == ["fetch_events", "notify_admin"]
    assert report.step_trace[1].status == "paused"
    assert report.brokered_actions[0].connector_action == "source.fetch_items"
    assert transport.requests[0].dry_run is False
    assert any(event.event_type == "step_paused" for event in report.events)
    handoff = json.loads(((tmp_path / ".workflow-runs") / report.run_id / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["next_step"] == "notify_admin"
    assert handoff["next_phase"] == "live-production"


def test_workflows_package_exports_live_draft_runner() -> None:
    from appv24.workflows import run_live_draft_workflow as exported

    assert exported is run_live_draft_workflow


def test_workflows_package_exports_live_production_runner() -> None:
    from appv24.workflows import run_live_production_workflow as exported

    assert exported is run_live_production_workflow


def test_live_draft_runner_executes_approved_draft_action_and_writes_report(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport(
        {"draft_id": "draft-1", "thread_id": "thread-1", "secret": "blocked"},
        status="live-draft",
    )

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.mode is WorkflowRunMode.LIVE_DRAFT
    assert report.terminal_step == "done"
    assert report.brokered_actions[0].connector_action == "gmail.create_draft"
    assert report.brokered_actions[0].broker_status == "live-draft"
    assert report.brokered_actions[0].simulated_outputs == {"draft_id": "draft-1", "thread_id": "thread-1"}
    assert report.brokered_actions[0].idempotency_key.startswith("idemp:")
    assert transport.requests[0].dry_run is False
    assert (run_dir / "report.json").is_file()
    idempotency_events = [event for event in report.events if event.event_type == "idempotency_reserved"]
    assert len(idempotency_events) == 1
    assert idempotency_events[0].step_id == "create_gmail_draft"
    assert idempotency_events[0].details["idempotency_key"].startswith("idemp:")
    saved_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved_report["mode"] == "live-draft"
    assert saved_report["status"] == "passed"
    assert any(event.event_type == "approval_preflight_passed" for event in report.events)


def test_live_draft_runner_blocks_wrong_broker_mode_before_transport(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport({"draft_id": "draft-1"}, status="live-draft")

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-draft requires live-draft broker mode",)
    assert transport.requests == []


def test_live_draft_runner_blocks_non_local_approval_run_before_transport(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    _set_run_mode(run_root, approval_report.run_id, "preview")
    transport = RecordingTransport({"draft_id": "draft-1"}, status="live-draft")

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-draft requires approval_run_id from a local run",)
    assert transport.requests == []


def test_live_draft_runner_requires_prior_approval_run_id(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
    transport = RecordingTransport({"draft_id": "draft-1"}, status="live-draft")

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-draft requires approval_run_id",)
    assert transport.requests == []


def test_live_draft_runner_rejects_denied_prior_approval(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
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
    _deny_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport({"draft_id": "draft-1"}, status="live-draft")

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker({"gmail.create_draft": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("approval not granted for create_gmail_draft",)
    assert transport.requests == []


def test_live_draft_runner_blocks_non_draft_eligible_mutation(tmp_path: Path) -> None:
    package = _write_mutating_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport({"issue_url": "https://example.invalid/1"}, status="live-draft")

    report = run_live_draft_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_DRAFT,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("github.create_issue",),
        ),
        broker=LiveWorkflowBroker({"github.create_issue": transport}, mode="live-draft"),
        credential_resolver=CredentialRefResolver({"github": CredentialScope(available=True, scopes=("issues:write",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-draft action is not draft-eligible: github.create_issue",)
    assert transport.requests == []


def test_live_production_runner_executes_approved_telegram_send_and_writes_report(tmp_path: Path) -> None:
    package = _write_production_telegram_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport(
        {"message_id": "msg-1", "chat_id": "admin-chat", "secret": "blocked"},
        status="live-production",
    )

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    run_dir = run_root / report.run_id
    assert report.status == "passed"
    assert report.mode is WorkflowRunMode.LIVE_PRODUCTION
    assert report.terminal_step == "done"
    assert report.brokered_actions[0].connector_action == "telegram.send_message"
    assert report.brokered_actions[0].broker_status == "live-production"
    assert report.brokered_actions[0].simulated_outputs == {"message_id": "msg-1", "chat_id": "admin-chat"}
    assert report.brokered_actions[0].idempotency_key.startswith("idemp:")
    assert transport.requests[0].dry_run is False
    assert (run_dir / "report.json").is_file()
    idempotency_events = [event for event in report.events if event.event_type == "idempotency_reserved"]
    assert len(idempotency_events) == 1
    assert idempotency_events[0].step_id == "notify_admin"
    assert idempotency_events[0].details["idempotency_key"].startswith("idemp:")
    saved_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert saved_report["mode"] == "live-production"
    assert saved_report["status"] == "passed"
    assert any(event.event_type == "approval_preflight_passed" for event in report.events)


def test_live_production_runner_blocks_non_local_approval_run_before_transport(tmp_path: Path) -> None:
    package = _write_production_telegram_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    _set_run_mode(run_root, approval_report.run_id, "preview")
    transport = RecordingTransport({"message_id": "msg-1"}, status="live-production")

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-production requires approval_run_id from a local run",)
    assert transport.requests == []


def test_live_production_runner_blocks_wrong_broker_mode_before_transport(tmp_path: Path) -> None:
    package = _write_production_telegram_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport({"message_id": "msg-1"}, status="live-production")

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("telegram.send_message",),
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-draft",
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-production requires live-production broker mode",)
    assert transport.requests == []


def test_live_production_runner_requires_prior_approval_run_id(tmp_path: Path) -> None:
    package = _write_production_telegram_package(tmp_path)
    transport = RecordingTransport({"message_id": "msg-1"}, status="live-production")

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=tmp_path / ".workflow-runs",
            connector_allowlist=("telegram.send_message",),
        ),
        broker=LiveWorkflowBroker(
            {"telegram.send_message": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"telegram": CredentialScope(available=True, scopes=("telegram:send",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-production requires approval_run_id",)
    assert transport.requests == []


def test_live_production_runner_blocks_non_production_enabled_mutation(tmp_path: Path) -> None:
    package = _write_draft_package(tmp_path)
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
    _approve_first_request(run_root, approval_report.run_id, package_fingerprint=approval_report.package_fingerprint)
    transport = RecordingTransport({"draft_id": "draft-1"}, status="live-production")

    report = run_live_production_workflow(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=run_root,
            approval_run_id=approval_report.run_id,
            connector_allowlist=("gmail.create_draft",),
        ),
        broker=LiveWorkflowBroker(
            {"gmail.create_draft": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver({"gmail": CredentialScope(available=True, scopes=("gmail.compose",))}),
    )

    assert report.status == "blocked"
    assert report.issues == ("live-production action is not production-enabled: gmail.create_draft",)
    assert transport.requests == []


def _write_read_only_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "dependency-alert-read",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "inspect_alerts",
            "steps": [
                {"id": "inspect_alerts", "uses": "github.list_dependabot_alerts", "next": "done"},
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=[]),
        },
        connectors={
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
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "github", "actions": ["list_dependabot_alerts"], "scopes": ["dependabot:read"]}],
        },
    )


def _write_non_live_read_catalog_action_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "llm-transform-live-read-block",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "extract_fields",
            "steps": [
                {
                    "id": "extract_fields",
                    "uses": "llm.extract_structured",
                    "with": {"output_contract": "items_v1"},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=[]),
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "llm",
                    "adapter": "llm",
                    "actions": [
                        {
                            "id": "extract_structured",
                            "side_effect": False,
                            "outputs": {"allowed": ["items", "count", "raw_response"]},
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "llm", "actions": ["extract_structured"], "scopes": []}],
        },
    )


def _write_source_then_llm_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "financial-catalyst-alerts",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "fetch_news",
            "steps": [
                {
                    "id": "fetch_news",
                    "uses": "source.fetch_items",
                    "with": {"query": "financial catalysts"},
                    "next": "extract_catalysts",
                },
                {
                    "id": "extract_catalysts",
                    "uses": "llm.extract_structured",
                    "with": {"output_contract": "items_v1"},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=[]),
        },
        connectors={
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
                            "id": "extract_structured",
                            "side_effect": False,
                            "outputs": {"allowed": ["items", "count", "raw_response"]},
                        }
                    ],
                },
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [
                {"connector": "source", "actions": ["fetch_items"], "scopes": ["source:read"]},
                {"connector": "llm", "actions": ["extract_structured"], "scopes": []},
            ],
        },
    )


def _write_source_then_mutating_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "source-then-admin-alert",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "fetch_events",
            "steps": [
                {
                    "id": "fetch_events",
                    "uses": "source.fetch_items",
                    "with": {"query": "security events"},
                    "next": "notify_admin",
                },
                {
                    "id": "notify_admin",
                    "uses": "telegram.send_message",
                    "side_effect_class": "send",
                    "approval": "admin-review",
                    "with": {
                        "chat_id": {"inputRef": "chat_id"},
                        "text": {"inputRef": "text"},
                    },
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=["Send Telegram message after approval"], approval_gates=["admin-review"]),
        },
        connectors={
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
                    "id": "telegram",
                    "adapter": "openapi",
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
                },
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "approvals": [{"id": "admin-review", "mode": "explicit", "required_for": ["notify_admin"]}],
            "permissions": [
                {"connector": "source", "actions": ["fetch_items"], "scopes": ["source:read"]},
                {"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]},
            ],
        },
    )


def _write_draft_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "gmail-draft-live",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "create_gmail_draft",
            "steps": [
                {
                    "id": "create_gmail_draft",
                    "uses": "gmail.create_draft",
                    "side_effect_class": "create",
                    "approval": "human-review",
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=["Create Gmail draft after approval"], approval_gates=["human-review"]),
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "gmail",
                    "adapter": "openapi",
                    "actions": [
                        {
                            "id": "create_draft",
                            "operationId": "gmail_drafts_create",
                            "side_effect": True,
                            "side_effect_class": "create",
                            "execution_mode": "brokered",
                            "approval_required": True,
                            "dry_run_supported": True,
                            "idempotency": {"key_fields": ["thread_id", "body"]},
                            "outputs": {"allowed": ["draft_id", "thread_id"]},
                            "required_scopes": ["gmail.compose"],
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "approvals": [{"id": "human-review", "mode": "explicit", "required_for": ["create_gmail_draft"]}],
            "permissions": [{"connector": "gmail", "actions": ["create_draft"], "scopes": ["gmail.compose"]}],
        },
    )


def _write_production_telegram_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
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
                    "with": {
                        "chat_id": {"inputRef": "chat_id"},
                        "text": {"inputRef": "text"},
                    },
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=["Send Telegram message after approval"], approval_gates=["admin-review"]),
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "telegram",
                    "adapter": "openapi",
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
        permissions={
            "version": "appv24.permissions/v1",
            "approvals": [{"id": "admin-review", "mode": "explicit", "required_for": ["notify_admin"]}],
            "permissions": [{"connector": "telegram", "actions": ["send_message"], "scopes": ["telegram:send"]}],
        },
    )


def _write_mutating_package(root: Path) -> Path:
    return _write_package(
        root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "dependency-alert-issue",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "draft_issue",
            "steps": [
                {
                    "id": "draft_issue",
                    "uses": "github.create_issue",
                    "side_effect_class": "create",
                    "approval": "human-review",
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
            "design": _design(side_effects=["Create GitHub issue after approval"], approval_gates=["human-review"]),
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "github",
                    "adapter": "openapi",
                    "actions": [
                        {
                            "id": "create_issue",
                            "operationId": "issues_create",
                            "side_effect": True,
                            "side_effect_class": "create",
                            "execution_mode": "brokered",
                            "approval_required": True,
                            "dry_run_supported": True,
                            "idempotency": {"key_fields": ["repo", "alert_id"]},
                            "outputs": {"allowed": ["issue_url"]},
                            "required_scopes": ["issues:write"],
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "approvals": [{"id": "human-review", "mode": "explicit", "required_for": ["draft_issue"]}],
            "permissions": [{"connector": "github", "actions": ["create_issue"], "scopes": ["issues:write"]}],
        },
    )


def _write_package(root: Path, *, workflow: dict[str, object], connectors: dict[str, object], permissions: dict[str, object]) -> Path:
    package = root / "package"
    package.mkdir(parents=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(connectors, sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(permissions, sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nReview the audit trail manually.\n", encoding="utf-8")
    return package


def _approve_first_request(run_root: Path, run_id: str, *, package_fingerprint: str) -> None:
    store = WorkflowRunStore(run_root)
    request = store.read_approval_requests(run_id)[0]
    store.record_approval_decision(
        run_id,
        request.approval_request_id,
        ApprovalDecision(approved=True, package_fingerprint=package_fingerprint, decided_by="Lewis"),
    )


def _deny_first_request(run_root: Path, run_id: str, *, package_fingerprint: str) -> None:
    store = WorkflowRunStore(run_root)
    request = store.read_approval_requests(run_id)[0]
    store.record_approval_decision(
        run_id,
        request.approval_request_id,
        ApprovalDecision(approved=False, package_fingerprint=package_fingerprint, decided_by="Lewis"),
    )


def _set_run_mode(run_root: Path, run_id: str, mode: str) -> None:
    path = run_root / run_id / "run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mode"] = mode
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _event_names(events: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(str(event.event_type) for event in events)


def _design(*, side_effects: list[str], approval_gates: list[str] | None = None) -> dict[str, object]:
    return {
        "intent": "Review dependency alerts.",
        "trigger": "Manual operator run.",
        "inputs": ["repo"],
        "outputs": ["alerts"],
        "assumptions": ["Credentials are resolved outside the package."],
        "side_effects": side_effects,
        "approval_gates": approval_gates or [],
        "failure_modes": ["Provider unavailable."],
        "rollback": ["Review the audit trail manually."],
    }
