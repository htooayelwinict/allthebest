"""No-side-effect preflight checks for live workflow modes."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.approval_gate import ApprovalIndexKey, build_payload_bound_approval_index
from appv24.workflows.bundle_guard import inspect_default_workflow_bundle
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_contracts import ConnectorContract, ConnectorLiveMode, builtin_connector_contract_catalog
from appv24.workflows.credential_refs import (
    CredentialRefResolver,
    credential_resolver_from_env,
    preflight_credential_refs,
)
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.idempotency import build_idempotency_key, build_idempotency_payload_hash
from appv24.workflows.ir import ActionCapability, StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.live_gate import evaluate_live_gate
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy
from appv24.workflows.prior_outputs import fixture_with_prior_run_outputs
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import DEFAULT_FIXTURE_NAME, MAX_TRANSITIONS
from appv24.workflows.validator import validate_workflow_documents
from appv24.workflows.workflow_extensions import extension_connector_contract_catalog


@dataclass(frozen=True)
class LiveDraftPreflightReport:
    workflow_name: str
    package_path: Path
    package_fingerprint: str
    fixture_name: str
    approval_run_id: str | None
    status: str
    checked_actions: tuple[str, ...]
    issues: tuple[str, ...]


def preflight_live_draft_workflow(
    request: WorkflowRunRequest,
    *,
    credential_resolver: CredentialRefResolver,
    available_transports: Collection[str] | None = None,
) -> LiveDraftPreflightReport:
    package_path = Path(request.package_path).expanduser().resolve()
    if request.mode is not WorkflowRunMode.LIVE_DRAFT:
        return _report(
            request,
            package_path=package_path,
            workflow_name="",
            package_fingerprint="",
            checked_actions=(),
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(package_path)
    if bundle_issues:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=bundle_issues,
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (package_path / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=credential_report.issues,
        )

    approval_index, approval_issues = _approval_index(
        store,
        request.approval_run_id,
        package_fingerprint=package_fingerprint,
        ir=compiled.ir,
        fixture=fixture,
    )
    if approval_issues:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=approval_issues,
        )

    contracts = _connector_contracts_for_documents(documents)
    start_step = _approved_live_start_step(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        approval_index=approval_index,
        connector_allowlist=set(request.connector_allowlist),
        mode=request.mode,
        contracts=contracts,
    )
    checked_actions, issues = _preflight_gate_steps(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=available_transports,
        env=request.operator_env,
        contracts=contracts,
        start_step=start_step,
    )
    return _report(
        request,
        package_path=package_path,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        checked_actions=checked_actions,
        issues=issues,
    )


def preflight_live_read_workflow(
    request: WorkflowRunRequest,
    *,
    credential_resolver: CredentialRefResolver,
    available_transports: Collection[str] | None = None,
) -> LiveDraftPreflightReport:
    package_path = Path(request.package_path).expanduser().resolve()
    if request.mode is not WorkflowRunMode.LIVE_READ:
        return _report(
            request,
            package_path=package_path,
            workflow_name="",
            package_fingerprint="",
            checked_actions=(),
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(package_path)
    if bundle_issues:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=bundle_issues,
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (package_path / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=credential_report.issues,
        )

    checked_actions, issues = _preflight_gate_steps(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index={},
        available_transports=available_transports,
        env=request.operator_env,
        contracts=_connector_contracts_for_documents(documents),
    )
    return _report(
        request,
        package_path=package_path,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        checked_actions=checked_actions,
        issues=issues,
    )


def preflight_live_production_workflow(
    request: WorkflowRunRequest,
    *,
    credential_resolver: CredentialRefResolver,
    available_transports: Collection[str] | None = None,
) -> LiveDraftPreflightReport:
    package_path = Path(request.package_path).expanduser().resolve()
    if request.mode is not WorkflowRunMode.LIVE_PRODUCTION:
        return _report(
            request,
            package_path=package_path,
            workflow_name="",
            package_fingerprint="",
            checked_actions=(),
            issues=(f"workflow run mode is not enabled: {request.mode.value}",),
        )

    documents = load_package_documents(package_path)
    workflow_name = str(documents.workflow.get("name", ""))
    package_fingerprint = fingerprint_package_documents(documents)

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in validation.issues),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in compiled.issues),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=tuple(issue.message for issue in policy.issues),
        )
    bundle_issues = _default_bundle_issues(package_path)
    if bundle_issues:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=bundle_issues,
        )

    fixture_name = request.fixture_name or DEFAULT_FIXTURE_NAME
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    store = WorkflowRunStore(request.run_root or (package_path / ".workflow-runs"))
    fixture = fixture_with_prior_run_outputs(fixture, store, request.approval_run_id)

    credential_report = preflight_credential_refs(documents, credential_resolver)
    if not credential_report.ok:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=credential_report.issues,
        )

    approval_index, approval_issues = _approval_index(
        store,
        request.approval_run_id,
        package_fingerprint=package_fingerprint,
        ir=compiled.ir,
        fixture=fixture,
        mode="live-production",
    )
    if approval_issues:
        return _report(
            request,
            package_path=package_path,
            workflow_name=workflow_name,
            package_fingerprint=package_fingerprint,
            checked_actions=(),
            issues=approval_issues,
        )

    contracts = _connector_contracts_for_documents(documents)
    start_step = _approved_live_start_step(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        approval_index=approval_index,
        connector_allowlist=set(request.connector_allowlist),
        mode=request.mode,
        contracts=contracts,
    )
    checked_actions, issues = _preflight_gate_steps(
        compiled.ir,
        fixture,
        mode=request.mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=available_transports,
        env=request.operator_env,
        contracts=contracts,
        start_step=start_step,
    )
    return _report(
        request,
        package_path=package_path,
        workflow_name=workflow_name,
        package_fingerprint=package_fingerprint,
        checked_actions=checked_actions,
        issues=issues,
    )


def credential_resolver_for_package_env(package_path: str | Path, env: Mapping[str, str]) -> CredentialRefResolver:
    documents = load_package_documents(package_path)
    connector_ids: list[str] = []
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_ids.append(str(permission["connector"]))
    return credential_resolver_from_env(tuple(dict.fromkeys(connector_ids)), env)


def format_live_draft_preflight_report(report: LiveDraftPreflightReport) -> str:
    return _format_preflight_report("Live-draft", report)


def format_live_read_preflight_report(report: LiveDraftPreflightReport) -> str:
    return _format_preflight_report("Live-read", report)


def format_live_production_preflight_report(report: LiveDraftPreflightReport) -> str:
    return _format_preflight_report("Live-production", report)


def _format_preflight_report(label: str, report: LiveDraftPreflightReport) -> str:
    lines = [
        f"{label} preflight: {report.status}",
        f"workflow: {report.workflow_name or '<unknown>'}",
        f"fixture: {report.fixture_name}",
    ]
    if report.approval_run_id:
        lines.append(f"approval run: {report.approval_run_id}")
    if report.checked_actions:
        lines.append("checked actions:")
        lines.extend(f"- {action}" for action in report.checked_actions)
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)


def _preflight_gate_steps(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    mode: WorkflowRunMode,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
    contracts: Mapping[str, ConnectorContract],
    start_step: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    decision = evaluate_live_gate(
        ir,
        fixture,
        mode=mode,
        package_fingerprint=package_fingerprint,
        connector_allowlist=connector_allowlist,
        approval_index=approval_index,
        available_transports=available_transports,
        env=env,
        start_step=start_step,
        contracts=contracts,
    )
    return decision.checked_actions, tuple(issue.message for issue in decision.issues)


def _connector_contracts_for_documents(documents: PackageDocuments) -> dict[str, ConnectorContract]:
    contracts = builtin_connector_contract_catalog()
    contracts.update(extension_connector_contract_catalog(documents, contract_catalog=contracts))
    return contracts


def _approval_index(
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


def _approved_live_start_step(
    ir: WorkflowIR,
    fixture: Mapping[str, Any],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    connector_allowlist: set[str],
    mode: WorkflowRunMode,
    contracts: Mapping[str, ConnectorContract],
) -> str | None:
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
            and _approved_start_mode_matches(step, action, action_ref=action_ref, mode=mode, contracts=contracts)
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


def _select_branch(step: WorkflowStep, fixture: Mapping[str, object]) -> tuple[str | None, str | None]:
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


def _is_mutating_step(step: WorkflowStep, action: ActionCapability) -> bool:
    return (
        step.approval is not None
        or step.side_effect_class in MUTATING_SIDE_EFFECTS
        or action.side_effect
        or action.approval_required is True
    )


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


def _default_bundle_issues(package_path: Path) -> tuple[str, ...]:
    return inspect_default_workflow_bundle(package_path).issues


def _report(
    request: WorkflowRunRequest,
    *,
    package_path: Path,
    workflow_name: str,
    package_fingerprint: str,
    checked_actions: tuple[str, ...],
    issues: tuple[str, ...],
) -> LiveDraftPreflightReport:
    return LiveDraftPreflightReport(
        workflow_name=workflow_name,
        package_path=package_path,
        package_fingerprint=package_fingerprint,
        fixture_name=request.fixture_name or DEFAULT_FIXTURE_NAME,
        approval_run_id=request.approval_run_id,
        status="blocked" if issues else "passed",
        checked_actions=checked_actions,
        issues=issues,
    )
