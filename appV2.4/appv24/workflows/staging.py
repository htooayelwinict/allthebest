"""Bounded workflow draft staging helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appv24.workflows.live_validation import should_validate_workflow_drafts
from appv24.workflows.prompt_examples import PACKAGE_STAGE_MICRO_EXAMPLES
from appv24.workflows.prompt_fragments import (
    render_capability_catalog_fragment,
    render_passive_safety_fragment,
    render_required_package_blocks_fragment,
    render_rollback_safety_fragment,
    render_schema_reminders_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
    render_workflow_passive_boundary_fragment,
)


DEFAULT_WORKFLOW_STAGE_IDS = ("intent", "skeleton", "package")


@dataclass(frozen=True)
class WorkflowDraftStageConfig:
    enabled: bool = True
    stage_ids: tuple[str, ...] = DEFAULT_WORKFLOW_STAGE_IDS


@dataclass(frozen=True)
class WorkflowDraftStageResult:
    staged: bool
    stage_ids: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()


def should_stage_workflow_drafts(profile_id: str | None) -> bool:
    return should_validate_workflow_drafts(profile_id)


def build_workflow_stage_prompt(
    user_request: str,
    *,
    stage_id: str,
    stage_index: int,
    total_stages: int,
) -> str:
    normalized_stage = stage_id.strip().lower().replace("_", "-")
    header = [
        f"Workflow draft staging {stage_index}/{total_stages}: {normalized_stage}.",
        "",
        "Original user request:",
        user_request.strip(),
        "",
        "Passive safety rules:",
        render_passive_safety_fragment(),
        "- Use secret references, redacted markers, or checksums for secrets; never raw secret values.",
        "",
    ]

    if normalized_stage == "intent":
        body = [
            "Return only an intent brief with these headings:",
            "- Goal",
            "- Trigger",
            "- Systems involved",
            "- Data needed",
            "- Side effects",
            "- Approval gates",
            "- Safety assumptions",
            "",
            "Identify the workflow design contract fields that the final package must preserve:",
            "intent, trigger, inputs, outputs, assumptions, side effects, approval gates, failure modes, rollback.",
            "",
            "Do not return workflow package files in this stage.",
        ]
    elif normalized_stage == "skeleton":
        body = [
            "Using the original request and the previous intent brief, return only a workflow skeleton.",
            "Include these headings:",
            "- Trigger",
            "- Step list",
            "- Decision points",
            "- Connector candidates",
            "- Permission candidates",
            "- Required dry-run fixtures",
            "- Rollback notes",
            "- Final design contract values",
            "",
            "Do not return workflow package files in this stage.",
        ]
    elif normalized_stage == "package":
        body = [
            "Using the original request, previous intent brief, and previous workflow skeleton,",
            "Return the complete workflow package draft.",
            "",
            render_required_package_blocks_fragment(),
            "",
            "Schema reminders:",
            render_schema_reminders_fragment(),
            render_capability_catalog_fragment(),
            render_side_effect_contract_fragment(),
            render_secret_reference_fragment(),
            render_rollback_safety_fragment(),
            render_workflow_passive_boundary_fragment(),
            "",
            PACKAGE_STAGE_MICRO_EXAMPLES,
        ]
    else:
        body = [
            "Return a concise workflow design note for this custom stage.",
            "Do not return workflow package files unless the stage name is package.",
        ]

    return "\n".join((*header, *body))


def run_workflow_draft_stages(
    user_request: str,
    run_turn: Callable[[str], None],
    *,
    config: WorkflowDraftStageConfig = WorkflowDraftStageConfig(),
    on_stage: Callable[[int, int, str], None] | None = None,
) -> WorkflowDraftStageResult:
    if not config.enabled:
        return WorkflowDraftStageResult(staged=False)

    stage_ids = tuple(stage_id.strip().lower().replace("_", "-") for stage_id in config.stage_ids if stage_id.strip())
    if not stage_ids:
        return WorkflowDraftStageResult(staged=False)

    prompts: list[str] = []
    total = len(stage_ids)
    for index, stage_id in enumerate(stage_ids, start=1):
        prompt = build_workflow_stage_prompt(
            user_request,
            stage_id=stage_id,
            stage_index=index,
            total_stages=total,
        )
        prompts.append(prompt)
        if on_stage is not None:
            on_stage(index, total, stage_id)
        run_turn(prompt)

    return WorkflowDraftStageResult(
        staged=True,
        stage_ids=stage_ids,
        prompts=tuple(prompts),
    )
