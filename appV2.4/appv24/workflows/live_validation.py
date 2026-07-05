"""Live assistant workflow draft validation helpers."""

from __future__ import annotations

from appv24.workflows.quality_gate import WorkflowQualityResult, check_workflow_quality_documents
from appv24.workflows.validator import WorkflowDraftValidationResult

WORKFLOW_DESIGNER_PROFILE_ID = "workflow-designer-lite"


def should_validate_workflow_drafts(profile_id: str | None) -> bool:
    if profile_id is None:
        return False
    normalized = profile_id.strip().lower().replace("_", "-")
    return normalized == WORKFLOW_DESIGNER_PROFILE_ID


def latest_assistant_stop_reason(messages: list[object]) -> str | None:
    for message in reversed(messages):
        if _field(message, "role") == "assistant":
            stop_reason = _field(message, "stop_reason")
            return stop_reason if isinstance(stop_reason, str) else None
    return None


def assistant_text_from_messages(messages: list[object]) -> str:
    for message in reversed(messages):
        if _field(message, "role") != "assistant":
            continue

        content = _field(message, "content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        text_blocks: list[str] = []
        for block in content:
            if _field(block, "type") != "text":
                continue
            text = _field(block, "text")
            if isinstance(text, str):
                text_blocks.append(text)
        return "".join(text_blocks)
    return ""


def format_workflow_validation_report(result: WorkflowDraftValidationResult, max_issues: int = 12) -> str:
    if result.ok:
        return "Workflow draft validation: passed"

    issues = result.validation.issues
    visible_count = max(0, max_issues)
    lines = ["Workflow draft validation: failed"]
    lines.extend(f"- {issue.code} {issue.location}: {issue.message}" for issue in issues[:visible_count])

    remaining = len(issues) - visible_count
    if remaining > 0:
        lines.append(f"- ... {remaining} more issue(s)")
    return "\n".join(lines)


def workflow_quality_result_for_draft(
    result: WorkflowDraftValidationResult,
) -> WorkflowQualityResult | None:
    if not result.ok or result.documents is None:
        return None
    return check_workflow_quality_documents(result.documents)


def format_workflow_quality_report(
    result: WorkflowQualityResult,
    *,
    label: str = "Workflow draft quality gate",
    max_issues: int = 12,
) -> str:
    if result.ok:
        return f"{label}: passed"

    visible_count = max(0, max_issues)
    lines = [f"{label}: failed"]
    lines.extend(f"- {issue.code} {issue.location}: {issue.message}" for issue in result.issues[:visible_count])

    remaining = len(result.issues) - visible_count
    if remaining > 0:
        lines.append(f"- ... {remaining} more issue(s)")
    return "\n".join(lines)


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
