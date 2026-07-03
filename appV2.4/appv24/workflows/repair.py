"""Bounded workflow draft repair helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appv24.workflows.live_validation import assistant_text_from_messages
from appv24.workflows.validator import WorkflowDraftValidationResult, validate_workflow_draft_text


@dataclass(frozen=True)
class WorkflowDraftRepairConfig:
    max_attempts: int = 2
    max_issues: int = 8


@dataclass(frozen=True)
class WorkflowDraftRepairResult:
    validation: WorkflowDraftValidationResult | None
    attempts: int
    repaired: bool
    exhausted: bool
    feedback_prompts: tuple[str, ...] = ()


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


def build_workflow_repair_prompt(
    result: WorkflowDraftValidationResult,
    *,
    attempt: int,
    max_attempts: int,
    max_issues: int = 8,
) -> str:
    issues = compact_workflow_validation_issues(result, max_issues=max_issues)
    return "\n".join(
        [
            "The latest workflow draft failed appv24 workflow validation.",
            "",
            f"Repair attempt {attempt}/{max_attempts}.",
            "",
            "Validation issues:",
            issues,
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
            "- Output the full package again, not a patch.",
            "- Do not call tools, APIs, shell commands, or write files.",
            "- For cron-like workflows, use workflow.yaml trigger type schedule.",
            "- workflow.yaml trigger type must be schedule, webhook, manual, or event.",
            "- connectors.yaml connectors[].actions must be a list of action objects.",
            "- connectors.yaml actions[].side_effect must be boolean true or false.",
            "- Mutating workflow.yaml steps must use approval: <approval-id>; never use approval_gate:.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions.yaml permissions[].scopes must grant every required_scopes value used by granted actions.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
            "- The approval id in steps[].approval must exist in permissions.yaml approvals[].id.",
            "- approvals[].required_for must include the exact mutating workflow step id.",
        ]
    )


def repair_workflow_draft_messages(
    messages: list[object],
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftRepairConfig = WorkflowDraftRepairConfig(),
    on_attempt: Callable[[int, int, WorkflowDraftValidationResult], None] | None = None,
) -> WorkflowDraftRepairResult:
    validation = validate_latest_workflow_draft(messages)
    max_attempts = max(0, config.max_attempts)
    max_issues = max(0, config.max_issues)

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
    for attempt in range(1, max_attempts + 1):
        prompt = build_workflow_repair_prompt(
            validation,
            attempt=attempt,
            max_attempts=max_attempts,
            max_issues=max_issues,
        )
        feedback_prompts.append(prompt)
        if on_attempt is not None:
            on_attempt(attempt, max_attempts, validation)
        run_turn(prompt)
        attempts = attempt
        next_validation = validate_latest_workflow_draft(messages)
        if next_validation is None:
            break
        validation = next_validation
        if validation.ok:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=True,
                exhausted=False,
                feedback_prompts=tuple(feedback_prompts),
            )

    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not validation.ok,
        feedback_prompts=tuple(feedback_prompts),
    )
