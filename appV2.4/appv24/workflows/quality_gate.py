"""Workflow readiness gate for appv24 passive workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_sandbox import ConnectorSandboxProviders
from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.simulator import simulate_workflow_fixture
from appv24.workflows.validator import validate_workflow_documents

WorkflowQualitySeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class WorkflowQualityIssue:
    code: str
    file: str
    path: tuple[str | int, ...]
    message: str
    severity: WorkflowQualitySeverity = "error"
    repair_hint: str | None = None

    @property
    def location(self) -> str:
        if not self.path:
            return self.file
        suffix = ".".join(str(part) for part in self.path)
        return f"{self.file}:{suffix}"


@dataclass(frozen=True)
class WorkflowFixtureQualityReport:
    fixture_name: str
    status: str
    terminal_step: str | None
    trace_steps: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowQualityResult:
    root: Path
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    issues: tuple[WorkflowQualityIssue, ...] = ()
    fixture_reports: tuple[WorkflowFixtureQualityReport, ...] = ()

    @property
    def ok(self) -> bool:
        return self.validation_ok and self.compile_ok and self.policy_ok and not self.issues


def check_workflow_quality_package(
    path: str | Path,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    documents = load_package_documents(path)
    return check_workflow_quality_documents(documents, broker=broker)


def check_workflow_quality_documents(
    documents: PackageDocuments,
    *,
    broker: LocalWorkflowBroker | None = None,
) -> WorkflowQualityResult:
    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=False,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file=issue.file,
                    path=issue.path,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                )
                for issue in validation.issues
            ),
        )

    compiled = compile_workflow_documents(documents)
    if not compiled.ok or compiled.ir is None:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=False,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file=issue.file,
                    path=issue.path,
                    message=issue.message,
                    repair_hint=issue.repair_hint,
                )
                for issue in compiled.issues
            ),
        )

    policy = evaluate_policy(compiled.ir)
    if not policy.ok:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=True,
            policy_ok=False,
            issues=tuple(
                WorkflowQualityIssue(
                    code=issue.code,
                    file="workflow.yaml",
                    path=("steps", issue.step_id) if issue.step_id else (),
                    message=issue.message,
                    severity=issue.severity.value,
                )
                for issue in policy.issues
            ),
        )

    graph_issues = tuple((*_check_graph(compiled.ir), *_check_registered_user_broadcast_shape(compiled.ir)))
    if graph_issues:
        return WorkflowQualityResult(
            root=documents.root,
            validation_ok=True,
            compile_ok=True,
            policy_ok=True,
            issues=graph_issues,
        )

    local_broker = broker
    temp_artifacts: TemporaryDirectory[str] | None = None
    if local_broker is None:
        temp_artifacts = TemporaryDirectory(prefix="appv24-quality-artifacts-")
        providers = ConnectorSandboxProviders(Path(temp_artifacts.name))
        local_broker = LocalWorkflowBroker(providers.provider_map())
    try:
        fixture_issues, fixture_reports = _check_fixtures(compiled.ir, documents, local_broker)
    finally:
        if temp_artifacts is not None:
            temp_artifacts.cleanup()
    return WorkflowQualityResult(
        root=documents.root,
        validation_ok=True,
        compile_ok=True,
        policy_ok=True,
        issues=tuple(fixture_issues),
        fixture_reports=tuple(fixture_reports),
    )


def _check_graph(ir: WorkflowIR) -> list[WorkflowQualityIssue]:
    issues: list[WorkflowQualityIssue] = []
    reachable = _reachable_steps(ir)
    terminals = _terminal_steps(ir)
    for step in ir.steps.values():
        if step.step_id not in reachable and step.kind is not StepKind.TERMINAL:
            issues.append(
                WorkflowQualityIssue(
                    code="unreachable_step",
                    file="workflow.yaml",
                    path=("steps", step.step_id),
                    message=f"step {step.step_id} is not reachable from entrypoint",
                    repair_hint=f"Connect or remove workflow.yaml step {step.step_id}.",
                )
            )
        if step.kind is StepKind.TERMINAL:
            continue
        if step.kind is StepKind.DECISION:
            if not step.branches:
                issues.append(
                    WorkflowQualityIssue(
                        code="missing_branch_transition",
                        file="workflow.yaml",
                        path=("steps", step.step_id, "branches"),
                        message=f"decision step {step.step_id} has no branches",
                        repair_hint=f"Add branches to workflow.yaml decision step {step.step_id}.",
                    )
                )
            continue
        if step.next_step is not None:
            continue
        issues.append(
            WorkflowQualityIssue(
                code="missing_transition",
                file="workflow.yaml",
                path=("steps", step.step_id, "next"),
                message=f"non-terminal step {step.step_id} has no next transition",
                repair_hint=f"Add next to workflow.yaml step {step.step_id}.",
            )
        )
    if reachable and not any(_can_reach_terminal(ir, step_id, terminals) for step_id in reachable):
        issues.append(
            WorkflowQualityIssue(
                code="terminal_unreachable",
                file="workflow.yaml",
                path=("entrypoint",),
                message="no terminal step is reachable from entrypoint",
                repair_hint="Connect the entrypoint path to an existing terminal step.",
            )
        )
    for cycle in _reachable_cycles(ir, reachable):
        if not any(_can_reach_terminal(ir, step_id, terminals) for step_id in cycle):
            issues.append(
                WorkflowQualityIssue(
                    code="cycle_without_terminal_escape",
                    file="workflow.yaml",
                    path=("steps", ",".join(sorted(cycle))),
                    message=f"cycle has no outbound path to terminal: {', '.join(sorted(cycle))}",
                    repair_hint="Add a branch or transition from the cycle to an existing terminal path.",
                )
            )
    return issues


def _check_registered_user_broadcast_shape(ir: WorkflowIR) -> list[WorkflowQualityIssue]:
    subscriber_steps = _steps_using(ir, "telegram.list_subscribers")
    broadcast_steps = _steps_using(ir, "telegram.broadcast_message")
    if not subscriber_steps or not broadcast_steps:
        return []

    issues: dict[str, WorkflowQualityIssue] = {}
    for subscriber_step_id in subscriber_steps:
        for broadcast_step_id in broadcast_steps:
            for path in _paths_between(ir, subscriber_step_id, broadcast_step_id):
                for step_id in path[1:-1]:
                    step = ir.steps[step_id]
                    issue = _registered_user_broadcast_detour_issue(step)
                    if issue is not None:
                        issues.setdefault(step.step_id, issue)
    return list(issues.values())


def _steps_using(ir: WorkflowIR, action_ref: str) -> set[str]:
    return {
        step.step_id
        for step in ir.steps.values()
        if step.uses is not None and step.uses.dotted == action_ref
    }


def _paths_between(ir: WorkflowIR, start: str, target: str) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    stack: list[tuple[str, tuple[str, ...]]] = [(start, (start,))]
    max_depth = len(ir.steps) + 1
    while stack and len(paths) < 64:
        current, path = stack.pop()
        if len(path) > max_depth:
            continue
        step = ir.steps.get(current)
        if step is None:
            continue
        for next_step in reversed(step.outbound):
            if next_step in path:
                continue
            next_path = (*path, next_step)
            if next_step == target:
                paths.append(next_path)
            else:
                stack.append((next_step, next_path))
    return paths


def _registered_user_broadcast_detour_issue(step: WorkflowStep) -> WorkflowQualityIssue | None:
    action_ref = step.uses.dotted if step.uses is not None else None
    if action_ref in {"telegram.create_message_draft", "telegram.send_message_draft"}:
        detail = f"{action_ref} step {step.step_id}"
    elif step.kind is StepKind.MANUAL_APPROVAL:
        detail = f"manual approval step {step.step_id}"
    elif step.kind is StepKind.DECISION:
        detail = f"decision step {step.step_id}"
    else:
        return None
    return WorkflowQualityIssue(
        code="registered_user_broadcast_detour",
        file="workflow.yaml",
        path=("steps", step.step_id),
        message=(
            "registered-user telegram delivery must use connector interfaces directly: "
            f"remove {detail} before telegram.broadcast_message"
        ),
        repair_hint=(
            "Use brave.search -> telegram.list_subscribers -> llm.summarize -> "
            "telegram.broadcast_message -> done; put the approval on telegram.broadcast_message."
        ),
    )


def _reachable_steps(ir: WorkflowIR) -> set[str]:
    reachable: set[str] = set()
    stack = [ir.entrypoint]
    while stack:
        step_id = stack.pop()
        if step_id in reachable:
            continue
        step = ir.steps.get(step_id)
        if step is None:
            continue
        reachable.add(step_id)
        stack.extend(target for target in step.outbound if target not in reachable)
    return reachable


def _terminal_steps(ir: WorkflowIR) -> set[str]:
    return {step.step_id for step in ir.steps.values() if step.kind is StepKind.TERMINAL}


def _can_reach_terminal(ir: WorkflowIR, start: str, terminals: set[str]) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        step_id = stack.pop()
        if step_id in terminals:
            return True
        if step_id in seen:
            continue
        seen.add(step_id)
        step = ir.steps.get(step_id)
        if step is not None:
            stack.extend(step.outbound)
    return False


def _reachable_cycles(ir: WorkflowIR, reachable: set[str]) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[set[str]] = []

    def strongconnect(step_id: str) -> None:
        nonlocal index
        indices[step_id] = index
        lowlinks[step_id] = index
        index += 1
        stack.append(step_id)
        on_stack.add(step_id)

        step = ir.steps.get(step_id)
        targets = tuple(target for target in (step.outbound if step else ()) if target in reachable)
        for target in targets:
            if target not in indices:
                strongconnect(target)
                lowlinks[step_id] = min(lowlinks[step_id], lowlinks[target])
            elif target in on_stack:
                lowlinks[step_id] = min(lowlinks[step_id], indices[target])

        if lowlinks[step_id] != indices[step_id]:
            return

        component: set[str] = set()
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.add(member)
            if member == step_id:
                break
        if len(component) > 1 or _has_self_loop(ir, step_id):
            cycles.append(component)

    for step_id in sorted(reachable):
        if step_id not in indices:
            strongconnect(step_id)
    return cycles


def _has_self_loop(ir: WorkflowIR, step_id: str) -> bool:
    step = ir.steps.get(step_id)
    return bool(step and step_id in step.outbound)


def _check_fixtures(
    ir: WorkflowIR,
    documents: PackageDocuments,
    broker: LocalWorkflowBroker | None,
) -> tuple[list[WorkflowQualityIssue], list[WorkflowFixtureQualityReport]]:
    issues: list[WorkflowQualityIssue] = []
    reports: list[WorkflowFixtureQualityReport] = []
    for fixture_name, fixture in sorted(documents.dry_run_fixtures.items()):
        ledger = simulate_workflow_fixture(ir, fixture_name, fixture, broker=broker)
        reports.append(
            WorkflowFixtureQualityReport(
                fixture_name=fixture_name,
                status=ledger.status,
                terminal_step=ledger.terminal_step,
                trace_steps=tuple(step.step_id for step in ledger.trace),
                issues=ledger.issues,
            )
        )
        if ledger.terminal_step is not None:
            continue
        issues.append(_fixture_issue(fixture_name, ledger.issues))
    return issues, reports


def _fixture_issue(fixture_name: str, ledger_issues: tuple[str, ...]) -> WorkflowQualityIssue:
    code = _fixture_issue_code(ledger_issues)
    rendered_issues = "; ".join(ledger_issues) if ledger_issues else "simulation stopped without terminal"
    return WorkflowQualityIssue(
        code=code,
        file=f"dry_run_fixtures/{fixture_name}",
        path=(),
        message=f"fixture {fixture_name} did not reach a terminal step: {rendered_issues}",
        repair_hint="Fix workflow transitions, fixture decisions, or broker dry-run support so this fixture reaches terminal.",
    )


def _fixture_issue_code(ledger_issues: tuple[str, ...]) -> str:
    if any("no branch matched" in issue for issue in ledger_issues):
        return "fixture_branch_unmatched"
    if any("transition budget exceeded" in issue for issue in ledger_issues):
        return "fixture_transition_budget_exceeded"
    if any("broker" in issue or "dry-run provider" in issue for issue in ledger_issues):
        return "fixture_broker_blocked"
    return "fixture_no_terminal"
