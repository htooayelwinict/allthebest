"""Reusable live workflow gate checks.

The gate is intentionally side-effect free. It validates a compiled workflow IR
against live mode, connector contracts, operator allowlists, approval state, and
configured live transports before the runner can dispatch to a broker.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
import re
from typing import Any

from appv24.workflows.connector_contracts import (
    ConnectorContract,
    ConnectorLiveMode,
    builtin_connector_contract_catalog,
    is_external_connector_action_payload,
)
from appv24.workflows.execution import WorkflowRunMode
from appv24.workflows.http_safety import validate_http_fetch_url
from appv24.workflows.idempotency import build_idempotency_key
from appv24.workflows.ir import ActionCapability, StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS
from appv24.workflows.runner import MAX_TRANSITIONS


STEP_OUTPUT_TEMPLATE_RE = re.compile(
    r"^\s*\{\{\s*(?:steps\.)?([A-Za-z0-9_-]+)\.(?:outputs\.)?([A-Za-z0-9_.-]+)\s*\}\}\s*$"
)


@dataclass(frozen=True)
class LiveGateIssue:
    code: str
    step_id: str | None
    action_ref: str | None
    message: str
    repair_hint: str | None = None


@dataclass(frozen=True)
class LiveGateDecision:
    ok: bool
    checked_actions: tuple[str, ...]
    issues: tuple[LiveGateIssue, ...]


def evaluate_live_gate(
    ir: WorkflowIR,
    fixture: Mapping[str, object],
    *,
    mode: WorkflowRunMode,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
    start_step: str | None = None,
) -> LiveGateDecision:
    """Evaluate the executable path for the selected live mode."""

    checked_actions: list[str] = []
    issues: list[LiveGateIssue] = []
    current = start_step or ir.entrypoint
    transitions = 0
    contracts = builtin_connector_contract_catalog()

    while current:
        transitions += 1
        if transitions > MAX_TRANSITIONS:
            issues.append(LiveGateIssue("transition_budget", current, None, "transition budget exceeded"))
            break
        step = ir.steps.get(current)
        if step is None:
            issues.append(LiveGateIssue("unknown_step", current, None, f"unknown step: {current}"))
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            current = _select_branch(step, fixture)
            if current is None:
                issues.append(LiveGateIssue("no_branch", step.step_id, None, f"no branch matched for {step.step_id}"))
                break
            continue

        action_ref = step.uses.dotted if step.uses is not None else None
        action = _action_for_step(ir, step)
        issue = gate_step_against_contract(
            step,
            action=action,
            action_ref=action_ref,
            mode=mode,
            package_fingerprint=package_fingerprint,
            connector_allowlist=connector_allowlist,
            approval_index=approval_index,
            available_transports=available_transports,
            env=env,
            fixture=fixture,
            contracts=contracts,
        )
        if issue is not None and _live_read_prefix_can_pause(mode, checked_actions, issue):
            break
        checked_actions.append(action_ref or "<missing>")
        if issue is not None:
            issues.append(issue)
            break
        current = step.next_step

    return LiveGateDecision(ok=not issues, checked_actions=tuple(checked_actions), issues=tuple(issues))


def gate_step_against_contract(
    step: WorkflowStep,
    *,
    action: ActionCapability | None,
    action_ref: str | None,
    mode: WorkflowRunMode,
    package_fingerprint: str,
    connector_allowlist: set[str],
    approval_index: Mapping[tuple[str, str, str | None], bool],
    available_transports: Collection[str] | None,
    env: Mapping[str, str],
    fixture: Mapping[str, object],
    contracts: Mapping[str, ConnectorContract] | None = None,
) -> LiveGateIssue | None:
    contracts = contracts or builtin_connector_contract_catalog()
    if action_ref is None:
        return LiveGateIssue("missing_action", step.step_id, None, f"missing connector action for step {step.step_id}")
    if action is None:
        return LiveGateIssue("unknown_action", step.step_id, action_ref, f"unknown action for step {step.step_id}")
    contract = contracts.get(action_ref)
    if contract is None:
        external_issue = _external_action_gate_issue(step, action, action_ref=action_ref, mode=mode)
        if external_issue is not None:
            return external_issue
        if action_ref not in connector_allowlist:
            return LiveGateIssue("missing_allowlist", step.step_id, action_ref, f"{mode.value} action is not allowlisted: {action_ref}")
        return _transport_gate_issue(mode.value, step, available_transports)

    mode_issue = _mode_gate_issue(
        step,
        action,
        action_ref=action_ref,
        contract=contract,
        mode=mode,
        package_fingerprint=package_fingerprint,
        approval_index=approval_index,
        fixture=fixture,
    )
    if mode_issue is not None:
        return mode_issue

    if action_ref not in connector_allowlist:
        return LiveGateIssue("missing_allowlist", step.step_id, action_ref, f"{mode.value} action is not allowlisted: {action_ref}")

    transport_issue = _transport_gate_issue(mode.value, step, available_transports)
    if transport_issue is not None:
        return transport_issue

    return _target_policy_issue(step, action_ref=action_ref, contract=contract, env=env, fixture=fixture)


def _external_action_gate_issue(
    step: WorkflowStep,
    action: ActionCapability,
    *,
    action_ref: str,
    mode: WorkflowRunMode,
) -> LiveGateIssue | None:
    if not is_external_connector_action_payload(action_ref, action.raw):
        return LiveGateIssue("uncataloged_action", step.step_id, action_ref, f"{mode.value} action is not cataloged: {action_ref}")
    if _is_mutating_step(step, action):
        return LiveGateIssue(
            "mode_mismatch",
            step.step_id,
            action_ref,
            f"{mode.value} external connector action is not read-only: {action_ref}",
        )
    if action.execution_mode != "brokered":
        return LiveGateIssue(
            "mode_mismatch",
            step.step_id,
            action_ref,
            f"{mode.value} external connector action must be brokered: {action_ref}",
        )
    return None


def _mode_gate_issue(
    step: WorkflowStep,
    action: ActionCapability,
    *,
    action_ref: str,
    contract: ConnectorContract,
    mode: WorkflowRunMode,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, object],
) -> LiveGateIssue | None:
    if mode is WorkflowRunMode.LIVE_READ:
        if _is_mutating_step(step, action):
            return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-read cannot execute mutating step {step.step_id}")
        if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
            return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-read action is not live-read eligible: {action_ref}")
        return None

    if mode is WorkflowRunMode.LIVE_DRAFT:
        if _is_mutating_step(step, action):
            if ConnectorLiveMode.LIVE_DRAFT not in contract.live_modes:
                return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-draft action is not draft-eligible: {action_ref}")
            return _approval_idempotency_issue(
                step,
                action,
                action_ref=action_ref,
                mode="live-draft",
                package_fingerprint=package_fingerprint,
                approval_index=approval_index,
                fixture=fixture,
            )
        if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
            return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-draft read action is not live-read eligible: {action_ref}")
        return None

    if mode is WorkflowRunMode.LIVE_PRODUCTION:
        if _is_mutating_step(step, action):
            if ConnectorLiveMode.LIVE_PRODUCTION not in contract.live_modes:
                return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-production action is not production-enabled: {action_ref}")
            return _approval_idempotency_issue(
                step,
                action,
                action_ref=action_ref,
                mode="live-production",
                package_fingerprint=package_fingerprint,
                approval_index=approval_index,
                fixture=fixture,
            )
        if ConnectorLiveMode.LIVE_READ not in contract.live_modes:
            return LiveGateIssue("mode_mismatch", step.step_id, action_ref, f"live-production read action is not live-read eligible: {action_ref}")
        return None

    return LiveGateIssue("mode_disabled", step.step_id, action_ref, f"workflow run mode is not enabled: {mode.value}")


def _approval_idempotency_issue(
    step: WorkflowStep,
    action: ActionCapability,
    *,
    action_ref: str,
    mode: str,
    package_fingerprint: str,
    approval_index: Mapping[tuple[str, str, str | None], bool],
    fixture: Mapping[str, object],
) -> LiveGateIssue | None:
    idempotency_key = build_idempotency_key(
        package_fingerprint=package_fingerprint,
        step_id=step.step_id,
        connector_action=action_ref,
        key_fields=action.idempotency_key_fields,
        input_ref=step.raw,
        fixture=fixture,
    )
    if idempotency_key is None:
        return LiveGateIssue("missing_idempotency", step.step_id, action_ref, f"{mode} requires idempotency key fields for {step.step_id}")
    if step.approval is None:
        return LiveGateIssue("missing_approval", step.step_id, action_ref, f"{mode} requires approval for {step.step_id}")
    if approval_index.get((step.step_id, action_ref, idempotency_key)) is not True:
        return LiveGateIssue("approval_not_granted", step.step_id, action_ref, f"approval not granted for {step.step_id}")
    return None


def _transport_gate_issue(mode: str, step: WorkflowStep, available_transports: Collection[str] | None) -> LiveGateIssue | None:
    if available_transports is None or step.uses is None:
        return None
    action_ref = step.uses.dotted
    if action_ref in available_transports:
        return None
    issue = f"{mode} transport is not configured for {action_ref}"
    hint = _transport_env_hint(action_ref)
    if hint is not None:
        issue = f"{issue}; required env: {hint}"
    return LiveGateIssue("missing_transport", step.step_id, action_ref, issue, repair_hint=hint)


def _target_policy_issue(
    step: WorkflowStep,
    *,
    action_ref: str,
    contract: ConnectorContract,
    env: Mapping[str, str],
    fixture: Mapping[str, object],
) -> LiveGateIssue | None:
    if action_ref == "http.fetch":
        allowed_hosts = _split_csv(env.get(contract.target_policy.allow_env or "", ""))
        if not allowed_hosts:
            return None
        url = _field_value(step, fixture, "url") or _field_value(step, fixture, "source_url")
        if url is None:
            return LiveGateIssue("missing_target", step.step_id, action_ref, "http.fetch requires url")
        issue = validate_http_fetch_url(url, allowed_hosts=allowed_hosts)
        if issue is not None:
            return LiveGateIssue("target_not_allowlisted", step.step_id, action_ref, issue)
        return None

    if action_ref in {"telegram.send_message", "telegram.broadcast_message"}:
        allowed_chat_ids = _split_csv(env.get(contract.target_policy.allowed_chat_ids_env or "", ""))
        if not allowed_chat_ids:
            return LiveGateIssue(
                "missing_target_allowlist",
                step.step_id,
                action_ref,
                f"{action_ref} requires APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
            )
        if action_ref == "telegram.broadcast_message":
            recipients = _field_values(step, fixture, "recipients")
            if not recipients:
                return LiveGateIssue("missing_target", step.step_id, action_ref, "telegram.broadcast_message requires recipients")
            for chat_id in recipients:
                if chat_id not in allowed_chat_ids:
                    return LiveGateIssue(
                        "target_not_allowlisted",
                        step.step_id,
                        action_ref,
                        f"telegram.broadcast_message chat_id is not allowlisted: {chat_id}",
                    )
            return None
        chat_id = _field_value(step, fixture, "chat_id")
        if chat_id is None:
            return LiveGateIssue("missing_target", step.step_id, action_ref, "telegram.send_message requires chat_id")
        if chat_id not in allowed_chat_ids:
            return LiveGateIssue(
                "target_not_allowlisted",
                step.step_id,
                action_ref,
                f"telegram.send_message chat_id is not allowlisted: {chat_id}",
            )
    return None


def _live_read_prefix_can_pause(mode: WorkflowRunMode, checked_actions: list[str], issue: LiveGateIssue) -> bool:
    if mode is not WorkflowRunMode.LIVE_READ or not checked_actions:
        return False
    return issue.code == "mode_mismatch" and (
        issue.message.startswith("live-read cannot execute mutating step ")
        or issue.message.startswith("live-read action is not live-read eligible: ")
    )


def _select_branch(step: WorkflowStep, fixture: Mapping[str, object]) -> str | None:
    decisions = fixture.get("decision", {})
    if isinstance(decisions, Mapping):
        selected = decisions.get(step.step_id)
        if isinstance(selected, str) and selected in step.branches:
            return step.branches[selected]
    if len(step.branches) == 1:
        return next(iter(step.branches.values()))
    return None


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


def _field_value(step: WorkflowStep, fixture: Mapping[str, object], key: str) -> str | None:
    with_map = step.raw.get("with")
    fixture_input = fixture.get("input")
    primary = with_map if isinstance(with_map, Mapping) else {}
    fallback = fixture_input if isinstance(fixture_input, Mapping) else {}
    value = _resolve_input_ref(primary.get(key), fallback, fixture=fixture)
    if value is None:
        value = fallback.get(key)
    return _string_value(value)


def _field_values(step: WorkflowStep, fixture: Mapping[str, object], key: str) -> tuple[str, ...]:
    with_map = step.raw.get("with")
    fixture_input = fixture.get("input")
    primary = with_map if isinstance(with_map, Mapping) else {}
    fallback = fixture_input if isinstance(fixture_input, Mapping) else {}
    value = _resolve_input_ref(primary.get(key), fallback, fixture=fixture)
    if value is None:
        value = fallback.get(key)
    if isinstance(value, list):
        return tuple(item for item in (_string_value(item) for item in value) if item is not None)
    scalar = _string_value(value)
    return (scalar,) if scalar is not None else ()


def _resolve_input_ref(
    value: object,
    fixture_input: Mapping[str, object],
    *,
    fixture: Mapping[str, object] | None = None,
) -> object:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if isinstance(input_ref, str):
            return fixture_input.get(input_ref)
    if isinstance(value, str) and fixture is not None:
        resolved = _resolve_step_output_template(value, fixture)
        if resolved is not None:
            return resolved
    return value


def _resolve_step_output_template(value: str, fixture: Mapping[str, object]) -> object | None:
    match = STEP_OUTPUT_TEMPLATE_RE.match(value)
    if match is None:
        return None
    step_id, output_path = match.groups()
    steps = fixture.get("steps")
    if not isinstance(steps, Mapping):
        return None
    step_entry = steps.get(step_id)
    if not isinstance(step_entry, Mapping):
        return None
    current: object = step_entry.get("outputs")
    for part in output_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _string_value(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _transport_env_hint(action_ref: str) -> str | None:
    return {
        "brave.search": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_BRAVE_SEARCH_API_KEY",
        "github.list_dependabot_alerts": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
        "source.fetch_document": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        "source.fetch_items": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        "http.fetch": "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_HTTP_FETCH_ALLOWED_HOSTS",
        "gmail.create_draft": (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or "
            "APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
        ),
        "telegram.send_message": (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
    }.get(action_ref)
