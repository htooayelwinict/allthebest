"""Bounded workflow draft staging helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from appv24.workflows.live_validation import should_validate_workflow_drafts


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
        "- Do not call tools.",
        "- Do not call external APIs.",
        "- Do not run shell commands.",
        "- Do not write files.",
        "- Do not request raw secrets.",
        "- Do not execute, publish, provision, send, or mutate anything.",
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
            "",
            "Do not return workflow package files in this stage.",
        ]
    elif normalized_stage == "package":
        body = [
            "Using the original request, previous intent brief, and previous workflow skeleton,",
            "Return the complete workflow package draft.",
            "",
            "Use these exact labeled fenced sections:",
            "- ```yaml workflow.yaml",
            "- ```yaml connectors.yaml",
            "- ```yaml permissions.yaml",
            "- ```yaml dry_run_fixtures/happy_path.yaml",
            "- ```yaml dry_run_fixtures/rejection_path.yaml",
            "- ```markdown rollback.md",
            "",
            "Schema reminders:",
            "- workflow.yaml version must be appv24.workflow/v1.",
            "- workflow.yaml triggers must be a list.",
            "- Cron-like triggers must use type: schedule and schedule: <cron>.",
            "- workflow.yaml steps must use approval: <approval-id> for mutating side effects.",
            "- connectors.yaml version must be appv24.connectors/v1.",
            "- connectors.yaml connectors must be a list.",
            "- connectors.yaml actions must be objects with id and side_effect boolean.",
            "- Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false unless they mutate external systems.",
            "- Only external create, update, delete, send, publish, or provision actions should be mutating.",
            "- Mutating connector actions must include side_effect_class, execution_mode, approval_required, and dry_run_supported.",
            "- Brokered mutating actions require approval_required: true and dry_run_supported: true.",
            "- Brokered create/send/publish/provision actions require idempotency.key_fields.",
            "- Brokered mutating actions require outputs.allowed with explicit field names and no wildcard.",
            "- Design-only actions must use execution_mode: design_only and must not be called by workflow steps.",
            "- Draft-only outputs must be represented as local artifact drafts, not connector send/publish calls.",
            "- Secret examples must use reference names such as secretRef: CONNECTOR_CREDENTIAL_REF, checksum fields, redacted: true, or non-secret references.",
            "- secretRef values must be reference names only, never raw tokens, passwords, keys, or credentials.",
            "- permissions.yaml version must be appv24.permissions/v1.",
            "- permissions.yaml permissions must be a list.",
            "- permissions.yaml permissions[].actions must be connector action id strings.",
            "- permissions.yaml approvals[].required_for must be workflow step id strings.",
            "- Every mutating step must have an explicit approval gate.",
            "- rollback.md must be manual, non-executable rollback guidance.",
            "- Do not include API, CLI, shell, curl, Terraform, kubectl, cloud CLI, or token examples in rollback.md.",
            "- rollback.md must not include ${secrets.*}, Authorization headers, HTTP method examples, or copy-paste commands.",
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
