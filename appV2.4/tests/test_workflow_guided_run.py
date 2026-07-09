from __future__ import annotations

from pathlib import Path
import time

import yaml

from appv24.workflows.approval_gate import ApprovalDecision
from appv24.workflows.broker import BrokerRequest, BrokerResult, LocalWorkflowBroker
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.guided_run import (
    approve_latest_guided_workflow_run,
    approve_and_continue_guided_workflow_run,
    approve_or_prepare_guided_workflow_run,
    continue_guided_workflow_run,
    format_guided_workflow_run_report,
    prepare_guided_workflow_run,
)
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.middleware_runtime import MiddlewareExecutor
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
        if request.connector_action == "crm.send_customer_update":
            return BrokerResult(
                status="live-production",
                provider_id="fake-crm",
                outputs={"message_id": "crm-msg-1"},
            )
        return BrokerResult(
            status="live-production",
            provider_id="fake-telegram",
            outputs={"sent_count": 1, "message_ids": ["msg-1"], "chat_ids": ["admin-chat"]},
        )


class _NoopRedactorMiddleware(MiddlewareExecutor):
    middleware_id = "team.redactor"

    def emit(self, hook, context, details) -> None:
        return None


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
    assert "next commands:" in rendered
    assert f"- /workflow-approve {package.resolve()} happy_path.yaml" in rendered


def test_prepare_guided_workflow_run_shows_extension_prd_prompt_for_missing_connector_qa(tmp_path: Path) -> None:
    package = _write_generated_crm_package(tmp_path)

    report = prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker({}, mode="live-read"),
        credential_resolver=CredentialRefResolver({}),
    )
    rendered = format_guided_workflow_run_report(report)

    assert report.status == "blocked"
    assert report.extension_handoff_path == package / ".workflow-extension-handoff" / "extension-handoff.manifest.json"
    assert report.extension_handoff_path.exists()
    assert len(report.extension_prd_prompts) == 1
    assert "# appv24 connector extension PRD: crm.lookup_contact" in rendered
    assert f"extension handoff: {report.extension_handoff_path}" in rendered
    assert "handoff mode: coding-profile-command" in rendered
    assert "dispatch status: pending_coding_profile" in rendered
    assert "dispatch outbox:" in rendered
    assert "dispatch script:" in rendered
    assert "queued_for_coding_profile" in rendered
    assert "QA return targets:" in rendered
    assert "connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa" in rendered
    assert "QA return manifest paths:" in rendered
    assert "returned-qa/crm.lookup_contact.qa.yaml" in rendered
    assert "Runtime must not call the coding profile" in rendered
    assert "next action: build extension with coding profile and attach QA manifest" in rendered


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
    rendered = format_guided_workflow_run_report(report)
    assert f"- /workflow-approve {package.resolve()} happy_path.yaml" in rendered


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


def test_approve_and_continue_guided_workflow_run_records_decision_and_runs_production(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    prepare_report = _prepare_package_to_approval(package)
    assert prepare_report.approval_run_id is not None
    approval_run_id = prepare_report.approval_run_id
    approval_request_id = prepare_report.approval_request_ids[0]
    transport = _ProductionTransport()

    report = approve_and_continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
        approval_run_id=approval_run_id,
        approval_request_id=approval_request_id,
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
        decided_by="test",
        readiness_inspector=lambda request, env: "ready",
    )

    decisions = WorkflowRunStore(package / ".workflow-runs").read_approval_decisions(approval_run_id)
    assert decisions[-1]["approval_request_id"] == approval_request_id
    assert decisions[-1]["status"] == "approved"
    assert report.status == "passed"
    assert report.readiness_status == "ready"
    assert report.approval_run_id == approval_run_id
    assert transport.requests[0].connector_action == "telegram.broadcast_message"


