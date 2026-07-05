"""Structured workflow validation-to-repair directives."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from appv24.workflows.quality_gate import check_workflow_quality_documents
from appv24.workflows.validator import WorkflowDraftValidationResult

MUTATING_SIDE_EFFECT_CLASSES = {"create", "update", "delete", "send", "publish", "provision"}
ACTION_REF_RE = re.compile(r"\b[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+\b")
DESIGN_ONLY_ACTION_RE = re.compile(r"design-only action (?P<action>[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)")
QUOTED_VALUE_RE = re.compile(r"'(?P<value>[^']+)'")
NO_BRANCH_MATCH_RE = re.compile(r"no branch matched for (?P<step>[A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class WorkflowRepairDirective:
    code: str
    location: str
    instruction: str
    rules: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"- {self.code} {self.location}: {self.instruction}"]
        lines.extend(f"  - {rule}" for rule in self.rules)
        return "\n".join(lines)


def render_workflow_repair_directives(
    result: WorkflowDraftValidationResult,
    *,
    max_directives: int = 8,
) -> str:
    directives = build_workflow_repair_directives(result, max_directives=max_directives)
    return "\n".join(directive.render() for directive in directives)


def build_workflow_repair_directives(
    result: WorkflowDraftValidationResult,
    *,
    max_directives: int = 8,
) -> tuple[WorkflowRepairDirective, ...]:
    directives: list[WorkflowRepairDirective] = []
    for issue in _repair_issues(result):
        if len(directives) >= max(0, max_directives):
            break
        directive = _directive_for_issue(result, issue)
        if directive is not None:
            directives.append(directive)
    return tuple(directives)


def _directive_for_issue(
    result: WorkflowDraftValidationResult,
    issue: object,
) -> WorkflowRepairDirective | None:
    code = str(getattr(issue, "code", ""))
    location = str(getattr(issue, "location", ""))
    message = str(getattr(issue, "message", ""))

    if code == "raw_secret":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Replace the inline secret value at this exact path.",
            rules=(
                "Use a secret reference field, checksum, redacted marker, or non-secret reference.",
                "For secret references, use reference names such as secretRef: CONNECTOR_CREDENTIAL_REF, not raw secret values.",
                'Use normal input keys with scalar values like github_token: "secretRef: GITHUB_TOKEN_REF".',
                "Do not invent keys like github_token_secretRef: GITHUB_TOKEN.",
                "Do not preserve or restate the inline secret value.",
                "Do not move the secret into another workflow package field.",
            ),
        )

    if code == "missing_side_effect_class":
        directive = _missing_side_effect_class_directive(result, issue)
        if directive is not None:
            return directive

    if code in {"missing_side_effect_contract", "invalid_side_effect_contract"}:
        action_ref = _first_action_ref(message)
        target = f" for {action_ref}" if action_ref else ""
        action = _action_for_ref(result, action_ref)
        side_effect_class = action.get("side_effect_class") if action is not None else None
        if side_effect_class not in MUTATING_SIDE_EFFECT_CLASSES:
            return WorkflowRepairDirective(
                code=code,
                location=location,
                instruction=f"Correct the side-effect classification for that connector action{target}.",
                rules=(
                    message,
                    "If this action only reads, classifies, analyzes, summarizes, drafts, formats, or writes local artifacts, set side_effect: false and remove broker-only metadata.",
                    "If it truly creates, updates, deletes, sends, publishes, or provisions externally, keep side_effect: true and add the broker side-effect metadata.",
                    "Brokered mutating actions require approval_required: true, dry_run_supported: true, explicit outputs.allowed, and idempotency.key_fields where required.",
                ),
            )
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Add the missing broker side-effect metadata to that connector action{target}.",
            rules=(
                message,
                "Mutating connector actions must declare side_effect_class, execution_mode, approval_required, and dry_run_supported.",
                "Brokered mutating actions require approval_required: true, dry_run_supported: true, explicit outputs.allowed, and idempotency.key_fields where required.",
            ),
        )

    if code == "design_only_action_used":
        action_ref = _design_only_action_ref(message)
        action_text = f" to design-only action {action_ref}" if action_ref else " to the design-only action"
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Remove the workflow step call{action_text}.",
            rules=(
                "Design-only connector actions may be declared but must not be called by workflow steps.",
                "keep the intended output as a non-executing local artifact draft instead.",
            ),
        )

    if code == "missing_approval":
        step_id = _issue_step_id(issue)
        target = f" {step_id}" if step_id else ""
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction=f"Resolve approval handling for workflow step{target}.",
            rules=(
                "If the step only reads, classifies, analyzes, summarizes, drafts, formats, or writes local artifacts, set the connector action side_effect: false instead of adding approval.",
                "If the step truly creates, updates, deletes, sends, publishes, or provisions externally, keep it mutating and add an explicit approval gate.",
                "Use workflow.yaml steps[].approval, not approval_gate.",
                "The approval id must exist in permissions.yaml approvals[].id.",
                "The approval's required_for list must include the exact mutating workflow step id.",
            ),
        )

    if code == "permission_missing_scope":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Grant the exact connector scope required by the referenced action.",
            rules=(
                message,
                "Add only the missing required scope; do not use wildcard scopes unless the draft already intentionally grants them.",
            ),
        )

    if code == "unsafe_rollback":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Rewrite rollback.md as manual prose only.",
            rules=(
                "Remove command examples, shell snippets, API examples, auth headers, and destructive verbs.",
                "Keep rollback guidance as operator review steps, audit notes, and manual correction guidance.",
            ),
        )

    if code in {"missing_rollback", "missing_fixture"}:
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add the missing required workflow package artifact.",
            rules=(message,),
        )

    if code in {"schema", "parse_error", "invalid_document", "missing_block", "duplicate_block"}:
        schema_directive = _schema_repair_directive(code, location, message, issue)
        if schema_directive is not None:
            return schema_directive
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix this exact package structure issue while preserving the workflow intent.",
            rules=(message,),
        )

    if code == "missing_transition":
        step_id = _issue_step_id(issue)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add the smallest correct next transition for this non-terminal step.",
            rules=(
                message,
                "Preserve existing step ids and step order.",
                "Prefer connecting to the next existing step in the intended flow.",
                "Do not redesign unrelated connector actions, permissions, fixtures, or rollback notes.",
                f"Target step: {step_id}." if step_id else "Use the failed path exactly as reported.",
            ),
        )

    if code == "missing_branch_transition":
        step_id = _issue_step_id(issue)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add explicit branches for this decision step.",
            rules=(
                message,
                "Use existing downstream step ids.",
                "Do not rename the decision step.",
                f"Target decision step: {step_id}." if step_id else "Use the failed path exactly as reported.",
            ),
        )

    if code == "terminal_unreachable":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Connect the entrypoint path to an existing terminal step.",
            rules=(
                message,
                "Prefer adding the smallest missing next transition or branch.",
                "Do not invent a new terminal if an existing terminal step can be reached.",
            ),
        )

    if code == "cycle_without_terminal_escape":
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Add an exit branch or transition from the cycle to an existing terminal path.",
            rules=(
                message,
                "Keep the loop only if it is part of the user intent.",
                "Do not remove approval gates or side-effect metadata.",
            ),
        )

    if code in {"fixture_no_terminal", "fixture_branch_unmatched", "fixture_transition_budget_exceeded", "fixture_broker_blocked"}:
        decision_rules = _fixture_decision_rules(result, issue, message)
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Fix the workflow transitions, fixture decisions, or dry-run support so this fixture reaches terminal.",
            rules=(
                message,
                "For decision failures, add the smallest valid dry_run_fixtures decision mapping.",
                *decision_rules,
                "For transition failures, fix workflow.yaml next or branches.",
                "For broker failures, preserve the connector action and make sure the dry-run path uses declared allowed outputs.",
            ),
        )

    return None


def _repair_issues(result: WorkflowDraftValidationResult) -> tuple[object, ...]:
    if not result.ok:
        return result.validation.issues
    if result.documents is None:
        return ()
    quality = check_workflow_quality_documents(result.documents)
    return () if quality.ok else quality.issues


def _schema_repair_directive(
    code: str,
    location: str,
    message: str,
    issue: object,
) -> WorkflowRepairDirective | None:
    file = str(getattr(issue, "file", ""))
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or not path:
        return None

    if file == "workflow.yaml" and _path_matches(path, "triggers", "type"):
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Normalize the workflow trigger type to the schema enum.",
            rules=(
                message,
                "workflow.yaml trigger type must be one of: schedule, webhook, manual, event.",
                "For cron-like triggers, use type: schedule with a schedule value.",
                "For event-like or signal-like triggers, use type: event and put signal metadata under event.",
                "Do not invent trigger enum values.",
            ),
        )

    if file == "workflow.yaml" and _path_matches(path, "steps", "uses"):
        value = _quoted_value(message)
        bare_action_hint = f" such as {value}" if value else ""
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Rewrite the workflow step action reference to match the connector action contract.",
            rules=(
                message,
                "workflow.yaml steps[].uses must be connector.action.",
                f"Do not use bare action ids{bare_action_hint}; use <connector-id>.<action-id>.",
                "The connector id and action id must exist in connectors.yaml.",
                "If needed, create a generic connector action for the intended local read, classify, analyze, draft, or approval-gated mutation.",
            ),
        )

    if file == "connectors.yaml" and _path_matches(path, "connectors", "actions", "execution_mode"):
        return WorkflowRepairDirective(
            code=code,
            location=location,
            instruction="Normalize the connector action execution mode to the schema enum.",
            rules=(
                message,
                "connectors.yaml actions[].execution_mode must be brokered, manual, or design_only.",
                "Replace sync, async, automatic, or auto with brokered for real external mutations.",
                "Use manual only for manually performed actions.",
                "Use design_only only for actions that workflow steps do not call.",
                "Do not invent execution_mode enum values.",
            ),
        )

    return None


def _path_matches(path: tuple[str | int, ...], *tail: str) -> bool:
    text_parts = tuple(str(part) for part in path)
    if len(text_parts) < len(tail):
        return False
    return text_parts[0] == tail[0] and text_parts[-1] == tail[-1]


def _missing_side_effect_class_directive(
    result: WorkflowDraftValidationResult,
    issue: object,
) -> WorkflowRepairDirective | None:
    if result.documents is None or str(getattr(issue, "file", "")) != "workflow.yaml":
        return None

    step_id = _issue_step_id(issue)
    if step_id is None:
        return None

    steps = _workflow_steps_by_id(result.documents.workflow)
    step = steps.get(step_id)
    if step is None:
        return None

    uses = step.get("uses")
    if not isinstance(uses, str):
        return None

    connector_id, _, action_id = uses.partition(".")
    action = _connector_actions(result.documents.connectors).get((connector_id, action_id))
    if action is None or action.get("side_effect") is not True:
        return None

    side_effect_class = action.get("side_effect_class")
    if side_effect_class not in MUTATING_SIDE_EFFECT_CLASSES:
        return None

    return WorkflowRepairDirective(
        code=str(getattr(issue, "code", "")),
        location=str(getattr(issue, "location", "")),
        instruction=(
            f"workflow.yaml step {step_id} uses {uses}. "
            f"Add side_effect_class: {side_effect_class} to workflow.yaml step {step_id}."
        ),
        rules=(
            "Keep the existing approval gate on the mutating step.",
            "Do not change connector ids, action ids, or user workflow intent.",
        ),
    )


def _workflow_steps_by_id(workflow: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return {}
    return {
        str(step["id"]): step
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }


def _connector_actions(connectors_document: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    actions: dict[tuple[str, str], Mapping[str, Any]] = {}
    connectors = connectors_document.get("connectors")
    if not isinstance(connectors, list):
        return actions
    for connector in connectors:
        if not isinstance(connector, dict) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        raw_actions = connector.get("actions")
        if not isinstance(raw_actions, list):
            continue
        for action in raw_actions:
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                actions[(connector_id, str(action["id"]))] = action
    return actions


def _action_for_ref(
    result: WorkflowDraftValidationResult,
    action_ref: str | None,
) -> Mapping[str, Any] | None:
    if result.documents is None or action_ref is None:
        return None
    connector_id, separator, action_id = action_ref.partition(".")
    if not separator:
        return None
    return _connector_actions(result.documents.connectors).get((connector_id, action_id))


def _issue_step_id(issue: object) -> str | None:
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or len(path) < 2 or path[0] != "steps":
        return None
    step_id = path[1]
    return step_id if isinstance(step_id, str) and step_id else None


def _fixture_decision_rules(result: WorkflowDraftValidationResult, issue: object, message: str) -> tuple[str, ...]:
    step_id = _decision_step_id_from_message(message)
    base_rules = [
        "Use top-level `decision:` in the fixture, not `decisions:`.",
        "Use branch keys as values, not target step ids.",
    ]
    if result.documents is None or step_id is None:
        return tuple(base_rules)

    branches = _workflow_decision_branches(result.documents.workflow, step_id)
    if not branches:
        return tuple(base_rules)

    branch_text = ", ".join(f"`{key}` -> `{target}`" for key, target in branches.items())
    first_key, first_target = next(iter(branches.items()))
    base_rules.extend(
        [
            f"Decision mapping key must be exactly `{step_id}`.",
            f"Decision step `{step_id}` branch map: {branch_text}.",
            f"Use `{step_id}: {first_key}`, not `{step_id}: {first_target}`.",
        ]
    )
    base_rules.extend(_current_fixture_decision_replacements(result, issue, step_id, branches))
    return tuple(base_rules)


def _decision_step_id_from_message(message: str) -> str | None:
    match = NO_BRANCH_MATCH_RE.search(message)
    if match is None:
        return None
    return match.group("step")


def _workflow_decision_branches(workflow: Mapping[str, Any], step_id: str) -> Mapping[str, str]:
    step = _workflow_steps_by_id(workflow).get(step_id)
    if step is None:
        return {}
    branches = step.get("branches")
    if not isinstance(branches, Mapping):
        return {}
    return {str(key): str(value) for key, value in branches.items() if isinstance(value, str)}


def _current_fixture_decision_replacements(
    result: WorkflowDraftValidationResult,
    issue: object,
    step_id: str,
    branches: Mapping[str, str],
) -> tuple[str, ...]:
    if result.documents is None:
        return ()

    fixture_name = _issue_fixture_name(issue)
    if fixture_name is None:
        return ()
    fixture = result.documents.dry_run_fixtures.get(fixture_name)
    if fixture is None:
        return ()

    decisions = _fixture_decisions(fixture)
    if not decisions:
        first_key = next(iter(branches))
        return (f"Add `decision: {{{step_id}: {first_key}}}` to {fixture_name}.",)

    target_to_branch = {target: key for key, target in branches.items()}
    rules: list[str] = []
    for current_key, current_value in decisions.items():
        replacement_value = current_value
        if current_value in target_to_branch:
            replacement_value = target_to_branch[current_value]
        if current_key != step_id or replacement_value != current_value:
            rules.append(f"Replace `{current_key}: {current_value}` with `{step_id}: {replacement_value}`.")
    if not rules:
        return ()
    rules.append(f"Remove any fixture decision keys that are not `{step_id}`.")
    return tuple(rules)


def _issue_fixture_name(issue: object) -> str | None:
    file = str(getattr(issue, "file", ""))
    prefix = "dry_run_fixtures/"
    if file.startswith(prefix):
        return file.removeprefix(prefix)
    location = str(getattr(issue, "location", ""))
    if location.startswith(prefix):
        return location.removeprefix(prefix).partition(":")[0]
    return None


def _fixture_decisions(fixture: Mapping[str, Any]) -> Mapping[str, str]:
    for key in ("decision", "decisions"):
        value = fixture.get(key)
        if isinstance(value, Mapping):
            return {str(decision_key): str(decision_value) for decision_key, decision_value in value.items()}
    nested = fixture.get("fixture")
    if isinstance(nested, Mapping):
        for key in ("decision", "decisions"):
            value = nested.get(key)
            if isinstance(value, Mapping):
                return {str(decision_key): str(decision_value) for decision_key, decision_value in value.items()}
    return {}


def _first_action_ref(message: str) -> str | None:
    match = ACTION_REF_RE.search(message)
    return match.group(0) if match is not None else None


def _design_only_action_ref(message: str) -> str | None:
    match = DESIGN_ONLY_ACTION_RE.search(message)
    if match is not None:
        return match.group("action")
    return _first_action_ref(message)


def _quoted_value(message: str) -> str | None:
    match = QUOTED_VALUE_RE.search(message)
    return match.group("value") if match is not None else None
