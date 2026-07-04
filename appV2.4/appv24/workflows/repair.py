"""Bounded workflow draft repair helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping

from appv24.workflows.live_validation import assistant_text_from_messages, latest_assistant_stop_reason
from appv24.workflows.prompt_examples import render_repair_micro_examples
from appv24.workflows.prompt_fragments import (
    render_rollback_safety_fragment,
    render_schema_reminders_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
)
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
    lines = [_render_compact_issue(issue) for issue in result.validation.issues[:visible_count]]
    remaining = len(result.validation.issues) - visible_count
    if remaining > 0:
        lines.append(f"- {remaining} more issue(s) were omitted from this repair prompt")
    return "\n".join(lines)


def compact_workflow_failed_edit_scope(result: WorkflowDraftValidationResult, *, max_issues: int = 8) -> str:
    if result.ok:
        return "No failed edit scope."

    visible_count = max(0, max_issues)
    lines = [_render_failed_edit_scope(issue) for issue in result.validation.issues[:visible_count]]
    remaining = len(result.validation.issues) - visible_count
    if remaining > 0:
        lines.append(f"- {remaining} more failed path(s) were omitted from this repair prompt")
    return "\n".join(lines)


def _render_compact_issue(issue: object) -> str:
    line = f"- {issue.code} {issue.location}: {issue.message}"
    expected = getattr(issue, "expected", None)
    if expected:
        line = f"{line} Expected: {expected}."
    repair_hint = getattr(issue, "repair_hint", None)
    if repair_hint:
        line = f"{line} Repair: {repair_hint}"
    return line


def _render_failed_edit_scope(issue: object) -> str:
    location = getattr(issue, "location", getattr(issue, "file", "<unknown>"))
    return f"- {location} [{getattr(issue, 'code', 'validation_issue')}]"


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
    failed_scope = compact_workflow_failed_edit_scope(result, max_issues=max_issues)
    directives = render_workflow_repair_directives(result, max_directives=max_issues)
    micro_examples = render_repair_micro_examples(result)
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
                "Do not redesign the package.",
                "If your last repair changed unrelated sections, revert those unrelated changes, then fix only the failed paths below.",
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
    if micro_examples:
        lines.extend(
            [
                micro_examples,
                "",
            ]
        )
    lines.extend(
        [
            "Scoped repair contract:",
            "- Use the latest workflow package draft already in this conversation as the source document.",
            "- Edit only the files and paths listed under Failed edit scope and Repair directives.",
            "- Preserve every unrelated file, field, id, name, step order, approval, fixture, and rollback line exactly.",
            "- Do not redesign, rename, reorder, add new workflow concepts, or rewrite passing sections.",
            "- If a listed issue requires a missing artifact or field, add the smallest valid artifact or field.",
            "- Return the full package as a patched copy for validation; do not output a diff.",
            "",
            "Failed edit scope:",
            failed_scope,
            "",
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
            "- Return the full package because the validator consumes complete drafts; treat it as a minimal patched copy.",
            "- Do not call tools, APIs, shell commands, or write files.",
            render_schema_reminders_fragment(),
            render_side_effect_contract_fragment(),
            render_secret_reference_fragment(),
            render_rollback_safety_fragment(),
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
    should_abort: Callable[[], bool] | None = None,
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
            stop_reason="aborted" if _repair_aborted(messages, should_abort) else None,
        )

    if _repair_aborted(messages, should_abort):
        return _aborted_repair_result(validation, attempts=0)

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
        if _repair_aborted(messages, should_abort):
            return _aborted_repair_result(
                validation,
                attempts=attempts,
                feedback_prompts=tuple(feedback_prompts),
            )

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
        if _repair_aborted(messages, should_abort):
            return _aborted_repair_result(
                next_validation or validation,
                attempts=attempts,
                feedback_prompts=tuple(feedback_prompts),
            )
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


def _repair_aborted(messages: list[object], should_abort: Callable[[], bool] | None) -> bool:
    if latest_assistant_stop_reason(messages) == "aborted":
        return True
    return bool(should_abort and should_abort())


def _aborted_repair_result(
    validation: WorkflowDraftValidationResult | None,
    *,
    attempts: int,
    feedback_prompts: tuple[str, ...] = (),
) -> WorkflowDraftRepairResult:
    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not bool(validation and validation.ok),
        feedback_prompts=feedback_prompts,
        stop_reason="aborted",
    )