def test_approve_latest_guided_workflow_run_uses_latest_pending_request(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    first_prepare_report = _prepare_package_to_approval(package)
    time.sleep(0.001)
    prepare_report = _prepare_package_to_approval(package)
    assert first_prepare_report.approval_run_id is not None
    assert prepare_report.approval_run_id is not None
    assert first_prepare_report.approval_run_id != prepare_report.approval_run_id
    transport = _ProductionTransport()

    report = approve_latest_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
            operator_env={"APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat"},
        ),
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
        decided_by="test",
        readiness_inspector=lambda request, env: "ready",
    )

    store = WorkflowRunStore(package / ".workflow-runs")
    first_decisions = store.read_approval_decisions(first_prepare_report.approval_run_id)
    decisions = store.read_approval_decisions(prepare_report.approval_run_id)
    assert first_decisions == ()
    assert decisions[-1]["approval_request_id"] == prepare_report.approval_request_ids[0]
    assert decisions[-1]["status"] == "approved"
    assert report.status == "passed"
    assert report.approval_run_id == prepare_report.approval_run_id
    assert report.readiness_status == "ready"
    assert transport.requests[0].connector_action == "telegram.broadcast_message"


def test_approve_or_prepare_guided_workflow_run_prepares_when_no_pending_request(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)

    report = approve_or_prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": _ReadTransport(),
                "telegram.list_subscribers": _ReadTransport(),
            },
            mode="live-read",
        ),
        live_production_broker=LiveWorkflowBroker(
            {"telegram.broadcast_message": _ProductionTransport()},
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
        env={},
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

    assert report.status == "approval-required"
    assert report.approval_run_id is not None
    assert len(report.approval_request_ids) == 1
    assert report.next_action == "approve external side effect"
    store = WorkflowRunStore(package / ".workflow-runs")
    requests = store.read_approval_requests(report.approval_run_id)
    assert requests[0].status == "pending"
    assert requests[0].connector_action == "telegram.broadcast_message"


def test_approve_or_prepare_guided_workflow_run_applies_returned_extension_qa_before_prepare(tmp_path: Path) -> None:
    package = _write_generated_crm_send_package(tmp_path)
    connectors_path = package / "connectors.yaml"
    connectors = yaml.safe_load(connectors_path.read_text(encoding="utf-8"))
    connectors["connectors"][1]["actions"][0].pop("qa")
    connectors_path.write_text(yaml.safe_dump(connectors, sort_keys=False), encoding="utf-8")
    blocked = prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker({"brave.search": _ReadTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "crm": CredentialScope(available=True, scopes=("crm:send",)),
            }
        ),
    )
    assert blocked.status == "blocked"
    assert blocked.extension_handoff_path is not None
    returned_dir = package / ".workflow-extension-handoff" / "returned-qa"
    assert returned_dir.is_dir()
    returned_qa = {
        "version": "appv24.extension_qa/v1",
        "kind": "connector",
        "extension_id": "crm.send_customer_update",
        "source": "mcp://crm.send_customer_update@v1",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": "pytest appV2.4/tests/test_crm_send_connector.py", "status": "passed"}],
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
    }
    (returned_dir / "crm.send_customer_update.qa.yaml").write_text(
        yaml.safe_dump(returned_qa, sort_keys=False),
        encoding="utf-8",
    )

    report = approve_or_prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker({"brave.search": _ReadTransport()}, mode="live-read"),
        live_production_broker=LiveWorkflowBroker(
            {"crm.send_customer_update": _ProductionTransport()},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "crm": CredentialScope(available=True, scopes=("crm:send",)),
            }
        ),
        env={},
    )

    assert report.status == "approval-required"
    assert report.extension_qa_apply_report
    rendered = format_guided_workflow_run_report(report)
    assert "Workflow extension returned QA apply: applied" in rendered
    assert f"- /workflow-approve {package.resolve()} happy_path.yaml" in rendered
    assert "crm.send_customer_update.qa.yaml" in rendered
    assert "extension catalog choice:" in rendered
    assert "keep package-only: no command needed" in rendered
    assert (
        f"/workflow-extension-promote {package.resolve()} "
        f"{(tmp_path / 'workflow-extension-catalog.yaml').resolve()} crm.send_customer_update"
    ) in rendered
    assert f"/workflow-checkin {(tmp_path / 'workflow-extension-catalog.yaml').resolve()}" in rendered
    updated = yaml.safe_load(connectors_path.read_text(encoding="utf-8"))
    assert updated["connectors"][1]["actions"][0]["qa"]["status"] == "passed"


