"""Deterministic dry-run simulator for compiled appv24 workflow IR."""

from __future__ import annotations

from typing import Any, Mapping

from appv24.workflows.broker import BrokerRequest, LocalWorkflowBroker
from appv24.workflows.ir import StepKind, WorkflowIR, WorkflowStep
from appv24.workflows.ledger import SimulationLedger, StepTrace
from appv24.workflows.policy import MUTATING_SIDE_EFFECTS, evaluate_policy


def simulate_workflow_fixture(
    ir: WorkflowIR,
    fixture_name: str,
    fixture: Mapping[str, Any],
    *,
    max_transitions: int = 200,
    broker: LocalWorkflowBroker | None = None,
) -> SimulationLedger:
    policy = evaluate_policy(ir)
    if not policy.ok:
        return SimulationLedger(
            workflow_name=ir.name,
            fixture_name=fixture_name,
            status="blocked",
            trace=(),
            terminal_step=None,
            simulated_side_effects=(),
            issues=tuple(issue.message for issue in policy.issues),
        )

    trace: list[StepTrace] = []
    side_effects: list[str] = []
    current = ir.entrypoint
    issues: list[str] = []
    transitions = 0

    while current:
        transitions += 1
        if transitions > max_transitions:
            issues.append("transition budget exceeded")
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        step = ir.steps.get(current)
        if step is None:
            issues.append(f"unknown step: {current}")
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        selected_branch = None
        next_step = step.next_step
        outputs, broker_status, broker_provider_id, broker_issues = _step_outputs(ir, step, fixture, broker)
        if broker_issues:
            issues.extend(broker_issues)
            trace.append(
                _trace(
                    step,
                    "blocked",
                    selected_branch,
                    outputs,
                    tuple(broker_issues),
                    broker_status,
                    broker_provider_id,
                )
            )
            return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        if step.kind is StepKind.DECISION:
            selected_branch, next_step = _select_branch(step, fixture)
            if next_step is None:
                issues.append(f"no branch matched for {step.step_id}")
                trace.append(_trace(step, "blocked", selected_branch, outputs, ("no branch matched",)))
                return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)
        if _is_side_effect_step(ir, step):
            side_effects.append(step.step_id)
        status = "terminal" if step.kind is StepKind.TERMINAL else "completed"
        trace.append(_trace(step, status, selected_branch, outputs, (), broker_status, broker_provider_id))
        if step.kind is StepKind.TERMINAL:
            return _ledger(ir, fixture_name, "passed", trace, step.step_id, side_effects, issues)
        current = next_step

    issues.append("workflow stopped without terminal step")
    return _ledger(ir, fixture_name, "blocked", trace, None, side_effects, issues)


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


def _mock_outputs(step: WorkflowStep, fixture: Mapping[str, Any]) -> Mapping[str, Any]:
    mocks = fixture.get("mock_responses", {})
    if not isinstance(mocks, dict) or step.uses is None:
        return {}
    value = mocks.get(step.uses.dotted, {})
    return value if isinstance(value, dict) else {}


def _step_outputs(
    ir: WorkflowIR,
    step: WorkflowStep,
    fixture: Mapping[str, Any],
    broker: LocalWorkflowBroker | None,
) -> tuple[Mapping[str, Any], str | None, str | None, tuple[str, ...]]:
    if broker is None or step.uses is None:
        return _mock_outputs(step, fixture), None, None, ()
    connector = ir.connectors.get(step.uses.connector_id)
    action = connector.actions.get(step.uses.action_id) if connector is not None else None
    allowed_outputs = action.allowed_outputs if action is not None else ()
    result = broker.invoke(
        BrokerRequest(
            step_id=step.step_id,
            connector_action=step.uses.dotted,
            input_ref=step.raw,
            fixture=fixture,
            allowed_outputs=allowed_outputs,
        )
    )
    if result.status == "blocked" and not result.issues:
        return result.outputs, result.status, result.provider_id, ("broker blocked step",)
    return result.outputs, result.status, result.provider_id, result.issues


def _is_side_effect_step(ir: WorkflowIR, step: WorkflowStep) -> bool:
    if step.side_effect_class in MUTATING_SIDE_EFFECTS:
        return True
    if step.uses is None:
        return False
    connector = ir.connectors.get(step.uses.connector_id)
    action = connector.actions.get(step.uses.action_id) if connector is not None else None
    return bool(action and action.side_effect)


def _trace(
    step: WorkflowStep,
    status: str,
    selected_branch: str | None,
    outputs: Mapping[str, Any],
    issues: tuple[str, ...],
    broker_status: str | None = None,
    broker_provider_id: str | None = None,
) -> StepTrace:
    return StepTrace(
        step_id=step.step_id,
        kind=step.kind.value,
        status=status,
        selected_branch=selected_branch,
        connector_action=step.uses.dotted if step.uses is not None else None,
        side_effect_class=step.side_effect_class,
        approval_id=step.approval,
        broker_status=broker_status,
        broker_provider_id=broker_provider_id,
        simulated_outputs=outputs,
        issues=issues,
    )


def _ledger(
    ir: WorkflowIR,
    fixture_name: str,
    status: str,
    trace: list[StepTrace],
    terminal_step: str | None,
    side_effects: list[str],
    issues: list[str],
) -> SimulationLedger:
    return SimulationLedger(
        workflow_name=ir.name,
        fixture_name=fixture_name,
        status=status,
        trace=tuple(trace),
        terminal_step=terminal_step,
        simulated_side_effects=tuple(side_effects),
        issues=tuple(issues),
    )
