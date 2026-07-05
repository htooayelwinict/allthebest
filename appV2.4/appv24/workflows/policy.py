"""Static policy evaluation for compiled appv24 workflow IR."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from appv24.workflows.catalog import is_builtin_workflow_action
from appv24.workflows.ir import WorkflowIR, WorkflowStep


MUTATING_SIDE_EFFECTS = {"create", "update", "delete", "send", "publish", "provision"}
IDEMPOTENCY_REQUIRED_CLASSES = {"create", "send", "publish", "provision"}


class PolicySeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    message: str
    severity: PolicySeverity = PolicySeverity.ERROR
    step_id: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class PolicyReport:
    issues: tuple[PolicyIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity is PolicySeverity.ERROR for issue in self.issues)


def evaluate_policy(ir: WorkflowIR) -> PolicyReport:
    issues: list[PolicyIssue] = []
    for step in ir.steps.values():
        action = _action_for_step(ir, step)
        if action is not None and action.execution_mode == "brokered" and not is_builtin_workflow_action(action.dotted):
            issues.append(
                PolicyIssue(
                    "policy_unknown_catalog_action",
                    f"{action.dotted} is not in the built-in workflow capability catalog",
                    step_id=step.step_id,
                    source=action.dotted,
                )
            )
        if not _is_mutating_step(step, action_side_effect=bool(action and action.side_effect)):
            continue
        if not step.approval:
            issues.append(PolicyIssue("policy_missing_approval", f"{step.step_id} requires approval", step_id=step.step_id))
        elif step.approval not in ir.approvals:
            issues.append(
                PolicyIssue(
                    "policy_unknown_approval",
                    f"{step.step_id} references unknown approval {step.approval}",
                    step_id=step.step_id,
                )
            )
        elif step.step_id not in ir.approvals[step.approval].required_for:
            issues.append(
                PolicyIssue(
                    "policy_approval_required_for",
                    f"{step.approval} must include {step.step_id}",
                    step_id=step.step_id,
                )
            )
        if action is None:
            continue
        for scope in action.required_scopes:
            if scope not in action.granted_scopes and "*" not in action.granted_scopes:
                issues.append(
                    PolicyIssue(
                        "policy_missing_scope",
                        f"{action.dotted} requires scope {scope}",
                        step_id=step.step_id,
                        source=action.dotted,
                    )
                )
        if action.execution_mode == "brokered":
            if action.dry_run_supported is not True:
                issues.append(
                    PolicyIssue(
                        "policy_missing_dry_run",
                        f"{action.dotted} requires dry_run_supported",
                        step_id=step.step_id,
                        source=action.dotted,
                    )
                )
            if "*" in action.allowed_outputs:
                issues.append(
                    PolicyIssue(
                        "policy_wildcard_outputs",
                        f"{action.dotted} must not allow wildcard outputs",
                        step_id=step.step_id,
                        source=action.dotted,
                    )
                )
            if not action.allowed_outputs:
                issues.append(
                    PolicyIssue(
                        "policy_missing_outputs",
                        f"{action.dotted} requires outputs.allowed",
                        step_id=step.step_id,
                        source=action.dotted,
                    )
                )
            if action.side_effect_class in IDEMPOTENCY_REQUIRED_CLASSES and not action.idempotency_key_fields:
                issues.append(
                    PolicyIssue(
                        "policy_missing_idempotency",
                        f"{action.dotted} requires idempotency key fields",
                        step_id=step.step_id,
                        source=action.dotted,
                    )
                )
        if action.execution_mode == "design_only":
            issues.append(
                PolicyIssue(
                    "policy_design_only_used",
                    f"{step.step_id} must not call design-only action {action.dotted}",
                    step_id=step.step_id,
                    source=action.dotted,
                )
            )
    return PolicyReport(issues=tuple(sorted(issues, key=lambda issue: (issue.code, issue.step_id or "", issue.message))))


def _action_for_step(ir: WorkflowIR, step: WorkflowStep):
    if step.uses is None:
        return None
    connector = ir.connectors.get(step.uses.connector_id)
    if connector is None:
        return None
    return connector.actions.get(step.uses.action_id)


def _is_mutating_step(step: WorkflowStep, *, action_side_effect: bool) -> bool:
    return step.side_effect_class in MUTATING_SIDE_EFFECTS or action_side_effect
