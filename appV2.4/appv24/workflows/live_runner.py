"""Live-read workflow runner for validated appv24 workflow packages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.approval_gate import ApprovalIndexKey, build_payload_bound_approval_index
from appv24.workflows.broker import BrokerRequest
from appv24.workflows.bundle_guard import inspect_default_workflow_bundle
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_contracts import (
    ConnectorContract,
    ConnectorLiveMode,
    builtin_connector_contract_catalog,
    is_external_connector_action_payload,
)
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.credential_refs import CredentialRefResolver, preflight_credential_refs
from appv24.workflows.execution import WorkflowRunEvent, WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.workflow_extensions import extension_connector_contract_catalog
from appv24.workflows.idempotency import PersistentIdempotencyStore, build_idempotency_key, build_idempotency_payload_hash
from appv24.workflows.ir import ActionCapability, StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.ledger import StepTrace
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_gate import evaluate_live_gate
from appv24.workflows.loader import load_package_documents
from appv24.workflows.middleware_runtime import (
    MiddlewareExecutorFactory,
    MiddlewareRunContext,
    MiddlewareRuntime,
    build_middleware_runtime,
    middleware_runtime_setup_issues,
)
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy
from appv24.workflows.prior_outputs import fixture_with_prior_run_outputs
from appv24.workflows.run_preview import WorkflowBrokeredActionPreview
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import DEFAULT_FIXTURE_NAME, MAX_TRANSITIONS, build_workflow_run_metadata
from appv24.workflows.validator import validate_workflow_documents


def run_live_read_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> WorkflowRunReport:
    """Run a workflow package through allowlisted live read-only transports."""

    if request.mode is not WorkflowRunMode.LIVE_READ:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(documents.root)
    if bundle_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=bundle_issues,
        )
    middleware_setup_issues = middleware_runtime_setup_issues(documents, request.operator_env, middleware_factories)
    if middleware_setup_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=middleware_setup_issues,
        )
    contracts = _connector_contracts_for_documents(documents)
    broker = broker.with_external_actions(_external_action_refs(contracts))

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (Path(request.package_path) / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)
    run_id = store.create_run(
        workflow_name,
        package_fingerprint,
        metadata=build_workflow_run_metadata(request, package_path=documents.root, fixture_name=fixture_name),
    )
    events: list[WorkflowRunEvent] = []
    event_counter = _EventCounter()

    def append_event(event_type: str, status: str, **kwargs: Any) -> None:
        event = WorkflowRunEvent(
            event_id=event_counter.next(),
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=kwargs.pop("step_id", None),
            connector_action=kwargs.pop("connector_action", None),
            status=status,
            details=kwargs,
        )
        store.append_event(event)
        events.append(event)

    append_event(
        "run_created",
        "started",
        fixture_name=fixture_name,
        mode=request.mode.value,
        run_dir=str(store.run_dir(run_id)),
    )
    middleware_runtime = build_middleware_runtime(documents, request.operator_env, middleware_factories)
    middleware_context = MiddlewareRunContext(
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        mode=request.mode.value,
    )
    if middleware_runtime.middleware_ids:
        append_event(
            "middleware_runtime_configured",
            "configured",
            middleware_ids=list(middleware_runtime.middleware_ids),
        )
    middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_started",
        append_event,
        status="started",
    )
    if middleware_issues:
        append_event("run_completed", "blocked", terminal_step=None, issues=list(middleware_issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=tuple(middleware_issues),
        )
    _append_package_gate_passed_events(append_event)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        append_event("credential_preflight_blocked", "blocked", issues=list(credential_report.issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=credential_report.issues,
        )
    append_event("credential_preflight_passed", "passed")

    live_gate = evaluate_live_gate(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index={},
        available_transports=broker.configured_actions,
        env=request.operator_env,
        contracts=contracts,
    )
    if not live_gate.ok:
        gate_issues = _live_gate_issue_messages(live_gate)
        append_event(
            "live_gate_blocked",
            "blocked",
            checked_actions=list(live_gate.checked_actions),
            issues=list(gate_issues),
        )
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=gate_issues,
        )
    append_event("live_gate_passed", "passed", checked_actions=list(live_gate.checked_actions))

    broker_mode_issue = _broker_mode_issue(broker, "live-read")
    if broker_mode_issue is not None:
        append_event("broker_preflight_blocked", "blocked", issues=[broker_mode_issue], broker_mode=broker.mode)
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=(broker_mode_issue,),
        )
    append_event("broker_preflight_passed", "passed", broker_mode=broker.mode)

    result = _execute_live_read(
        compiled.ir,
        fixture,
        broker=broker,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        append_event=append_event,
        middleware_runtime=middleware_runtime,
        middleware_context=middleware_context,
        contracts=contracts,
    )
    completion_middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_completed",
        append_event,
        status=result.status,
        terminal_step=result.terminal_step,
        issue_count=len(result.issues),
    )
    if completion_middleware_issues:
        result.issues.extend(completion_middleware_issues)
        result.status = "blocked"
    append_event("run_completed", result.status, terminal_step=result.terminal_step, issues=list(result.issues))
    return _persist_report(
        request,
        store=store,
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        status=result.status,
        terminal_step=result.terminal_step,
        trace=tuple(result.trace),
        brokered_actions=tuple(result.brokered_actions),
        events=events,
        issues=tuple(result.issues),
    )


def run_live_draft_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> WorkflowRunReport:
    """Run approval-gated draft-eligible workflow actions through live transports."""

    if request.mode is not WorkflowRunMode.LIVE_DRAFT:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(documents.root)
    if bundle_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=bundle_issues,
        )
    middleware_setup_issues = middleware_runtime_setup_issues(documents, request.operator_env, middleware_factories)
    if middleware_setup_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=middleware_setup_issues,
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (Path(request.package_path) / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)
    run_id = store.create_run(
        workflow_name,
        package_fingerprint,
        metadata=build_workflow_run_metadata(request, package_path=documents.root, fixture_name=fixture_name),
    )
    events: list[WorkflowRunEvent] = []
    event_counter = _EventCounter()

    def append_event(event_type: str, status: str, **kwargs: Any) -> None:
        event = WorkflowRunEvent(
            event_id=event_counter.next(),
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=kwargs.pop("step_id", None),
            connector_action=kwargs.pop("connector_action", None),
            status=status,
            details=kwargs,
        )
        store.append_event(event)
        events.append(event)

    append_event(
        "run_created",
        "started",
        fixture_name=fixture_name,
        mode=request.mode.value,
        run_dir=str(store.run_dir(run_id)),
    )
    middleware_runtime = build_middleware_runtime(documents, request.operator_env, middleware_factories)
    middleware_context = MiddlewareRunContext(
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        mode=request.mode.value,
    )
    if middleware_runtime.middleware_ids:
        append_event(
            "middleware_runtime_configured",
            "configured",
            middleware_ids=list(middleware_runtime.middleware_ids),
        )
    middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_started",
        append_event,
        status="started",
    )
    if middleware_issues:
        append_event("run_completed", "blocked", terminal_step=None, issues=list(middleware_issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=tuple(middleware_issues),
        )
    _append_package_gate_passed_events(append_event)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        append_event("credential_preflight_blocked", "blocked", issues=list(credential_report.issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=credential_report.issues,
        )
    append_event("credential_preflight_passed", "passed")

    contracts = _connector_contracts_for_documents(documents)
    broker = broker.with_external_actions(_external_action_refs(contracts))
    approval_index, approval_issues = _live_draft_approval_index(
        store,
        request.approval_run_id,
        package_fingerprint=package_fingerprint,
        ir=compiled.ir,
        fixture=fixture,
    )
    if approval_issues:
        append_event("approval_preflight_blocked", "blocked", issues=list(approval_issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=approval_issues,
        )
    start_step = _approved_live_start_step(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        approval_index=approval_index,
        connector_allowlist=set(request.connector_allowlist),
        mode=request.mode,
        contracts=contracts,
    )
    append_event("approval_preflight_passed", "passed", approval_run_id=request.approval_run_id, start_step=start_step)

    live_gate = evaluate_live_gate(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=broker.configured_actions,
        env=request.operator_env,
        start_step=start_step,
        contracts=contracts,
    )
    if not live_gate.ok:
        gate_issues = _live_gate_issue_messages(live_gate)
        append_event(
            "live_gate_blocked",
            "blocked",
            checked_actions=list(live_gate.checked_actions),
            issues=list(gate_issues),
        )
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=gate_issues,
        )
    append_event("live_gate_passed", "passed", checked_actions=list(live_gate.checked_actions))

    broker_mode_issue = _broker_mode_issue(broker, "live-draft")
    if broker_mode_issue is not None:
        append_event("broker_preflight_blocked", "blocked", issues=[broker_mode_issue], broker_mode=broker.mode)
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=(broker_mode_issue,),
        )
    append_event("broker_preflight_passed", "passed", broker_mode=broker.mode)

    result = _execute_live_draft(
        compiled.ir,
        fixture,
        broker=broker,
        idempotency_store=PersistentIdempotencyStore(store.root / "idempotency.jsonl"),
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        append_event=append_event,
        start_step=start_step,
        middleware_runtime=middleware_runtime,
        middleware_context=middleware_context,
        contracts=contracts,
    )
    completion_middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_completed",
        append_event,
        status=result.status,
        terminal_step=result.terminal_step,
        issue_count=len(result.issues),
    )
    if completion_middleware_issues:
        result.issues.extend(completion_middleware_issues)
        result.status = "blocked"
    append_event("run_completed", result.status, terminal_step=result.terminal_step, issues=list(result.issues))
    return _persist_report(
        request,
        store=store,
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        status=result.status,
        terminal_step=result.terminal_step,
        trace=tuple(result.trace),
        brokered_actions=tuple(result.brokered_actions),
        events=events,
        issues=tuple(result.issues),
    )


def run_live_production_workflow(
    request: WorkflowRunRequest,
    *,
    broker: LiveWorkflowBroker,
    credential_resolver: CredentialRefResolver,
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> WorkflowRunReport:
    """Run approval-gated production-enabled workflow actions through live transports."""

    if request.mode is not WorkflowRunMode.LIVE_PRODUCTION:
        return _blocked_report(
            request,
            workflow_name="",
            package_fingerprint="",
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(request.package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(documents.root)
    if bundle_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=bundle_issues,
        )
    middleware_setup_issues = middleware_runtime_setup_issues(documents, request.operator_env, middleware_factories)
    if middleware_setup_issues:
        return _blocked_report(
            request,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            issues=middleware_setup_issues,
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (Path(request.package_path) / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)
    run_id = store.create_run(
        workflow_name,
        package_fingerprint,
        metadata=build_workflow_run_metadata(request, package_path=documents.root, fixture_name=fixture_name),
    )
    events: list[WorkflowRunEvent] = []
    event_counter = _EventCounter()

    def append_event(event_type: str, status: str, **kwargs: Any) -> None:
        event = WorkflowRunEvent(
            event_id=event_counter.next(),
            run_id=run_id,
            event_type=event_type,
            package_fingerprint=package_fingerprint,
            step_id=kwargs.pop("step_id", None),
            connector_action=kwargs.pop("connector_action", None),
            status=status,
            details=kwargs,
        )
        store.append_event(event)
        events.append(event)

    append_event(
        "run_created",
        "started",
        fixture_name=fixture_name,
        mode=request.mode.value,
        run_dir=str(store.run_dir(run_id)),
    )
    middleware_runtime = build_middleware_runtime(documents, request.operator_env, middleware_factories)
    middleware_context = MiddlewareRunContext(
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        mode=request.mode.value,
    )
    if middleware_runtime.middleware_ids:
        append_event(
            "middleware_runtime_configured",
            "configured",
            middleware_ids=list(middleware_runtime.middleware_ids),
        )
    middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_started",
        append_event,
        status="started",
    )
    if middleware_issues:
        append_event("run_completed", "blocked", terminal_step=None, issues=list(middleware_issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=tuple(middleware_issues),
        )
    _append_package_gate_passed_events(append_event)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        append_event("credential_preflight_blocked", "blocked", issues=list(credential_report.issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=credential_report.issues,
        )
    append_event("credential_preflight_passed", "passed")

    contracts = _connector_contracts_for_documents(documents)
    broker = broker.with_external_actions(_external_action_refs(contracts))
    approval_index, approval_issues = _live_draft_approval_index(
        store,
        request.approval_run_id,
        package_fingerprint=package_fingerprint,
        ir=compiled.ir,
        fixture=fixture,
        mode="live-production",
    )
    if approval_issues:
        append_event("approval_preflight_blocked", "blocked", issues=list(approval_issues))
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=approval_issues,
        )
    start_step = _approved_live_start_step(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        approval_index=approval_index,
        connector_allowlist=set(request.connector_allowlist),
        mode=request.mode,
        contracts=contracts,
    )
    append_event("approval_preflight_passed", "passed", approval_run_id=request.approval_run_id, start_step=start_step)

    live_gate = evaluate_live_gate(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=broker.configured_actions,
        env=request.operator_env,
        start_step=start_step,
        contracts=contracts,
    )
    if not live_gate.ok:
        gate_issues = _live_gate_issue_messages(live_gate)
        append_event(
            "live_gate_blocked",
            "blocked",
            checked_actions=list(live_gate.checked_actions),
            issues=list(gate_issues),
        )
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=gate_issues,
        )
    append_event("live_gate_passed", "passed", checked_actions=list(live_gate.checked_actions))

    broker_mode_issue = _broker_mode_issue(broker, "live-production")
    if broker_mode_issue is not None:
        append_event("broker_preflight_blocked", "blocked", issues=[broker_mode_issue], broker_mode=broker.mode)
        return _persist_report(
            request,
            store=store,
            run_id=run_id,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            status="blocked",
            terminal_step=None,
            trace=(),
            brokered_actions=(),
            events=events,
            issues=(broker_mode_issue,),
        )
    append_event("broker_preflight_passed", "passed", broker_mode=broker.mode)

    result = _execute_live_production(
        compiled.ir,
        fixture,
        broker=broker,
        idempotency_store=PersistentIdempotencyStore(store.root / "idempotency.jsonl"),
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        append_event=append_event,
        start_step=start_step,
        middleware_runtime=middleware_runtime,
        middleware_context=middleware_context,
        contracts=contracts,
    )
    completion_middleware_issues = _emit_middleware(
        middleware_runtime,
        middleware_context,
        "run_completed",
        append_event,
        status=result.status,
        terminal_step=result.terminal_step,
        issue_count=len(result.issues),
    )
    if completion_middleware_issues:
        result.issues.extend(completion_middleware_issues)
        result.status = "blocked"
    append_event("run_completed", result.status, terminal_step=result.terminal_step, issues=list(result.issues))
    return _persist_report(
        request,
        store=store,
        run_id=run_id,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        status=result.status,
        terminal_step=result.terminal_step,
        trace=tuple(result.trace),
        brokered_actions=tuple(result.brokered_actions),
        events=events,
        issues=tuple(result.issues),
    )


def _connector_contracts_for_documents(documents: PackageDocuments) -> dict[str, ConnectorContract]:
    contracts = builtin_connector_contract_catalog()
    contracts.update(extension_connector_contract_catalog(documents, contract_catalog=contracts))
    return contracts


def _external_action_refs(contracts: Mapping[str, ConnectorContract]) -> tuple[str, ...]:
    builtins = builtin_connector_contract_catalog()
    return tuple(sorted(action_ref for action_ref in contracts if action_ref not in builtins))


class _EventCounter:
    def __init__(self) -> None:
        self._value = 0

    def next(self) -> str:
        self._value += 1
        return f"event-{self._value}"


class _ExecutionResult:
    def __init__(
        self,
        *,
        status: str,
        terminal_step: str | None,
        trace: list[StepTrace],
        brokered_actions: list[WorkflowBrokeredActionPreview],
        issues: list[str],
    ) -> None:
        self.status = status
        self.terminal_step = terminal_step
        self.trace = trace
        self.brokered_actions = brokered_actions
        self.issues = issues


def _execute_live_read(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    broker: LiveWorkflowBroker,
    package_fingerprint: str,
    connector_allowlist: set[str],
    append_event: Callable[..., None],
    middleware_runtime: MiddlewareRuntime,
    middleware_context: MiddlewareRunContext,
    contracts: Mapping[str, ConnectorContract],
) -> _ExecutionResult:
    trace: list[StepTrace] = []
    brokered_actions: list[WorkflowBrokeredActionPreview] = []
    issues: list[str] = []
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            return _result("blocked", None, trace, brokered_actions, issues)

        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _result("blocked", None, trace, brokered_actions, issues)

        append_event("step_started", "started", step_id=step.step_id, connector_action=_connector_action(step))
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_started",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="started",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            trace.append(_trace(step, "blocked", issues=middleware_issues))
            return _result("blocked", None, trace, brokered_actions, issues)
        if step.kind is StepKind.TERMINAL:
            trace.append(_trace(step, "terminal"))
            append_event("step_completed", "terminal", step_id=step.step_id)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="terminal",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            return _result("passed", step.step_id, trace, brokered_actions, issues)

        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issue = f"no branch matched for {step.step_id}"
                issues.append(issue)
                trace.append(_trace(step, "blocked", selected_branch=selected_branch, issues=("no branch matched",)))
                append_event("step_blocked", "blocked", step_id=step.step_id, selected_branch=selected_branch, issue=issue)
                return _result("blocked", None, trace, brokered_actions, issues)
            trace.append(_trace(step, "completed", selected_branch=selected_branch))
            append_event("step_completed", "completed", step_id=step.step_id, selected_branch=selected_branch)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="completed",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            current = next_step
            continue

        action = _action_for_step(ir, step)
        gate_issue = _live_read_gate_issue(step, action, connector_allowlist, contracts)
        if gate_issue is not None:
            pause_issue = _live_read_prefix_pause_issue(step, gate_issue, brokered_actions)
            if pause_issue is not None:
                issues.append(pause_issue)
                trace.append(_trace(step, "paused", issues=(pause_issue,)))
                append_event(
                    "step_paused",
                    "paused",
                    step_id=step.step_id,
                    connector_action=_connector_action(step),
                    issue=pause_issue,
                    gate_issue=gate_issue,
                )
                return _result("paused", None, trace, brokered_actions, issues)
            issues.append(gate_issue)
            trace.append(_trace(step, "blocked", issues=(gate_issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=gate_issue,
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        outputs, broker_status, broker_provider_id, broker_issues = _invoke_broker(ir, step, fixture, broker, append_event)
        if action is not None and step.uses is not None:
            brokered_actions.append(
                WorkflowBrokeredActionPreview(
                    step_id=step.step_id,
                    connector_action=step.uses.dotted,
                    approval_id=step.approval,
                    side_effect_class=step.side_effect_class,
                    allowed_outputs=action.allowed_outputs,
                    idempotency_key_fields=action.idempotency_key_fields,
                    idempotency_key=_idempotency_key(package_fingerprint, step, action, fixture),
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                    simulated_outputs=dict(outputs),
                )
            )
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(
                _trace(
                    step,
                    "blocked",
                    outputs=outputs,
                    issues=broker_issues,
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                )
            )
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issues=list(broker_issues),
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        trace.append(_trace(step, "completed", outputs=outputs, broker_status=broker_status, broker_provider_id=broker_provider_id))
        append_event(
            "step_completed",
            "completed",
            step_id=step.step_id,
            connector_action=_connector_action(step),
            outputs=dict(outputs),
        )
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_completed",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="completed",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            return _result("blocked", None, trace, brokered_actions, issues)
        current = step.next_step

    issues.append("workflow stopped without terminal step")
    return _result("blocked", None, trace, brokered_actions, issues)


def _execute_live_draft(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    broker: LiveWorkflowBroker,
    idempotency_store: PersistentIdempotencyStore,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    append_event: Callable[..., None],
    middleware_runtime: MiddlewareRuntime,
    middleware_context: MiddlewareRunContext,
    start_step: str | None = None,
    contracts: Mapping[str, ConnectorContract] | None = None,
) -> _ExecutionResult:
    contract_catalog = contracts or builtin_connector_contract_catalog()
    trace: list[StepTrace] = []
    brokered_actions: list[WorkflowBrokeredActionPreview] = []
    issues: list[str] = []
    current = start_step or ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            return _result("blocked", None, trace, brokered_actions, issues)

        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _result("blocked", None, trace, brokered_actions, issues)

        append_event("step_started", "started", step_id=step.step_id, connector_action=_connector_action(step))
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_started",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="started",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            trace.append(_trace(step, "blocked", issues=middleware_issues))
            return _result("blocked", None, trace, brokered_actions, issues)
        if step.kind is StepKind.TERMINAL:
            trace.append(_trace(step, "terminal"))
            append_event("step_completed", "terminal", step_id=step.step_id)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="terminal",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            return _result("passed", step.step_id, trace, brokered_actions, issues)

        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issue = f"no branch matched for {step.step_id}"
                issues.append(issue)
                trace.append(_trace(step, "blocked", selected_branch=selected_branch, issues=("no branch matched",)))
                append_event("step_blocked", "blocked", step_id=step.step_id, selected_branch=selected_branch, issue=issue)
                return _result("blocked", None, trace, brokered_actions, issues)
            trace.append(_trace(step, "completed", selected_branch=selected_branch))
            append_event("step_completed", "completed", step_id=step.step_id, selected_branch=selected_branch)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="completed",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            current = next_step
            continue

        action = _action_for_step(ir, step)
        gate_issue = _live_draft_gate_issue(
            step,
            action,
            connector_allowlist,
            package_fingerprint=package_fingerprint,
            approval_index=approval_index,
            fixture=fixture,
            contracts=contract_catalog,
        )
        if gate_issue is not None:
            issues.append(gate_issue)
            trace.append(_trace(step, "blocked", issues=(gate_issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=gate_issue,
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        _append_idempotency_reservation(
            step,
            action,
            package_fingerprint=package_fingerprint,
            fixture=fixture,
            append_event=append_event,
        )
        reused_outputs, reused_issue = _reuse_committed_idempotency_result(
            step,
            action,
            fixture,
            idempotency_store=idempotency_store,
            package_fingerprint=package_fingerprint,
            append_event=append_event,
        )
        if reused_issue is not None:
            issues.append(reused_issue)
            trace.append(_trace(step, "blocked", issues=(reused_issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=reused_issue,
            )
            return _result("blocked", None, trace, brokered_actions, issues)
        if reused_outputs is not None:
            _append_brokered_action_preview(
                brokered_actions,
                step,
                action,
                package_fingerprint=package_fingerprint,
                fixture=fixture,
                outputs=reused_outputs,
                broker_status="idempotency-reused",
                broker_provider_id="idempotency",
            )
            trace.append(_trace(step, "completed", outputs=reused_outputs, broker_status="idempotency-reused", broker_provider_id="idempotency"))
            append_event(
                "step_completed",
                "completed",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                outputs=dict(reused_outputs),
                broker_status="idempotency-reused",
            )
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                connector_action=_connector_action(step),
                status="completed",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            current = step.next_step
            continue
        outputs, broker_status, broker_provider_id, broker_issues = _invoke_broker(ir, step, fixture, broker, append_event)
        _append_brokered_action_preview(
            brokered_actions,
            step,
            action,
            package_fingerprint=package_fingerprint,
            fixture=fixture,
            outputs=outputs,
            broker_status=broker_status,
            broker_provider_id=broker_provider_id,
        )
        if not broker_issues:
            commit_issue = _commit_idempotency_result(
                step,
                action,
                fixture,
                outputs,
                idempotency_store=idempotency_store,
                package_fingerprint=package_fingerprint,
                append_event=append_event,
            )
            if commit_issue is not None:
                broker_issues = (*broker_issues, commit_issue)
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(
                _trace(
                    step,
                    "blocked",
                    outputs=outputs,
                    issues=broker_issues,
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                )
            )
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issues=list(broker_issues),
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        trace.append(_trace(step, "completed", outputs=outputs, broker_status=broker_status, broker_provider_id=broker_provider_id))
        append_event(
            "step_completed",
            "completed",
            step_id=step.step_id,
            connector_action=_connector_action(step),
            outputs=dict(outputs),
        )
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_completed",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="completed",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            return _result("blocked", None, trace, brokered_actions, issues)
        current = step.next_step

    issues.append("workflow stopped without terminal step")
    return _result("blocked", None, trace, brokered_actions, issues)


def _execute_live_production(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    broker: LiveWorkflowBroker,
    idempotency_store: PersistentIdempotencyStore,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    append_event: Callable[..., None],
    middleware_runtime: MiddlewareRuntime,
    middleware_context: MiddlewareRunContext,
    start_step: str | None = None,
    contracts: Mapping[str, ConnectorContract] | None = None,
) -> _ExecutionResult:
    contract_catalog = contracts or builtin_connector_contract_catalog()
    trace: list[StepTrace] = []
    brokered_actions: list[WorkflowBrokeredActionPreview] = []
    issues: list[str] = []
    current = start_step or ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            return _result("blocked", None, trace, brokered_actions, issues)

        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _result("blocked", None, trace, brokered_actions, issues)

        append_event("step_started", "started", step_id=step.step_id, connector_action=_connector_action(step))
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_started",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="started",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            trace.append(_trace(step, "blocked", issues=middleware_issues))
            return _result("blocked", None, trace, brokered_actions, issues)
        if step.kind is StepKind.TERMINAL:
            trace.append(_trace(step, "terminal"))
            append_event("step_completed", "terminal", step_id=step.step_id)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="terminal",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            return _result("passed", step.step_id, trace, brokered_actions, issues)

        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issue = f"no branch matched for {step.step_id}"
                issues.append(issue)
                trace.append(_trace(step, "blocked", selected_branch=selected_branch, issues=("no branch matched",)))
                append_event("step_blocked", "blocked", step_id=step.step_id, selected_branch=selected_branch, issue=issue)
                return _result("blocked", None, trace, brokered_actions, issues)
            trace.append(_trace(step, "completed", selected_branch=selected_branch))
            append_event("step_completed", "completed", step_id=step.step_id, selected_branch=selected_branch)
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                status="completed",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            current = next_step
            continue

        action = _action_for_step(ir, step)
        gate_issue = _live_production_gate_issue(
            step,
            action,
            connector_allowlist,
            package_fingerprint=package_fingerprint,
            approval_index=approval_index,
            fixture=fixture,
            contracts=contract_catalog,
        )
        if gate_issue is not None:
            issues.append(gate_issue)
            trace.append(_trace(step, "blocked", issues=(gate_issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=gate_issue,
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        _append_idempotency_reservation(
            step,
            action,
            package_fingerprint=package_fingerprint,
            fixture=fixture,
            append_event=append_event,
        )
        reused_outputs, reused_issue = _reuse_committed_idempotency_result(
            step,
            action,
            fixture,
            idempotency_store=idempotency_store,
            package_fingerprint=package_fingerprint,
            append_event=append_event,
        )
        if reused_issue is not None:
            issues.append(reused_issue)
            trace.append(_trace(step, "blocked", issues=(reused_issue,)))
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issue=reused_issue,
            )
            return _result("blocked", None, trace, brokered_actions, issues)
        if reused_outputs is not None:
            _append_brokered_action_preview(
                brokered_actions,
                step,
                action,
                package_fingerprint=package_fingerprint,
                fixture=fixture,
                outputs=reused_outputs,
                broker_status="idempotency-reused",
                broker_provider_id="idempotency",
            )
            trace.append(_trace(step, "completed", outputs=reused_outputs, broker_status="idempotency-reused", broker_provider_id="idempotency"))
            append_event(
                "step_completed",
                "completed",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                outputs=dict(reused_outputs),
                broker_status="idempotency-reused",
            )
            middleware_issues = _emit_middleware(
                middleware_runtime,
                middleware_context,
                "step_completed",
                append_event,
                step_id=step.step_id,
                connector_action=_connector_action(step),
                status="completed",
            )
            if middleware_issues:
                issues.extend(middleware_issues)
                return _result("blocked", None, trace, brokered_actions, issues)
            current = step.next_step
            continue
        outputs, broker_status, broker_provider_id, broker_issues = _invoke_broker(ir, step, fixture, broker, append_event)
        _append_brokered_action_preview(
            brokered_actions,
            step,
            action,
            package_fingerprint=package_fingerprint,
            fixture=fixture,
            outputs=outputs,
            broker_status=broker_status,
            broker_provider_id=broker_provider_id,
        )
        if not broker_issues:
            commit_issue = _commit_idempotency_result(
                step,
                action,
                fixture,
                outputs,
                idempotency_store=idempotency_store,
                package_fingerprint=package_fingerprint,
                append_event=append_event,
            )
            if commit_issue is not None:
                broker_issues = (*broker_issues, commit_issue)
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(
                _trace(
                    step,
                    "blocked",
                    outputs=outputs,
                    issues=broker_issues,
                    broker_status=broker_status,
                    broker_provider_id=broker_provider_id,
                )
            )
            append_event(
                "step_blocked",
                "blocked",
                step_id=step.step_id,
                connector_action=_connector_action(step),
                issues=list(broker_issues),
            )
            return _result("blocked", None, trace, brokered_actions, issues)

        trace.append(_trace(step, "completed", outputs=outputs, broker_status=broker_status, broker_provider_id=broker_provider_id))
        append_event(
            "step_completed",
            "completed",
            step_id=step.step_id,
            connector_action=_connector_action(step),
            outputs=dict(outputs),
        )
        middleware_issues = _emit_middleware(
            middleware_runtime,
            middleware_context,
            "step_completed",
            append_event,
            step_id=step.step_id,
            connector_action=_connector_action(step),
            status="completed",
        )
        if middleware_issues:
            issues.extend(middleware_issues)
            return _result("blocked", None, trace, brokered_actions, issues)
        current = step.next_step

    issues.append("workflow stopped without terminal step")
    return _result("blocked", None, trace, brokered_actions, issues)


def _emit_middleware(
    middleware_runtime: MiddlewareRuntime,
    middleware_context: MiddlewareRunContext,
    hook: str,
    append_event: Callable[..., None],
    **details: Any,
) -> list[str]:
    issues = list(middleware_runtime.emit(hook, middleware_context, **details))
    if issues:
        append_event(
            "middleware_hook_failed",
            "blocked",
            middleware_hook=hook,
            issues=issues,
            middleware_ids=list(middleware_runtime.middleware_ids),
        )
    return issues


def _result(
    status: str,
    terminal_step: str | None,
    trace: list[StepTrace],
    brokered_actions: list[WorkflowBrokeredActionPreview],
    issues: list[str],
) -> _ExecutionResult:
    return _ExecutionResult(
        status=status,
        terminal_step=terminal_step,
        trace=trace,
        brokered_actions=brokered_actions,
        issues=issues,
    )


def _live_gate_issue_messages(live_gate: Any) -> tuple[str, ...]:
    return tuple(issue.message for issue in live_gate.issues)


def _approved_live_start_step(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    connector_allowlist: set[str],
    mode: WorkflowRunMode,
    contracts: Mapping[str, ConnectorContract] | None = None,
) -> str | None:
    contract_catalog = contracts or builtin_connector_contract_catalog()
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            return None
        step = ir.steps.get(current)
        if step is None or step.kind is StepKind.TERMINAL:
            return None
        if step.kind is StepKind.DECISION:
            _, current = _select_branch(step, fixture)
            continue
        if step.uses is None:
            current = step.next_step
            continue
        action = _action_for_step(ir, step)
        action_ref = step.uses.dotted
        if (
            action is not None
            and action_ref in connector_allowlist
            and _approved_start_mode_matches(step, action, action_ref=action_ref, mode=mode, contracts=contract_catalog)
        ):
            idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
            if approval_index.get((step.step_id, action_ref, idempotency_key)) is True:
                return step.step_id
        current = step.next_step
    return None


def _approved_start_mode_matches(
    step: WorkflowStep,
    action: ActionCapability,
    *,
    action_ref: str,
    mode: WorkflowRunMode,
    contracts: Mapping[str, ConnectorContract],
) -> bool:
    if not _is_mutating_step(step, action):
        return False
    contract = contracts.get(action_ref)
    if contract is None:
        return False
    if mode is WorkflowRunMode.LIVE_DRAFT:
        return ConnectorLiveMode.LIVE_DRAFT in contract.live_modes
    if mode is WorkflowRunMode.LIVE_PRODUCTION:
        return ConnectorLiveMode.LIVE_PRODUCTION in contract.live_modes
    return False


def _append_package_gate_passed_events(append_event: Callable[..., None]) -> None:
    append_event("package_validation_passed", "passed")
    append_event("compile_passed", "passed")
    append_event("policy_passed", "passed")
    append_event("bundle_guard_passed", "passed")


def _append_idempotency_reservation(
    step: WorkflowStep,
    action: ActionCapability | None,
    *,
    package_fingerprint: str,
    fixture: Mapping[str, Any],
    append_event: Callable[..., None],
) -> None:
    if action is None or step.uses is None or not _is_mutating_step(step, action):
        return
    idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
    if idempotency_key is None:
        return
    append_event(
        "idempotency_reserved",
        "reserved",
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        idempotency_key=idempotency_key,
        idempotency_key_fields=list(action.idempotency_key_fields),
    )


def _reuse_committed_idempotency_result(
    step: WorkflowStep,
    action: ActionCapability | None,
    fixture: Mapping[str, Any],
    *,
    idempotency_store: PersistentIdempotencyStore,
    package_fingerprint: str,
    append_event: Callable[..., None],
) -> tuple[Mapping[str, Any] | None, str | None]:
    if action is None or step.uses is None or not _is_mutating_step(step, action):
        return None, None
    idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
    if idempotency_key is None:
        return None, None
    payload_hash = build_idempotency_payload_hash(input_ref=step.raw, fixture=fixture)
    try:
        reused = idempotency_store.reuse_committed(idempotency_key, payload_hash=payload_hash)
    except ValueError as error:
        return None, str(error)
    if reused is None:
        return None, None
    append_event(
        "idempotency_reused",
        "reused",
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    return reused, None


def _commit_idempotency_result(
    step: WorkflowStep,
    action: ActionCapability | None,
    fixture: Mapping[str, Any],
    outputs: Mapping[str, Any],
    *,
    idempotency_store: PersistentIdempotencyStore,
    package_fingerprint: str,
    append_event: Callable[..., None],
) -> str | None:
    if action is None or step.uses is None or not _is_mutating_step(step, action):
        return None
    idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
    if idempotency_key is None:
        return None
    payload_hash = build_idempotency_payload_hash(input_ref=step.raw, fixture=fixture)
    try:
        idempotency_store.record_or_reuse(idempotency_key, outputs, payload_hash=payload_hash)
    except ValueError as error:
        return str(error)
    append_event(
        "idempotency_committed",
        "committed",
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    return None


def _append_brokered_action_preview(
    brokered_actions: list[WorkflowBrokeredActionPreview],
    step: WorkflowStep,
    action: ActionCapability | None,
    *,
    package_fingerprint: str,
    fixture: Mapping[str, Any],
    outputs: Mapping[str, Any],
    broker_status: str | None,
    broker_provider_id: str | None,
) -> None:
    if action is None or step.uses is None:
        return
    brokered_actions.append(
        WorkflowBrokeredActionPreview(
            step_id=step.step_id,
            connector_action=step.uses.dotted,
            approval_id=step.approval,
            side_effect_class=step.side_effect_class,
            allowed_outputs=action.allowed_outputs,
            idempotency_key_fields=action.idempotency_key_fields,
            idempotency_key=_idempotency_key(package_fingerprint, step, action, fixture),
            broker_status=broker_status,
            broker_provider_id=broker_provider_id,
            simulated_outputs=dict(outputs),
        )
    )


def _default_bundle_issues(package_path: Path) -> tuple[str, ...]:
    return inspect_default_workflow_bundle(package_path).issues


def _invoke_broker(
    ir: WorkflowIR,
    step: WorkflowStep,
    fixture: Mapping[str, Any],
    broker: LiveWorkflowBroker,
    append_event: Callable[..., None],
) -> tuple[Mapping[str, Any], str | None, str | None, tuple[str, ...]]:
    if step.uses is None:
        return {}, None, None, ()
    action = _action_for_step(ir, step)
    allowed_outputs = action.allowed_outputs if action is not None else ()
    append_event(
        "broker_action_started",
        "started",
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        allowed_outputs=list(allowed_outputs),
    )
    result = broker.invoke(
        BrokerRequest(
            step_id=step.step_id,
            connector_action=step.uses.dotted,
            input_ref=step.raw,
            fixture=fixture,
            allowed_outputs=allowed_outputs,
            dry_run=False,
        )
    )
    status = "blocked" if result.issues or result.status == "blocked" else "completed"
    append_event(
        "broker_action_completed" if status == "completed" else "broker_action_blocked",
        status,
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        broker_provider_id=result.provider_id,
        broker_status=result.status,
        outputs=dict(result.outputs),
        issues=list(result.issues),
    )
    if result.status == "blocked" and not result.issues:
        return result.outputs, result.status, result.provider_id, ("broker blocked step",)
    return result.outputs, result.status, result.provider_id, result.issues


def _live_read_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
    contracts: Mapping[str, ConnectorContract],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if _is_mutating_step(step, action):
        return f"live-read cannot execute mutating step {step.step_id}"
    contract = contracts.get(step.uses.dotted)
    if contract is None:
        return _external_read_gate_issue("live-read", step, action)
    if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
        return f"live-read action is not live-read eligible: {step.uses.dotted}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-read action is not allowlisted: {step.uses.dotted}"
    return None


def _live_read_prefix_pause_issue(
    step: WorkflowStep,
    gate_issue: str,
    brokered_actions: list[WorkflowBrokeredActionPreview],
) -> str | None:
    if not brokered_actions:
        return None
    if gate_issue.startswith("live-read cannot execute mutating step ") or gate_issue.startswith(
        "live-read action is not live-read eligible: "
    ):
        return f"live-read prefix completed before non-live-read step {step.step_id}"
    return None


def _live_draft_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, Any],
    contracts: Mapping[str, ConnectorContract],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-draft action is not allowlisted: {step.uses.dotted}"
    contract = contracts.get(step.uses.dotted)
    if contract is None:
        return _external_read_gate_issue("live-draft", step, action)
    if _is_mutating_step(step, action):
        if ConnectorLiveMode.LIVE_DRAFT not in contract.live_modes:
            return f"live-draft action is not draft-eligible: {step.uses.dotted}"
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        if idempotency_key is None:
            return f"live-draft requires idempotency key fields for {step.step_id}"
        if step.approval is None:
            return f"live-draft requires approval for {step.step_id}"
        if approval_index.get((step.step_id, step.uses.dotted, idempotency_key)) is not True:
            return f"approval not granted for {step.step_id}"
        return None
    if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
        return f"live-draft read action is not live-read eligible: {step.uses.dotted}"
    return None


def _live_production_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, Any],
    contracts: Mapping[str, ConnectorContract],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-production action is not allowlisted: {step.uses.dotted}"
    contract = contracts.get(step.uses.dotted)
    if contract is None:
        return _external_read_gate_issue("live-production", step, action)
    if _is_mutating_step(step, action):
        if ConnectorLiveMode.LIVE_PRODUCTION not in contract.live_modes:
            return f"live-production action is not production-enabled: {step.uses.dotted}"
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        if idempotency_key is None:
            return f"live-production requires idempotency key fields for {step.step_id}"
        if step.approval is None:
            return f"live-production requires approval for {step.step_id}"
        if approval_index.get((step.step_id, step.uses.dotted, idempotency_key)) is not True:
            return f"approval not granted for {step.step_id}"
        return None
    if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
        return f"live-production read action is not live-read eligible: {step.uses.dotted}"
    return None


def _external_read_gate_issue(mode: str, step: WorkflowStep, action: ActionCapability) -> str | None:
    action_ref = step.uses.dotted if step.uses is not None else ""
    if not is_external_connector_action_payload(action_ref, action.raw):
        return f"{mode} action is not cataloged: {action_ref}"
    if _is_mutating_step(step, action):
        return f"{mode} external connector action is not read-only: {action_ref}"
    if action.execution_mode != "brokered":
        return f"{mode} external connector action must be brokered: {action_ref}"
    return None


def _is_mutating_step(step: WorkflowStep, action: ActionCapability) -> bool:
    return (
        step.approval is not None
        or step.side_effect_class in MUTATING_SIDE_EFFECTS
        or action.side_effect
        or action.approval_required is True
    )


def _select_branch(step: WorkflowStep, fixture: Mapping[str, Any]) -> tuple[str | None, str | None]:
    decisions = fixture.get("decision", {})
    if isinstance(decisions, dict):
        selected = decisions.get(step.step_id)
        if isinstance(selected, str) and selected in step.branches:
            return selected, step.branches[selected]
    if len(step.branches) == 1:
        selected, target = next(iter(step.branches.items()))
        return selected, target
    return None, None


def _action_for_step(ir: WorkflowIR, step: WorkflowStep) -> ActionCapability | None:
    if step.uses is None:
        return None
    connector = ir.connectors.get(step.uses.connector_id)
    if connector is None:
        return None
    return connector.actions.get(step.uses.action_id)


def _connector_action(step: WorkflowStep) -> str | None:
    return step.uses.dotted if step.uses is not None else None


def _broker_mode_issue(broker: LiveWorkflowBroker, expected_mode: str) -> str | None:
    if broker.mode != expected_mode:
        return f"{expected_mode} requires {expected_mode} broker mode"
    return None


def _idempotency_key(
    package_fingerprint: str,
    step: WorkflowStep,
    action: ActionCapability | None,
    fixture: Mapping[str, Any],
) -> str | None:
    if action is None or step.uses is None:
        return None
    return build_idempotency_key(
        package_fingerprint=package_fingerprint,
        step_id=step.step_id,
        connector_action=step.uses.dotted,
        key_fields=action.idempotency_key_fields,
        input_ref=step.raw,
        fixture=fixture,
    )


def _live_draft_approval_index(
    store: WorkflowRunStore,
    approval_run_id: str | None,
    *,
    package_fingerprint: str,
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    mode: str = "live-draft",
) -> tuple[dict[tuple[str, str, str | None], bool], tuple[str, ...]]:
    if not approval_run_id:
        return {}, (f"{mode} requires approval_run_id",)
    try:
        manifest = store.read_run_manifest(approval_run_id)
        if manifest.get("mode") != "local":
            return {}, (f"{mode} requires approval_run_id from a local run",)
        decisions = store.read_approval_decisions(approval_run_id)
    except ValueError:
        return {}, (f"unknown approval run: {approval_run_id}",)
    return build_payload_bound_approval_index(
        decisions,
        package_fingerprint=package_fingerprint,
        expected_payload_hashes=_expected_approval_payload_hashes(ir, fixture, package_fingerprint),
    )


def _expected_approval_payload_hashes(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    package_fingerprint: str,
) -> dict[ApprovalIndexKey, str]:
    expected: dict[ApprovalIndexKey, str] = {}
    for step in ir.steps.values():
        if step.uses is None:
            continue
        action = _action_for_step(ir, step)
        if action is None or not _is_mutating_step(step, action):
            continue
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        if idempotency_key is None:
            continue
        expected[(step.step_id, step.uses.dotted, idempotency_key)] = build_idempotency_payload_hash(
            input_ref=step.raw,
            fixture=fixture,
        )
    return expected


def _trace(
    step: WorkflowStep,
    status: str,
    *,
    selected_branch: str | None = None,
    outputs: Mapping[str, Any] | None = None,
    issues: tuple[str, ...] = (),
    broker_status: str | None = None,
    broker_provider_id: str | None = None,
) -> StepTrace:
    return StepTrace(
        step_id=step.step_id,
        kind=step.kind.value,
        status=status,
        selected_branch=selected_branch,
        connector_action=_connector_action(step),
        side_effect_class=step.side_effect_class,
        approval_id=step.approval,
        broker_status=broker_status,
        broker_provider_id=broker_provider_id,
        simulated_outputs=outputs or {},
        issues=issues,
    )


def _persist_report(
    request: WorkflowRunRequest,
    *,
    store: WorkflowRunStore,
    run_id: str,
    workflow_name: str,
    package_fingerprint: str,
    status: str,
    terminal_step: str | None,
    trace: tuple[StepTrace, ...],
    brokered_actions: tuple[WorkflowBrokeredActionPreview, ...],
    events: list[WorkflowRunEvent],
    issues: tuple[str, ...],
) -> WorkflowRunReport:
    report = WorkflowRunReport(
        run_id=run_id,
        workflow_name=workflow_name,
        mode=request.mode,
        status=status,
        package_fingerprint=package_fingerprint,
        terminal_step=terminal_step,
        step_trace=trace,
        brokered_actions=brokered_actions,
        approvals=(),
        events=tuple(events),
        issues=issues,
    )
    store.write_trace(run_id, report.step_trace)
    store.write_approvals(run_id, report.approvals)
    store.write_report(run_id, report.to_dict())
    if status == "paused":
        store.write_handoff(
            run_id,
            _handoff_payload(
                report,
                workflow_name=workflow_name,
                package_fingerprint=package_fingerprint,
            ),
        )
    return report


def _handoff_payload(
    report: WorkflowRunReport,
    *,
    workflow_name: str,
    package_fingerprint: str,
) -> dict[str, object]:
    paused_step = next((trace for trace in report.step_trace if trace.status == "paused"), None)
    return {
        "version": "appv24.workflow-handoff/v1",
        "run_id": report.run_id,
        "workflow_name": workflow_name,
        "package_fingerprint": package_fingerprint,
        "from_mode": report.mode.value,
        "status": "ready",
        "completed_steps": [trace.step_id for trace in report.step_trace if trace.status == "completed"],
        "next_step": paused_step.step_id if paused_step is not None else None,
        "next_phase": _next_phase(paused_step),
        "available_outputs": _available_outputs(report.step_trace),
    }


def _available_outputs(trace: tuple[StepTrace, ...]) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    for item in trace:
        if item.status != "completed" or not item.simulated_outputs:
            continue
        outputs.append(
            {
                "step_id": item.step_id,
                "connector_action": item.connector_action,
                "output_keys": sorted(str(key) for key in item.simulated_outputs),
                "trace_ref": f"trace.json#{item.step_id}",
            }
        )
    return outputs


def _next_phase(paused_step: StepTrace | None) -> str | None:
    if paused_step is None or paused_step.connector_action is None:
        return None
    metadata = builtin_connector_catalog().actions.get(paused_step.connector_action)
    if metadata is None:
        return "local"
    if metadata.live_production_enabled:
        return "live-production"
    if metadata.live_draft_eligible:
        return "live-draft"
    return "local"


def _blocked_report(
    request: WorkflowRunRequest,
    *,
    workflow_name: str,
    package_fingerprint: str,
    issues: tuple[str, ...],
) -> WorkflowRunReport:
    return WorkflowRunReport(
        run_id="",
        workflow_name=workflow_name,
        mode=request.mode,
        status="blocked",
        package_fingerprint=package_fingerprint,
        terminal_step=None,
        step_trace=(),
        brokered_actions=(),
        approvals=(),
        events=(),
        issues=issues,
    )
