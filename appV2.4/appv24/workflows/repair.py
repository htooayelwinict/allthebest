"""Bounded workflow draft repair helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping

from appv24.workflows.live_validation import assistant_text_from_messages
from appv24.workflows.repair_directives import render_workflow_repair_directives
from appv24.workflows.validator import WorkflowDraftValidationResult, validate_workflow_draft_text

MUTATING_SIDE_EFFECT_CLASSES = {"create", "update", "delete", "send", "publish", "provision"}


@dataclass(frozen=True)
class WorkflowDraftRepairConfig:
    max_issues: int = 8
    max_attempts: int | None = None
    max_repeated_validation_results: int = 3
    max_seconds: float | None = None


@dataclass(frozen=True)
class WorkflowDraftRepairResult:
    validation: WorkflowDraftValidationResult | None
    attempts: int
    repaired: bool
    exhausted: bool
    feedback_prompts: tuple[str, ...] = ()
    stop_reason: str | None = None


def validate_latest_workflow_draft(messages: list[object]) -> WorkflowDraftValidationResult | None:
    text = assistant_text_from_messages(messages)
    if not text.strip():
        return None
    return validate_workflow_draft_text(text)


def compact_workflow_validation_issues(result: WorkflowDraftValidationResult, *, max_issues: int = 8) -> str:
    if result.ok:
        return "No validation issues."

    visible_count = max(0, max_issues)
    lines = [
        f"- {issue.code} {issue.location}: {issue.message}"
        for issue in result.validation.issues[:visible_count]
    ]
    remaining = len(result.validation.issues) - visible_count
    if remaining > 0:
        lines.append(f"- {remaining} more issue(s) were omitted from this repair prompt")
    return "\n".join(lines)


def compact_workflow_repair_hints(result: WorkflowDraftValidationResult, *, max_hints: int = 8) -> str:
    if result.ok or result.documents is None:
        return ""

    steps = _workflow_steps_by_id(result.documents.workflow)
    connector_actions = _connector_actions(result.documents.connectors)
    hints: list[str] = []
    for issue in result.validation.issues:
        if len(hints) >= max_hints:
            break
        if issue.code != "missing_side_effect_class" or issue.file != "workflow.yaml":
            continue
        step_id = _issue_step_id(issue)
        if step_id is None:
            continue
        step = steps.get(step_id)
        if step is None:
            continue
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        action = connector_actions.get((connector_id, action_id))
        if action is None or action.get("side_effect") is not True:
            continue
        side_effect_class = action.get("side_effect_class")
        if side_effect_class not in MUTATING_SIDE_EFFECT_CLASSES:
            continue
        hints.append(
            " ".join(
                [
                    f"- workflow.yaml step {step_id} uses {uses};",
                    f"connector action {uses} has side_effect: true and side_effect_class: {side_effect_class}.",
                    f"Add side_effect_class: {side_effect_class} to workflow.yaml step {step_id}",
                    "and keep its approval gate.",
                ]
            )
        )

    return "\n".join(hints)


def build_workflow_repair_prompt(
    result: WorkflowDraftValidationResult,
    *,
    attempt: int,
    max_attempts: int | None,
    max_issues: int = 8,
    repeated_validation_count: int = 1,
) -> str:
    issues = compact_workflow_validation_issues(result, max_issues=max_issues)
    directives = render_workflow_repair_directives(result, max_directives=max_issues)
    attempt_line = f"Repair attempt {attempt}."
    if max_attempts is not None:
        attempt_line = f"Repair attempt {attempt}/{max_attempts}."
    lines = [
        "The latest workflow draft failed appv24 workflow validation.",
        "",
        attempt_line,
        "",
        "This repair loop continues until the workflow validates. Treat validation issues like compiler errors.",
        "",
        "Validation issues:",
        issues,
        "",
    ]
    if repeated_validation_count > 1:
        lines.extend(
            [
                "Loop progress warning:",
                "The latest repair returned the same validation failure pattern as an earlier attempt.",
                "Make a different structural repair instead of restating the same package shape.",
                "",
            ]
        )
    if directives:
        lines.extend(
            [
                "Repair directives:",
                directives,
                "",
            ]
        )
    lines.extend(
        [
            "Return a complete corrected workflow package draft with these labeled fenced sections:",
            "- workflow.yaml",
            "- connectors.yaml",
            "- permissions.yaml",
            "- dry_run_fixtures/happy_path.yaml",
            "- dry_run_fixtures/rejection_path.yaml",
            "- rollback.md",
            "",
            "Rules:",
            "- Preserve the user's intended workflow.",
            "- Fix schema and semantic validation issues only.",
            "- Output the full package again, not a patch.",
            "- Do not call tools, APIs, shell commands, or write files.",
            "- For cron-like workflows, use workflow.yaml trigger type schedule.",
            "- workflow.yaml trigger type must be schedule, webhook, manual, or event.",
            "- connectors.yaml connectors[].actions must be a list of action objects.",
            "- connectors.yaml actions[].side_effect must be boolean true or false.",
            "- Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false unless they mutate external systems.",
            "- Only external create, update, delete, send, publish, or provision actions should be mutating.",
            "- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
            "- Brokered mutating actions require approval_required: true, dry_run_supported: true, idempotency.key_fields, and outputs.allowed.",
            "- Design-only connector actions must not be referenced by workflow steps.",
            "- Draft-only outputs must be represented as local artifact drafts, not connector send/publish calls.",
            "- Secret references may use secretRef: NAME, checksum fields, redacted: true, or non-secret references; never raw secret values.",
            "- secretRef values must be reference names only, never raw tokens, passwords, keys, or credentials.",
            "- Mutating workflow.yaml steps must use approval: <approval-id>; never use approval_gate:.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions.yaml permissions[].scopes must grant every required_scopes value used by granted actions.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
            "- The approval id in steps[].approval must exist in permissions.yaml approvals[].id.",
            "- approvals[].required_for must include the exact mutating workflow step id.",
        ]
    )
    return "\n".join(lines)


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


def _issue_step_id(issue: object) -> str | None:
    path = getattr(issue, "path", ())
    if not isinstance(path, tuple) or len(path) < 2 or path[0] != "steps":
        return None
    step_id = path[1]
    return step_id if isinstance(step_id, str) and step_id else None


def _validation_fingerprint(result: WorkflowDraftValidationResult) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (issue.code, issue.location, issue.message)
        for issue in result.validation.issues
    )


def repair_workflow_draft_messages(
    messages: list[object],
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftRepairConfig = WorkflowDraftRepairConfig(),
    on_attempt: Callable[[int, int | None, WorkflowDraftValidationResult], None] | None = None,
) -> WorkflowDraftRepairResult:
    validation = validate_latest_workflow_draft(messages)
    max_attempts = None if config.max_attempts is None else max(0, config.max_attempts)
    max_issues = max(0, config.max_issues)
    max_repeated_validation_results = max(1, config.max_repeated_validation_results)
    deadline = None if config.max_seconds is None else monotonic() + max(0.0, config.max_seconds)

    if validation is None:
        return WorkflowDraftRepairResult(
            validation=None,
            attempts=0,
            repaired=False,
            exhausted=False,
        )

    if validation.ok:
        return WorkflowDraftRepairResult(
            validation=validation,
            attempts=0,
            repaired=False,
            exhausted=False,
        )

    feedback_prompts: list[str] = []
    attempts = 0
    validation_fingerprint_counts: dict[tuple[tuple[str, str, str], ...], int] = {
        _validation_fingerprint(validation): 1
    }
    while max_attempts is None or attempts < max_attempts:
        if deadline is not None and monotonic() >= deadline:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=not validation.ok,
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="timeout",
            )

        attempt = attempts + 1
        fingerprint = _validation_fingerprint(validation)
        prompt = build_workflow_repair_prompt(
            validation,
            attempt=attempt,
            max_attempts=max_attempts,
            max_issues=max_issues,
            repeated_validation_count=validation_fingerprint_counts.get(fingerprint, 1),
        )
        feedback_prompts.append(prompt)
        if on_attempt is not None:
            on_attempt(attempt, max_attempts, validation)
        run_turn(prompt)
        attempts = attempt
        next_validation = validate_latest_workflow_draft(messages)
        if next_validation is None:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=not validation.ok,
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="missing_repair_draft",
            )
        validation = next_validation
        if validation.ok:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=True,
                exhausted=False,
                feedback_prompts=tuple(feedback_prompts),
            )
        fingerprint = _validation_fingerprint(validation)
        validation_fingerprint_counts[fingerprint] = validation_fingerprint_counts.get(fingerprint, 0) + 1
        if validation_fingerprint_counts[fingerprint] >= max_repeated_validation_results:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=True,
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="repeated_validation",
            )

    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not validation.ok,
        feedback_prompts=tuple(feedback_prompts),
        stop_reason="max_attempts" if not validation.ok else None,
    )