def test_approve_or_prepare_guided_workflow_run_applies_returned_middleware_qa_before_prepare(tmp_path: Path) -> None:
    package = _write_ai_news_broadcast_package(tmp_path)
    middlewares_path = package / "middlewares.yaml"
    middlewares_path.write_text(
        yaml.safe_dump(
            {
                "version": "appv24.middlewares/v1",
                "middlewares": [
                    {
                        "id": "team.redactor",
                        "adapter": "local",
                        "source": "local://middlewares/team/redactor@sha256:abc123",
                        "env": [
                            {
                                "name": "APPV24_TEAM_REDACTOR_LEVEL",
                                "purpose": "redaction level",
                                "required": True,
                            }
                        ],
                        "audit": ["middleware:team.redactor"],
                        "workflow_hints": {
                            "business_goal": "Redact sensitive fields from workflow audit events.",
                            "generated_by": "coding_profile",
                            "qa_required": True,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    blocked = prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker({"brave.search": _ReadTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "telegram": CredentialScope(available=True, scopes=("telegram:subscribers:read", "telegram:send")),
                "llm": CredentialScope(available=True, scopes=()),
            }
        ),
    )
    assert blocked.status == "blocked"
    assert blocked.extension_handoff_path is not None
    returned_dir = package / ".workflow-extension-handoff" / "returned-qa"
    assert returned_dir.is_dir()
    (returned_dir / "team.redactor.qa.yaml").write_text(
        yaml.safe_dump(_passed_middleware_qa("team.redactor"), sort_keys=False),
        encoding="utf-8",
    )

    report = approve_or_prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
            operator_env={"APPV24_TEAM_REDACTOR_LEVEL": "strict"},
        ),
        live_read_broker=LiveWorkflowBroker(
            {
                "brave.search": _ReadTransport(),
                "telegram.list_subscribers": _ReadTransport(),
            },
            mode="live-read",
        ),
        live_production_broker=LiveWorkflowBroker(
            {"telegram.broadcast_message": _ProductionTransport()},
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
        env={},
        local_broker=LocalWorkflowBroker(
            {
                "llm.summarize": lambda request: BrokerResult(
                    status="simulated",
                    provider_id="fake-llm",
                    outputs={"summary": "AI digest"},
                )
            }
        ),
        middleware_factories={"team.redactor": lambda definition, env: _NoopRedactorMiddleware()},
    )

    assert report.status == "approval-required"
    assert report.extension_qa_apply_report
    rendered = format_guided_workflow_run_report(report)
    assert "Workflow extension returned QA apply: applied" in rendered
    assert "team.redactor.qa.yaml" in rendered
    assert f"- /workflow-approve {package.resolve()} happy_path.yaml" in rendered
    assert "extension catalog choice:" in rendered
    assert "keep package-only: no command needed" in rendered
    assert (
        f"/workflow-extension-promote {package.resolve()} "
        f"{(tmp_path / 'workflow-extension-catalog.yaml').resolve()} team.redactor"
    ) in rendered
    assert f"/workflow-checkin {(tmp_path / 'workflow-extension-catalog.yaml').resolve()}" in rendered
    updated = yaml.safe_load(middlewares_path.read_text(encoding="utf-8"))
    assert updated["middlewares"][0]["qa"]["status"] == "passed"


def test_guided_run_executes_qa_passed_custom_live_production_connector(tmp_path: Path) -> None:
    package = _write_generated_crm_send_package(tmp_path)
    prepare_report = prepare_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            run_root=package / ".workflow-runs",
        ),
        live_read_broker=LiveWorkflowBroker({"brave.search": _ReadTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "crm": CredentialScope(available=True, scopes=("crm:send",)),
            }
        ),
    )
    assert prepare_report.status == "approval-required"
    assert prepare_report.approval_run_id is not None
    approval_run_id = prepare_report.approval_run_id
    approval_request_id = prepare_report.approval_request_ids[0]
    transport = _ProductionTransport()

    report = approve_and_continue_guided_workflow_run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_PRODUCTION,
            run_root=package / ".workflow-runs",
        ),
        approval_run_id=approval_run_id,
        approval_request_id=approval_request_id,
        live_production_broker=LiveWorkflowBroker(
            {"crm.send_customer_update": transport},
            mode="live-production",
            production_enabled=True,
        ),
        credential_resolver=CredentialRefResolver(
            {
                "brave": CredentialScope(available=True, scopes=("brave:search",)),
                "crm": CredentialScope(available=True, scopes=("crm:send",)),
            }
        ),
        env={"APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true"},
        decided_by="test",
        readiness_inspector=lambda request, env: "ready",
    )

    assert report.status == "passed"
    assert report.readiness_status == "ready"
    assert transport.requests[0].connector_action == "crm.send_customer_update"


