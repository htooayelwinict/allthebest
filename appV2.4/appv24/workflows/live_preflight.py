"""No-side-effect preflight checks for live workflow modes."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.approval_gate import ApprovalIndexKey, build_payload_bound_approval_index
from appv24.workflows.bundle_guard import inspect_default_workflow_bundle
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_contracts import builtin_connector_contract_catalog
from appv24.workflows.connector_packs import builtin_connector_catalog
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
from appv24.workflows.loader import load_package_documents
from appv24.workflows.http_safety import validate_http_fetch_url
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy
from appv24.workflows.run_store import WorkflowRunStore
from appv24.workflows.runner import DEFAULT_FIXTURE_NAME, MAX_TRANSITIONS
from appv24.workflows.validator import validate_workflow_documents


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
        WorkflowRunStore(request.run_root or (package_path / ".workflow-runs")),
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

    checked_actions, issues = _preflight_steps(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=available_transports,
        env=request.operator_env,
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

    checked_actions, issues = _preflight_live_read_steps(
        compiled.ir,
        fixture,
        connector_allowlist=set(request.connector_allowlist),
        available_transports=available_transports,
        env=request.operator_env,
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
        WorkflowRunStore(request.run_root or (package_path / ".workflow-runs")),
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

    checked_actions, issues = _preflight_live_production_steps(
        compiled.ir,
        fixture,
        package_fingerprint=package_fingerprint,
        connector_allowlist=set(request.connector_allowlist),
        approval_index=approval_index,
        available_transports=available_transports,
        env=request.operator_env,
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


def _preflight_live_read_steps(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    connector_allowlist: set[str],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    checked_actions: list[str] = []
    issues: list[str] = []
    current = ir.entrypoint
    transitions = 0
    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            break
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            _, current = _select_branch(step, fixture)
            if current is None:
                issues.append(f"no branch matched for {step.step_id}")
                break
            continue
        action = _action_for_step(ir, step)
        if step.uses is not None:
            checked_actions.append(step.uses.dotted)
        issue = _live_read_gate_issue(step, action, connector_allowlist)
        if issue is not None:
            issues.append(issue)
            break
        target_issue = _target_policy_issue(step, fixture, env=env)
        if target_issue is not None:
            issues.append(target_issue)
            break
        transport_issue = _transport_gate_issue("live-read", step, available_transports)
        if transport_issue is not None:
            issues.append(transport_issue)
            break
        current = step.next_step
    return tuple(checked_actions), tuple(issues)


def _preflight_steps(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    checked_actions: list[str] = []
    issues: list[str] = []
    current = ir.entrypoint
    transitions = 0
    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            break
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            _, current = _select_branch(step, fixture)
            if current is None:
                issues.append(f"no branch matched for {step.step_id}")
                break
            continue
        action = _action_for_step(ir, step)
        if step.uses is not None:
            checked_actions.append(step.uses.dotted)
        issue = _live_draft_gate_issue(
            step,
            action,
            connector_allowlist,
            package_fingerprint=package_fingerprint,
            approval_index=approval_index,
            fixture=fixture,
            env=env,
        )
        if issue is not None:
            issues.append(issue)
            break
        transport_issue = _transport_gate_issue("live-draft", step, available_transports)
        if transport_issue is not None:
            issues.append(transport_issue)
            break
        current = step.next_step
    return tuple(checked_actions), tuple(issues)


def _preflight_live_production_steps(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    checked_actions: list[str] = []
    issues: list[str] = []
    current = ir.entrypoint
    transitions = 0
    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append("transition budget exceeded")
            break
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            _, current = _select_branch(step, fixture)
            if current is None:
                issues.append(f"no branch matched for {step.step_id}")
                break
            continue
        action = _action_for_step(ir, step)
        if step.uses is not None:
            checked_actions.append(step.uses.dotted)
        issue = _live_production_gate_issue(
            step,
            action,
            connector_allowlist,
            package_fingerprint=package_fingerprint,
            approval_index=approval_index,
            fixture=fixture,
            env=env,
        )
        if issue is not None:
            issues.append(issue)
            break
        transport_issue = _transport_gate_issue("live-production", step, available_transports)
        if transport_issue is not None:
            issues.append(transport_issue)
            break
        target_issue = _target_policy_issue(step, fixture, env=env)
        if target_issue is not None:
            issues.append(target_issue)
            break
        current = step.next_step
    return tuple(checked_actions), tuple(issues)


def _live_read_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if _is_mutating_step(step, action):
        return f"live-read cannot execute mutating step {step.step_id}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-read action is not allowlisted: {step.uses.dotted}"
    metadata = builtin_connector_catalog().actions.get(step.uses.dotted)
    if metadata is None:
        return f"live-read action is not cataloged: {step.uses.dotted}"
    if not metadata.live_read_eligible:
        return f"live-read action is not live-read eligible: {step.uses.dotted}"
    return None


def _live_production_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, Any],
    env: Mapping[str, str],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-production action is not allowlisted: {step.uses.dotted}"
    metadata = builtin_connector_catalog().actions.get(step.uses.dotted)
    if metadata is None:
        return f"live-production action is not cataloged: {step.uses.dotted}"
    if _is_mutating_step(step, action):
        if not metadata.live_production_enabled:
            return f"live-production action is not production-enabled: {step.uses.dotted}"
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        if idempotency_key is None:
            return f"live-production requires idempotency key fields for {step.step_id}"
        if step.approval is None:
            return f"live-production requires approval for {step.step_id}"
        if approval_index.get((step.step_id, step.uses.dotted, idempotency_key)) is not True:
            return f"approval not granted for {step.step_id}"
        return None
    if not metadata.live_read_eligible:
        return f"live-production read action is not live-read eligible: {step.uses.dotted}"
    return None


def _target_policy_issue(step: WorkflowStep, fixture: Mapping[str, object], *, env: Mapping[str, str]) -> str | None:
    if step.uses is None:
        return None
    contract = builtin_connector_contract_catalog().get(step.uses.dotted)
    if contract is None:
        return None

    if step.uses.dotted == "http.fetch":
        allowed_hosts = _split_csv(env.get(contract.target_policy.allow_env or "", ""))
        if not allowed_hosts:
            return None
        url = _field_value(step, fixture, "url") or _field_value(step, fixture, "source_url")
        if url is None:
            return "http.fetch requires url"
        return validate_http_fetch_url(str(url), allowed_hosts=allowed_hosts)

    if step.uses.dotted == "telegram.send_message":
        allowed_chat_ids = _split_csv(env.get(contract.target_policy.allowed_chat_ids_env or "", ""))
        if not allowed_chat_ids:
            return "telegram.send_message requires APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        chat_id = _field_value(step, fixture, "chat_id")
        if chat_id is None:
            return "telegram.send_message requires chat_id"
        if str(chat_id) not in allowed_chat_ids:
            return f"telegram.send_message chat_id is not allowlisted: {chat_id}"
    return None


def _field_value(step: WorkflowStep, fixture: Mapping[str, object], field: str) -> object | None:
    raw_with = step.raw.get("with") if isinstance(step.raw, dict) else None
    if isinstance(raw_with, dict) and field in raw_with:
        value = raw_with[field]
        if isinstance(value, dict) and isinstance(value.get("inputRef"), str):
            input_values = fixture.get("input")
            if isinstance(input_values, Mapping):
                return input_values.get(value["inputRef"])
            return None
        return value
    input_values = fixture.get("input")
    if isinstance(input_values, Mapping):
        return input_values.get(field)
    return None


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _transport_gate_issue(mode: str, step: WorkflowStep, available_transports: Collection[str] | None) -> str | None:
    if available_transports is None or step.uses is None:
        return None
    action_ref = step.uses.dotted
    if action_ref not in available_transports:
        issue = f"{mode} transport is not configured for {action_ref}"
        hint = _transport_env_hint(action_ref)
        if hint is not None:
            issue = f"{issue}; required env: {hint}"
        return issue
    return None


def _transport_env_hint(action_ref: str) -> str | None:
    return {
        "brave.search": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_BRAVE_SEARCH_API_KEY",
        "github.list_dependabot_alerts": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
        "source.fetch_document": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        "source.fetch_items": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        "gmail.create_draft": (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or "
            "APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
        ),
        "telegram.send_message": (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
    }.get(action_ref)


def _live_draft_gate_issue(
    step: WorkflowStep,
    action: ActionCapability | None,
    connector_allowlist: set[str],
    *,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, Any],
    env: Mapping[str, str],
) -> str | None:
    if step.uses is None:
        return f"missing connector action for step {step.step_id}"
    if action is None:
        return f"unknown action for step {step.step_id}"
    if step.uses.dotted not in connector_allowlist:
        return f"live-draft action is not allowlisted: {step.uses.dotted}"
    metadata = builtin_connector_catalog().actions.get(step.uses.dotted)
    if metadata is None:
        return f"live-draft action is not cataloged: {step.uses.dotted}"
    if _is_mutating_step(step, action):
        if not metadata.live_draft_eligible:
            return f"live-draft action is not draft-eligible: {step.uses.dotted}"
        target_issue = _target_policy_issue(step, fixture, env=env)
        if target_issue is not None:
            return target_issue
        idempotency_key = _idempotency_key(package_fingerprint, step, action, fixture)
        if idempotency_key is None:
            return f"live-draft requires idempotency key fields for {step.step_id}"
        if step.approval is None:
            return f"live-draft requires approval for {step.step_id}"
        if approval_index.get((step.step_id, step.uses.dotted, idempotency_key)) is not True:
            return f"approval not granted for {step.step_id}"
        return None
    if not metadata.live_read_eligible:
        return f"live-draft read action is not live-read eligible: {step.uses.dotted}"
    return None


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
