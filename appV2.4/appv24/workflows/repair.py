"""Bounded workflow draft repair helpers."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from time import monotonic
from typing import Any, Mapping

import yaml

from appv24.ai.types import AssistantMessage, TextContent, empty_usage
from appv24.workflows.live_validation import assistant_text_from_messages, latest_assistant_stop_reason
from appv24.workflows.prompt_examples import render_repair_micro_examples
from appv24.workflows.prompt_fragments import (
    render_capability_catalog_fragment,
    render_rollback_safety_fragment,
    render_schema_reminders_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
)
from appv24.workflows.quality_gate import WorkflowQualityResult, check_workflow_quality_documents
from appv24.workflows.repair_directives import render_workflow_repair_directives
from appv24.workflows.repair_report import WorkflowRepairAttemptSummary, WorkflowRepairReport
from appv24.workflows.validator import WorkflowDraftValidationResult, validate_workflow_draft_text

MUTATING_SIDE_EFFECT_CLASSES = {"create", "update", "delete", "send", "publish", "provision"}
DESIGN_STRING_FIELDS = ("intent", "trigger")
DESIGN_LIST_FIELDS = (
    "inputs",
    "outputs",
    "assumptions",
    "side_effects",
    "approval_gates",
    "failure_modes",
    "rollback",
)
DESIGN_CONTRACT_FIELDS = (*DESIGN_STRING_FIELDS, *DESIGN_LIST_FIELDS)


@dataclass(frozen=True)
class WorkflowDraftRepairConfig:
    max_issues: int = 8
    max_attempts: int | None = None
    max_repeated_validation_results: int | None = None
    max_seconds: float | None = None


@dataclass(frozen=True)
class WorkflowDraftRepairResult:
    validation: WorkflowDraftValidationResult | None
    attempts: int
    repaired: bool
    exhausted: bool
    feedback_prompts: tuple[str, ...] = ()
    stop_reason: str | None = None
    repair_report: WorkflowRepairReport | None = None


def format_workflow_repair_stop_report(result: WorkflowDraftRepairResult) -> str:
    if result.validation is not None and result.validation.ok:
        return ""
    if not result.stop_reason:
        return ""
    if result.stop_reason == "extension_handoff":
        return ""
    if result.stop_reason == "aborted":
        return "Workflow draft repair stopped: aborted"
    return f"Workflow draft repair stopped: {result.stop_reason} after {result.attempts} attempt(s)"


def validate_latest_workflow_draft(messages: list[object]) -> WorkflowDraftValidationResult | None:
    text = assistant_text_from_messages(messages)
    if not text.strip():
        return None
    return validate_workflow_draft_text(text)


def compact_workflow_validation_issues(result: WorkflowDraftValidationResult, *, max_issues: int = 8) -> str:
    repair_issues = _repair_issues(result)
    if not repair_issues:
        return "No validation issues."

    visible_count = max(0, max_issues)
    lines = [_render_compact_issue(issue) for issue in repair_issues[:visible_count]]
    remaining = len(repair_issues) - visible_count
    if remaining > 0:
        lines.append(f"- {remaining} more issue(s) were omitted from this repair prompt")
    return "\n".join(lines)


def compact_workflow_failed_edit_scope(result: WorkflowDraftValidationResult, *, max_issues: int = 8) -> str:
    repair_issues = _repair_issues(result)
    if not repair_issues:
        return "No failed edit scope."

    visible_count = max(0, max_issues)
    lines = [_render_failed_edit_scope(issue) for issue in repair_issues[:visible_count]]
    remaining = len(repair_issues) - visible_count
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
    if result.validation.ok or result.documents is None:
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
    failure_kind = "workflow validation"
    if result.ok and _workflow_quality_result(result) is not None:
        failure_kind = "workflow validation or quality gate"
    attempt_line = f"Repair attempt {attempt}."
    if max_attempts is not None:
        attempt_line = f"Repair attempt {attempt}/{max_attempts}."
    lines = [
        f"The latest workflow draft failed appv24 {failure_kind}.",
        "",
        attempt_line,
        "",
        "This repair loop continues until the workflow validates and passes the quality gate. Treat validation and quality issues like compiler errors.",
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
            *_repair_package_block_labels(result),
            "",
            "Rules:",
            "- Preserve the user's intended workflow.",
            "- Fix schema and semantic validation issues only.",
            "- Return the full package because the validator consumes complete drafts; treat it as a minimal patched copy.",
            "- Do not call tools, APIs, shell commands, or write files.",
            render_schema_reminders_fragment(),
            render_capability_catalog_fragment(),
            render_side_effect_contract_fragment(),
            render_secret_reference_fragment(),
            render_rollback_safety_fragment(),
        ]
    )
    return "\n".join(lines)


def _repair_package_block_labels(result: WorkflowDraftValidationResult) -> list[str]:
    labels = [
        "- workflow.yaml",
        "- connectors.yaml",
        "- permissions.yaml",
        "- dry_run_fixtures/happy_path.yaml",
    ]
    for fixture_name in sorted(result.blocks.dry_run_fixtures):
        if fixture_name == "dry_run_fixtures/happy_path.yaml":
            continue
        labels.append(f"- {fixture_name}")
    labels.append("- rollback.md")
    return labels


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


def _validation_fingerprint(result: WorkflowDraftValidationResult) -> tuple[str, ...]:
    return tuple(
        f"{issue.code} {issue.location}: {issue.message}"
        for issue in _repair_issues(result)
    )


def _workflow_quality_result(result: WorkflowDraftValidationResult) -> WorkflowQualityResult | None:
    if not result.ok or result.documents is None:
        return None
    quality = check_workflow_quality_documents(result.documents)
    return None if quality.ok else quality


def _workflow_draft_ready(result: WorkflowDraftValidationResult) -> bool:
    return result.ok and _workflow_quality_result(result) is None


def _repair_issues(result: WorkflowDraftValidationResult) -> tuple[object, ...]:
    if not result.ok:
        return tuple(issue for issue in result.validation.issues if not _is_extension_handoff_issue(issue))
    quality = _workflow_quality_result(result)
    if quality is None:
        return ()
    return quality.issues


def _is_extension_handoff_issue(issue: object) -> bool:
    return str(getattr(issue, "code", "")).startswith("extension_qa")


def _workflow_draft_waiting_on_extension_handoff(result: WorkflowDraftValidationResult) -> bool:
    if result.ok or result.documents is None:
        return False
    issues = tuple(result.validation.issues)
    return bool(issues) and all(_is_extension_handoff_issue(issue) for issue in issues)


def _extension_handoff_repair_result(
    validation: WorkflowDraftValidationResult,
    *,
    attempts: int,
    repaired: bool,
    feedback_prompts: tuple[str, ...] = (),
    repair_report: WorkflowRepairReport | None = None,
) -> WorkflowDraftRepairResult:
    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=repaired,
        exhausted=False,
        feedback_prompts=feedback_prompts,
        stop_reason="extension_handoff",
        repair_report=repair_report,
    )


def repair_workflow_draft_messages(
    messages: list[object],
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftRepairConfig = WorkflowDraftRepairConfig(),
    on_attempt: Callable[[int, int | None, WorkflowDraftValidationResult], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    get_messages: Callable[[], list[object]] | None = None,
) -> WorkflowDraftRepairResult:
    def current_messages() -> list[object]:
        return get_messages() if get_messages is not None else messages

    validation = validate_latest_workflow_draft(current_messages())
    max_attempts = None if config.max_attempts is None else max(0, config.max_attempts)
    max_issues = max(0, config.max_issues)
    max_repeated_validation_results = (
        None
        if config.max_repeated_validation_results is None
        else max(1, config.max_repeated_validation_results)
    )
    deadline = None if config.max_seconds is None else monotonic() + max(0.0, config.max_seconds)

    if validation is None:
        return WorkflowDraftRepairResult(
            validation=None,
            attempts=0,
            repaired=False,
            exhausted=False,
            stop_reason="aborted" if _repair_aborted(current_messages(), should_abort) else None,
        )

    if _repair_aborted(current_messages(), should_abort):
        return _aborted_repair_result(validation, attempts=0)

    if _workflow_draft_waiting_on_extension_handoff(validation):
        return _extension_handoff_repair_result(validation, attempts=0, repaired=False)

    if _workflow_draft_ready(validation):
        return WorkflowDraftRepairResult(
            validation=validation,
            attempts=0,
            repaired=False,
            exhausted=False,
        )

    feedback_prompts: list[str] = []
    repair_attempts: list[WorkflowRepairAttemptSummary] = []
    attempts = 0
    validation_fingerprint_counts: dict[tuple[str, ...], int] = {
        _validation_fingerprint(validation): 1
    }
    while max_attempts is None or attempts < max_attempts:
        deterministic_validation = _apply_deterministic_workflow_repair(current_messages(), validation)
        if deterministic_validation is not None:
            validation = deterministic_validation
            fingerprint = _validation_fingerprint(validation)
            validation_fingerprint_counts[fingerprint] = validation_fingerprint_counts.get(fingerprint, 0) + 1
            if _workflow_draft_ready(validation):
                repair_attempts.append(WorkflowRepairAttemptSummary(attempt=attempts, status="passed", issue_fingerprints=()))
                return WorkflowDraftRepairResult(
                    validation=validation,
                    attempts=attempts,
                    repaired=True,
                    exhausted=False,
                    feedback_prompts=tuple(feedback_prompts),
                    repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
                )
            if _workflow_draft_waiting_on_extension_handoff(validation):
                return _extension_handoff_repair_result(
                    validation,
                    attempts=attempts,
                    repaired=True,
                    feedback_prompts=tuple(feedback_prompts),
                    repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
                )

        if _repair_aborted(current_messages(), should_abort):
            return _aborted_repair_result(
                validation,
                attempts=attempts,
                feedback_prompts=tuple(feedback_prompts),
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )

        if deadline is not None and monotonic() >= deadline:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=not _workflow_draft_ready(validation),
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="timeout",
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )

        attempt = attempts + 1
        fingerprint = _validation_fingerprint(validation)
        repair_attempts.append(
            WorkflowRepairAttemptSummary(
                attempt=attempt,
                status="failed",
                issue_fingerprints=fingerprint,
                repeated_count=max(0, validation_fingerprint_counts.get(fingerprint, 1) - 1),
            )
        )
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
        messages_after_turn = current_messages()
        next_validation = validate_latest_workflow_draft(messages_after_turn)
        if _repair_aborted(messages_after_turn, should_abort):
            return _aborted_repair_result(
                next_validation or validation,
                attempts=attempts,
                feedback_prompts=tuple(feedback_prompts),
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )
        if next_validation is None:
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=not _workflow_draft_ready(validation),
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="missing_repair_draft",
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )
        validation = next_validation
        if _workflow_draft_ready(validation):
            repair_attempts.append(WorkflowRepairAttemptSummary(attempt=attempts, status="passed", issue_fingerprints=()))
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=True,
                exhausted=False,
                feedback_prompts=tuple(feedback_prompts),
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )
        if _workflow_draft_waiting_on_extension_handoff(validation):
            return _extension_handoff_repair_result(
                validation,
                attempts=attempts,
                repaired=True,
                feedback_prompts=tuple(feedback_prompts),
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )
        fingerprint = _validation_fingerprint(validation)
        validation_fingerprint_counts[fingerprint] = validation_fingerprint_counts.get(fingerprint, 0) + 1
        if (
            max_repeated_validation_results is not None
            and validation_fingerprint_counts[fingerprint] >= max_repeated_validation_results
        ):
            return WorkflowDraftRepairResult(
                validation=validation,
                attempts=attempts,
                repaired=False,
                exhausted=True,
                feedback_prompts=tuple(feedback_prompts),
                stop_reason="repeated_validation",
                repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
            )

    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not _workflow_draft_ready(validation),
        feedback_prompts=tuple(feedback_prompts),
        stop_reason="max_attempts" if not _workflow_draft_ready(validation) else None,
        repair_report=WorkflowRepairReport(attempts=tuple(repair_attempts)),
    )


def _apply_deterministic_workflow_repair(
    messages: list[object],
    validation: WorkflowDraftValidationResult,
) -> WorkflowDraftValidationResult | None:
    patched_text = _patched_missing_design_contract_text(validation)
    if patched_text is None:
        return None

    patched_validation = validate_workflow_draft_text(patched_text)
    if not patched_validation.ok and _validation_fingerprint(patched_validation) == _validation_fingerprint(validation):
        return None

    _append_deterministic_repair_message(messages, patched_text)
    return patched_validation


def _patched_missing_design_contract_text(result: WorkflowDraftValidationResult) -> str | None:
    if result.documents is None:
        return None

    missing_fields = _missing_design_contract_fields(result)
    if not missing_fields:
        return None

    workflow = deepcopy(dict(result.documents.workflow))
    raw_design = workflow.get("design")
    design = deepcopy(raw_design) if isinstance(raw_design, dict) else {}
    changed = not isinstance(raw_design, dict)

    for field in DESIGN_CONTRACT_FIELDS if "design" in missing_fields else missing_fields:
        if field == "design":
            continue
        replacement = _default_design_value(field, workflow, result.documents.connectors, result.documents.permissions)
        if replacement is None:
            continue
        if design.get(field) != replacement:
            design[field] = replacement
            changed = True

    if not changed:
        return None

    workflow = _workflow_with_design(workflow, design)
    return _render_workflow_draft_text(
        workflow=workflow,
        connectors=result.documents.connectors,
        permissions=result.documents.permissions,
        dry_run_fixtures=result.documents.dry_run_fixtures,
        rollback_text=result.documents.rollback_text,
    )


def _missing_design_contract_fields(result: WorkflowDraftValidationResult) -> list[str]:
    fields: list[str] = []
    for issue in result.validation.issues:
        if issue.code != "missing_design_contract" or issue.file != "workflow.yaml":
            continue
        if issue.path == ("design",):
            fields.append("design")
            continue
        if len(issue.path) == 2 and issue.path[0] == "design" and isinstance(issue.path[1], str):
            fields.append(issue.path[1])
    return fields


def _workflow_with_design(workflow: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    if isinstance(workflow.get("design"), dict):
        workflow["design"] = design
        return workflow

    reordered: dict[str, Any] = {}
    inserted = False
    for key, value in workflow.items():
        if key == "design":
            continue
        if key == "triggers" and not inserted:
            reordered["design"] = design
            inserted = True
        reordered[key] = value
    if not inserted:
        reordered["design"] = design
    return reordered


def _default_design_value(
    field: str,
    workflow: Mapping[str, Any],
    connectors: Mapping[str, Any],
    permissions: Mapping[str, Any],
) -> str | list[str] | None:
    name = _workflow_name(workflow)
    if field == "intent":
        description = workflow.get("description")
        return description.strip() if isinstance(description, str) and description.strip() else f"Run {name}."
    if field == "trigger":
        return _trigger_summary(workflow)
    if field == "inputs":
        return [f"Approved trigger payload and dry-run fixture data for {name}."]
    if field == "outputs":
        return [f"Validated workflow trace, local draft artifacts, and allowed outputs for {name}."]
    if field == "assumptions":
        return [f"Runtime inputs, fixtures, and credential references for {name} are supplied by approved configuration."]
    if field == "side_effects":
        return [_side_effect_summary(workflow, connectors)]
    if field == "approval_gates":
        return _approval_gate_summaries(permissions)
    if field == "failure_modes":
        return ["Required input, fixture data, connector output, or approval decision may be unavailable during execution."]
    if field == "rollback":
        return ["Review generated artifacts and manually reverse any approved external mutation if one was executed."]
    return None


def _workflow_name(workflow: Mapping[str, Any]) -> str:
    name = workflow.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "the workflow"


def _trigger_summary(workflow: Mapping[str, Any]) -> str:
    triggers = workflow.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        return "Workflow starts from the configured trigger payload."

    first = triggers[0]
    if not isinstance(first, Mapping):
        return "Workflow starts from the configured trigger payload."
    trigger_type = first.get("type")
    trigger_id = first.get("id")
    if isinstance(trigger_type, str) and isinstance(trigger_id, str):
        return f"Workflow starts from the {trigger_type} trigger {trigger_id}."
    if isinstance(trigger_type, str):
        return f"Workflow starts from the {trigger_type} trigger."
    return "Workflow starts from the configured trigger payload."


def _side_effect_summary(workflow: Mapping[str, Any], connectors: Mapping[str, Any]) -> str:
    mutating_steps = _mutating_workflow_step_ids(workflow)
    if mutating_steps:
        joined = ", ".join(mutating_steps[:4])
        return f"Mutating steps run only after their explicit approval gates: {joined}."

    connector_actions = _connector_actions(connectors)
    if any(action.get("side_effect") is True for action in connector_actions.values()):
        return "Declared mutating connector actions are not executed unless a workflow step reaches an explicit approval gate."
    return "Workflow execution is read-only or local-draft-only and does not mutate external systems."


def _mutating_workflow_step_ids(workflow: Mapping[str, Any]) -> list[str]:
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return []
    ids: list[str] = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if step.get("side_effect_class") not in MUTATING_SIDE_EFFECT_CLASSES:
            continue
        step_id = step.get("id")
        ids.append(str(step_id) if step_id else "unnamed_step")
    return ids


def _approval_gate_summaries(permissions: Mapping[str, Any]) -> list[str]:
    approvals = permissions.get("approvals")
    if not isinstance(approvals, list):
        return ["No approval gate is required for read-only or local-draft-only execution."]

    summaries: list[str] = []
    for approval in approvals:
        if not isinstance(approval, Mapping):
            continue
        approval_id = approval.get("id")
        if isinstance(approval_id, str) and approval_id.strip():
            summaries.append(f"{approval_id.strip()} explicit approval is required before its listed step runs.")
    return summaries or ["No approval gate is required for read-only or local-draft-only execution."]


def _render_workflow_draft_text(
    *,
    workflow: Mapping[str, Any],
    connectors: Mapping[str, Any],
    permissions: Mapping[str, Any],
    dry_run_fixtures: Mapping[str, Mapping[str, Any]],
    rollback_text: str | None,
) -> str:
    sections = [
        ("yaml", "workflow.yaml", _dump_yaml(workflow)),
        ("yaml", "connectors.yaml", _dump_yaml(connectors)),
        ("yaml", "permissions.yaml", _dump_yaml(permissions)),
    ]
    sections.extend(
        ("yaml", f"dry_run_fixtures/{filename}", _dump_yaml(document))
        for filename, document in dry_run_fixtures.items()
    )
    if rollback_text is not None:
        sections.append(("markdown", "rollback.md", rollback_text.rstrip("\r\n")))

    lines = ["Here is the workflow package draft.", ""]
    for language, filename, body in sections:
        lines.append(f"```{language} {filename}")
        lines.append(body)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _dump_yaml(value: Mapping[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=False).rstrip()


def _append_deterministic_repair_message(messages: list[object], text: str) -> None:
    latest_assistant = _latest_typed_assistant_message(messages)
    if latest_assistant is not None:
        messages.append(
            AssistantMessage(
                content=[TextContent(text=text)],
                api=latest_assistant.api,
                provider=latest_assistant.provider,
                model=latest_assistant.model,
                usage=empty_usage(),
                stop_reason="stop",
            )
        )
        return

    messages.append(SimpleNamespace(role="assistant", content=[SimpleNamespace(type="text", text=text)]))


def _latest_typed_assistant_message(messages: list[object]) -> AssistantMessage | None:
    for message in reversed(messages):
        if isinstance(message, AssistantMessage):
            return message
    return None


def _repair_aborted(messages: list[object], should_abort: Callable[[], bool] | None) -> bool:
    if latest_assistant_stop_reason(messages) == "aborted":
        return True
    return bool(should_abort and should_abort())


def _aborted_repair_result(
    validation: WorkflowDraftValidationResult | None,
    *,
    attempts: int,
    feedback_prompts: tuple[str, ...] = (),
    repair_report: WorkflowRepairReport | None = None,
) -> WorkflowDraftRepairResult:
    return WorkflowDraftRepairResult(
        validation=validation,
        attempts=attempts,
        repaired=False,
        exhausted=not bool(validation and validation.ok),
        feedback_prompts=feedback_prompts,
        stop_reason="aborted",
        repair_report=repair_report,
    )