def test_workflows_package_exports_guided_approve_and_continue() -> None:
    from appv24.workflows import approve_and_continue_guided_workflow_run as exported

    assert exported is approve_and_continue_guided_workflow_run


def test_workflows_package_exports_guided_approve_latest() -> None:
    from appv24.workflows import approve_latest_guided_workflow_run as exported

    assert exported is approve_latest_guided_workflow_run


def test_workflows_package_exports_guided_approve_or_prepare() -> None:
    from appv24.workflows import approve_or_prepare_guided_workflow_run as exported

    assert exported is approve_or_prepare_guided_workflow_run


def test_workflows_package_exports_extension_connector_contract_catalog() -> None:
    from appv24.workflows import extension_connector_contract_catalog as exported
    from appv24.workflows.workflow_extensions import extension_connector_contract_catalog

    assert exported is extension_connector_contract_catalog


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
    next: summarize_news
  - id: summarize_news
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
      text: "{{ steps.summarize_news.outputs.summary }}"
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


def _write_generated_crm_package(root: Path) -> Path:
    package = root / "generated-crm"
    (package / "dry_run_fixtures").mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        """version: appv24.workflow/v1
name: generated-crm
description: Look up a CRM contact using a generated connector.
design:
  intent: CRM lookup
  trigger: manual
  inputs: [email]
  outputs: [contact]
  assumptions: []
  side_effects: []
  approval_gates: []
  failure_modes: []
  rollback: [No external mutation occurs.]
triggers:
  - id: manual
    type: manual
entrypoint: lookup_contact
steps:
  - id: lookup_contact
    uses: crm.lookup_contact
    with:
      email: "{{ inputs.email }}"
    next: done
  - id: done
    type: terminal
""",
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        """version: appv24.connectors/v1
connectors:
  - id: crm
    adapter: mcp
    custom: true
    source: mcp://crm@v1
    env:
      - name: APPV24_CRM_MCP_SERVER
        purpose: CRM MCP server endpoint
        required: true
    actions:
      - id: lookup_contact
        custom: true
        source: mcp://crm.lookup_contact@v1
        tool: crm.lookup_contact
        side_effect: false
        side_effect_class: read
        execution_mode: brokered
        dry_run_supported: true
        required_scopes: [crm:read]
        outputs:
          allowed: [contact, artifact_path]
        workflow_hints:
          business_goal: Look up CRM contacts for support workflow users.
          generated_by: coding_profile
          input_fields: [email]
          qa_required: true
          required_input_fields: [email]
""",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """version: appv24.permissions/v1
permissions:
  - connector: crm
    actions: [lookup_contact]
    scopes: [crm:read]
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text(
        "input:\n  email: customer@example.com\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("No external mutation occurs.\n", encoding="utf-8")
    return package


def _write_generated_crm_send_package(root: Path) -> Path:
    package = root / "generated-crm-send"
    (package / "dry_run_fixtures").mkdir(parents=True)
    (package / "workflow.yaml").write_text(
        """version: appv24.workflow/v1
