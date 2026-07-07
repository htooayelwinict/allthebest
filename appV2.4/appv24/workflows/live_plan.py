"""Read-only live execution planning for appv24 workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.fixtures import require_fixture
from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.loader import load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.validator import validate_workflow_documents


BUILTIN_LIVE_READ_TRANSPORTS = frozenset(
    {
        "github.list_dependabot_alerts",
        "brave.search",
        "source.fetch_document",
        "source.fetch_items",
    }
)
BUILTIN_LIVE_DRAFT_TRANSPORTS = frozenset({"gmail.create_draft"})
BUILTIN_LIVE_PRODUCTION_TRANSPORTS = frozenset({"telegram.send_message"})


@dataclass(frozen=True)
class WorkflowLivePlan:
    workflow_name: str
    fixture_name: str
    package_path: Path
    status: str
    live_read_actions: tuple[str, ...]
    live_draft_actions: tuple[str, ...]
    live_production_actions: tuple[str, ...]
    local_only_actions: tuple[str, ...]
    approval_actions: tuple[str, ...]
    transport_gaps: tuple[str, ...]
    phase_sequence: tuple[str, ...]
    next_commands: tuple[str, ...]
    issues: tuple[str, ...] = ()


def build_workflow_live_plan_from_path(package_path: str | Path, fixture_name: str = "happy_path.yaml") -> WorkflowLivePlan:
    package = Path(package_path).expanduser().resolve()
    documents = load_package_documents(package)
    workflow_name = str(documents.workflow.get("name", ""))

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return _blocked_plan(
            workflow_name,
            fixture_name,
            package,
            tuple(issue.message for issue in validation.issues),
        )
    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return _blocked_plan(
            workflow_name,
            fixture_name,
            package,
            tuple(issue.message for issue in compiled.issues),
        )
    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return _blocked_plan(
            workflow_name,
            fixture_name,
            package,
            tuple(issue.message for issue in policy.issues),
        )
    fixture = require_fixture(documents.dry_run_fixtures, fixture_name)
    return build_workflow_live_plan(compiled.ir, package, fixture_name, fixture=fixture)


def build_workflow_live_plan(
    ir: WorkflowIR,
    package_path: str | Path,
    fixture_name: str,
    *,
    fixture: Mapping[str, Any] | None = None,
) -> WorkflowLivePlan:
    package = Path(package_path).expanduser().resolve()
    live_read: list[str] = []
    live_draft: list[str] = []
    live_production: list[str] = []
    local_only: list[str] = []
    approvals: list[str] = []
    transport_gaps: list[str] = []

    catalog = builtin_connector_catalog()
    for step in ir.steps.values():
        if step.kind is not StepKind.ACTION or step.uses is None:
            continue
        action_ref = step.uses.dotted
        label = _action_label(step)
        metadata = catalog.actions.get(action_ref)
        if metadata is None:
            local_only.append(label)
        else:
            classified = False
            if metadata.live_read_eligible:
                live_read.append(label)
                classified = True
                if action_ref not in BUILTIN_LIVE_READ_TRANSPORTS:
                    transport_gaps.append(f"{action_ref}: no built-in live-read transport")
            if metadata.live_draft_eligible:
                live_draft.append(label)
                classified = True
                if action_ref not in BUILTIN_LIVE_DRAFT_TRANSPORTS:
                    transport_gaps.append(f"{action_ref}: no built-in live-draft transport")
            if metadata.live_production_enabled:
                live_production.append(label)
                classified = True
                if action_ref not in BUILTIN_LIVE_PRODUCTION_TRANSPORTS:
                    transport_gaps.append(f"{action_ref}: no built-in live-production transport")
            if not classified:
                local_only.append(label)
        if step.approval is not None:
            approvals.append(f"{label} approval={step.approval}")

    live_read_refs = _action_refs(live_read)
    live_draft_refs = _action_refs(live_draft)
    live_production_refs = _action_refs(live_production)
    phase_command_refs = _live_read_to_approval_refs(ir, fixture or {}) if approvals else ()
    return WorkflowLivePlan(
        workflow_name=ir.name,
        fixture_name=fixture_name,
        package_path=package,
        status=_status(live_read, live_draft, live_production, local_only, transport_gaps),
        live_read_actions=tuple(live_read),
        live_draft_actions=tuple(live_draft),
        live_production_actions=tuple(live_production),
        local_only_actions=tuple(local_only),
        approval_actions=tuple(approvals),
        transport_gaps=tuple(dict.fromkeys(transport_gaps)),
        phase_sequence=_phase_sequence(ir, fixture or {}),
        next_commands=_next_commands(
            package,
            fixture_name,
            live_read_refs=live_read_refs,
            live_read_to_approval_refs=phase_command_refs,
            live_draft_refs=live_draft_refs,
            live_production_refs=live_production_refs,
        ),
    )


def format_workflow_live_plan(plan: WorkflowLivePlan) -> str:
    lines = [
        f"Workflow live plan: {plan.status}",
        f"workflow: {plan.workflow_name or '<unknown>'}",
        f"fixture: {plan.fixture_name}",
    ]
    _append_section(lines, "live-read actions", plan.live_read_actions)
    _append_section(lines, "local/simulated actions", plan.local_only_actions)
    _append_section(lines, "live-draft actions", plan.live_draft_actions)
    _append_section(lines, "live-production actions", plan.live_production_actions)
    _append_section(lines, "approval actions", plan.approval_actions)
    _append_section(lines, "transport gaps", plan.transport_gaps)
    _append_section(lines, "phase sequence", plan.phase_sequence)
    _append_section(lines, "next commands", plan.next_commands)
    if plan.issues:
        _append_section(lines, "issues", plan.issues)
    return "\n".join(lines)


def _blocked_plan(
    workflow_name: str,
    fixture_name: str,
    package_path: Path,
    issues: tuple[str, ...],
) -> WorkflowLivePlan:
    return WorkflowLivePlan(
        workflow_name=workflow_name,
        fixture_name=fixture_name,
        package_path=package_path,
        status="blocked",
        live_read_actions=(),
        live_draft_actions=(),
        live_production_actions=(),
        local_only_actions=(),
        approval_actions=(),
        transport_gaps=(),
        phase_sequence=(),
        next_commands=(),
        issues=issues,
    )


def _status(
    live_read: list[str],
    live_draft: list[str],
    live_production: list[str],
    local_only: list[str],
    transport_gaps: list[str],
) -> str:
    if live_production and not live_read and not live_draft and not local_only and not transport_gaps:
        return "live-production"
    if live_draft and not live_read and not live_production and not local_only and not transport_gaps:
        return "live-draft"
    if live_read and not live_draft and not live_production and not local_only and not transport_gaps:
        return "live-read"
    if live_read or live_draft or live_production:
        return "partial-live"
    return "local-only"


def _next_commands(
    package: Path,
    fixture_name: str,
    *,
    live_read_refs: tuple[str, ...],
    live_read_to_approval_refs: tuple[str, ...],
    live_draft_refs: tuple[str, ...],
    live_production_refs: tuple[str, ...],
) -> tuple[str, ...]:
    commands: list[str] = []
    package_arg = str(package)
    if live_read_refs:
        allow = ",".join(live_read_refs)
        commands.append(f"/workflow-live-ready live-read {package_arg} {fixture_name} --allow {allow}")
        if live_read_to_approval_refs:
            phase_allow = ",".join(live_read_to_approval_refs)
            commands.append(f"/workflow-run-live-read-to-approval {package_arg} {fixture_name} --allow {phase_allow}")
        else:
            commands.append(f"/workflow-run-live-read {package_arg} {fixture_name} --allow {allow}")
    if live_draft_refs or live_production_refs:
        if not live_read_to_approval_refs:
            commands.append(f"/workflow-run-approval {package_arg} {fixture_name}")
        commands.append(f"/workflow-approvals {package_arg} <approval-run-id>")
    if live_draft_refs:
        allow = ",".join(live_draft_refs)
        commands.append(f"/workflow-live-ready live-draft {package_arg} {fixture_name} <approval-run-id> --allow {allow}")
        if all(action in BUILTIN_LIVE_DRAFT_TRANSPORTS for action in live_draft_refs):
            commands.append(f"/workflow-run-live-draft {package_arg} {fixture_name} <approval-run-id> --allow {allow}")
    if live_production_refs:
        allow = ",".join(live_production_refs)
        commands.append(f"/workflow-live-ready live-production {package_arg} {fixture_name} <approval-run-id> --allow {allow}")
        if all(action in BUILTIN_LIVE_PRODUCTION_TRANSPORTS for action in live_production_refs):
            commands.append(f"/workflow-run-live-production {package_arg} {fixture_name} <approval-run-id> --allow {allow}")
    return tuple(commands)


def _live_read_to_approval_refs(ir: WorkflowIR, fixture: Mapping[str, Any]) -> tuple[str, ...]:
    catalog = builtin_connector_catalog()
    refs: list[str] = []
    saw_live_read = False
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > 200:
            break
        step = ir.steps.get(current)
        if step is None or step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            _selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                break
            current = next_step
            continue
        if step.kind is not StepKind.ACTION or step.uses is None:
            current = step.next_step
            continue

        action_ref = step.uses.dotted
        metadata = catalog.actions.get(action_ref)
        is_live_read = metadata is not None and metadata.live_read_eligible
        if is_live_read:
            refs.append(action_ref)
            saw_live_read = True
            current = step.next_step
            continue
        if saw_live_read:
            refs.append(action_ref)
            break
        current = step.next_step

    return tuple(dict.fromkeys(refs)) if saw_live_read and len(refs) > 1 else ()


def _action_label(step: WorkflowStep) -> str:
    return f"{step.step_id}: {step.uses.dotted if step.uses is not None else '<manual>'}"


def _phase_sequence(ir: WorkflowIR, fixture: Mapping[str, Any]) -> tuple[str, ...]:
    catalog = builtin_connector_catalog()
    groups: list[tuple[str, list[str]]] = []
    current = ir.entrypoint
    transitions = 0

    while current:
        transitions += 1
        if transitions > 200:
            _append_phase_group(groups, "blocked", "transition budget exceeded")
            break
        step = ir.steps.get(current)
        if step is None:
            _append_phase_group(groups, "blocked", f"unknown step: {current}")
            break
        if step.kind is StepKind.TERMINAL:
            break
        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            label = f"{step.step_id}: decision"
            if selected_branch is not None:
                label = f"{label} -> {selected_branch}"
            _append_phase_group(groups, "local", label)
            if next_step is None:
                break
            current = next_step
            continue

        phase, label = _phase_for_action(step, catalog.actions)
        _append_phase_group(groups, phase, label)
        current = step.next_step

    return tuple(f"{index} {phase}: {', '.join(actions)}" for index, (phase, actions) in enumerate(groups, start=1))


def _phase_for_action(step: WorkflowStep, catalog_actions: Mapping[str, Any]) -> tuple[str, str]:
    label = _action_label(step)
    if step.uses is None:
        return "local", label
    action_ref = step.uses.dotted
    metadata = catalog_actions.get(action_ref)
    if metadata is None:
        return "local", label
    if metadata.live_production_enabled:
        return "live-production", _transport_gap_label(label, action_ref, BUILTIN_LIVE_PRODUCTION_TRANSPORTS)
    if metadata.live_draft_eligible:
        return "live-draft", _transport_gap_label(label, action_ref, BUILTIN_LIVE_DRAFT_TRANSPORTS)
    if metadata.live_read_eligible:
        return "live-read", _transport_gap_label(label, action_ref, BUILTIN_LIVE_READ_TRANSPORTS)
    return "local", label


def _transport_gap_label(label: str, action_ref: str, builtins: frozenset[str]) -> str:
    if action_ref in builtins:
        return label
    return f"{label} (transport gap)"


def _append_phase_group(groups: list[tuple[str, list[str]]], phase: str, label: str) -> None:
    if groups and groups[-1][0] == phase:
        groups[-1][1].append(label)
        return
    groups.append((phase, [label]))


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


def _action_refs(labels: list[str]) -> tuple[str, ...]:
    refs: list[str] = []
    for label in labels:
        _, _, action_ref = label.partition(": ")
        if action_ref:
            refs.append(action_ref)
    return tuple(dict.fromkeys(refs))


def _append_section(lines: list[str], title: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    lines.append(f"{title}:")
    lines.extend(f"- {value}" for value in values)