name: generated-crm-send
description: Search AI news and send a CRM customer update through a generated connector.
design:
  intent: Send a customer update from current AI news.
  trigger: manual
  inputs: []
  outputs: [customer update]
  assumptions: [The CRM send connector has passed extension QA.]
  side_effects: [CRM customer message]
  approval_gates: [approve_crm_send]
  failure_modes: [CRM connector unavailable]
  rollback:
    - Send a corrective CRM message manually if the wrong update was sent.
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
    next: send_customer_update
  - id: send_customer_update
    uses: crm.send_customer_update
    side_effect_class: send
    approval: approve_crm_send
    with:
      customer_id: customer-1
      text: "{{ steps.search_ai_news.outputs.query }}"
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
  - id: crm
    adapter: mcp
    custom: true
    source: mcp://crm@v1
    actions:
      - id: send_customer_update
        custom: true
        source: mcp://crm.send_customer_update@v1
        tool: crm.send_customer_update
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        live_modes: [live-production]
        approval_required: true
        dry_run_supported: true
        required_scopes: [crm:send]
        idempotency:
          key_fields: [customer_id, text]
        outputs:
          allowed: [message_id, artifact_path]
        workflow_hints:
          business_goal: Send customer updates through the CRM connector.
          catalog_group: Draft or send
          catalog_label: Send CRM customer update
          generated_by: coding_profile
          input_fields: [customer_id, text]
          qa_required: true
          required_input_fields: [customer_id, text]
        qa:
          version: appv24.extension_qa/v1
          kind: connector
          extension_id: crm.send_customer_update
          source: mcp://crm.send_customer_update@v1
          status: passed
          qa:
            schema_validation: passed
            tests:
              - command: PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_crm_send_connector.py
                status: passed
            side_effect_policy: passed
            runtime_boundary: passed
            adapter_contract: passed
          contract:
            required_scopes: [crm:send]
            allowed_outputs: [message_id, artifact_path]
          fingerprints:
            spec: sha256:1111111111111111111111111111111111111111111111111111111111111111
            implementation: sha256:2222222222222222222222222222222222222222222222222222222222222222
            tests: sha256:3333333333333333333333333333333333333333333333333333333333333333
            fixtures: sha256:4444444444444444444444444444444444444444444444444444444444444444
""",
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        """version: appv24.permissions/v1
permissions:
  - connector: brave
    actions: [search]
    scopes: [brave:search]
  - connector: crm
    actions: [send_customer_update]
    scopes: [crm:send]
approvals:
  - id: approve_crm_send
    mode: explicit
    required_for: [send_customer_update]
""",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (package / "dry_run_fixtures" / "rejection_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (package / "rollback.md").write_text("Manual CRM corrective follow-up if a wrong update is sent.\n", encoding="utf-8")
    return package


def _passed_middleware_qa(extension_id: str) -> dict[str, object]:
    return {
        "version": "appv24.extension_qa/v1",
        "kind": "middleware",
        "extension_id": extension_id,
        "source": "local://middlewares/team/redactor@sha256:abc123",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": "pytest appV2.4/tests/test_team_redactor.py", "status": "passed"}],
            "side_effect_policy": "passed",
            "runtime_boundary": "passed",
            "adapter_contract": "passed",
        },
        "fingerprints": {
            "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
            "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
        },
    }
