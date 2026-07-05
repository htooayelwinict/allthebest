from __future__ import annotations

from appv24.workflows.prompt_fragments import (
    render_required_package_blocks_fragment,
    render_schema_reminders_fragment,
)
from appv24.workflows.staging import (
    WorkflowDraftStageConfig,
    build_workflow_stage_prompt,
    run_workflow_draft_stages,
    should_stage_workflow_drafts,
)


def test_should_stage_only_workflow_designer_profile() -> None:
    assert should_stage_workflow_drafts("workflow-designer-lite")
    assert should_stage_workflow_drafts("WORKFLOW_DESIGNER_LITE")
    assert not should_stage_workflow_drafts("coding")
    assert not should_stage_workflow_drafts(None)


def test_prompt_fragments_render_required_package_blocks() -> None:
    fragment = render_required_package_blocks_fragment()

    assert "```yaml workflow.yaml" in fragment
    assert "```yaml connectors.yaml" in fragment
    assert "```yaml permissions.yaml" in fragment
    assert "```yaml dry_run_fixtures/happy_path.yaml" in fragment
    assert "```yaml dry_run_fixtures/rejection_path.yaml" in fragment
    assert "```markdown rollback.md" in fragment


def test_prompt_fragments_render_schema_reminders() -> None:
    fragment = render_schema_reminders_fragment()

    assert "workflow.yaml version must be appv24.workflow/v1" in fragment
    assert "connectors.yaml connectors must be a list" in fragment
    assert "permissions.yaml permissions must be a list" in fragment
    assert "permissions.yaml approvals[].required_for must be workflow step id strings" in fragment
    assert "Non-terminal action and manual approval steps must set next:" in fragment
    assert "Decision steps must use branches:" in fragment
    assert "Do not use condition plus next/reject_next as runtime branch syntax" in fragment
    assert "dry_run_fixtures/*.yaml must include decision:" in fragment


def test_build_intent_stage_prompt_is_passive_and_preserves_request() -> None:
    prompt = build_workflow_stage_prompt(
        "Every weekday, check GitHub alerts and draft a Telegram summary.",
        stage_id="intent",
        stage_index=1,
        total_stages=3,
    )

    assert "Workflow draft staging 1/3: intent" in prompt
    assert "Every weekday, check GitHub alerts and draft a Telegram summary." in prompt
    assert "Do not call tools" in prompt
    assert "Do not call external APIs" in prompt
    assert "Do not write files" in prompt
    assert "Return only an intent brief" in prompt
    assert "Identify the workflow design contract fields" in prompt
    assert "intent, trigger, inputs, outputs, assumptions, side effects, approval gates, failure modes, rollback" in prompt
    assert "workflow.yaml" not in prompt


def test_build_package_stage_prompt_requires_full_package_sections() -> None:
    prompt = build_workflow_stage_prompt(
        "Create a human-approved dependency triage workflow.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert "Workflow draft staging 3/3: package" in prompt
    assert "Return the complete workflow package draft" in prompt
    assert "```yaml workflow.yaml" in prompt
    assert "```yaml connectors.yaml" in prompt
    assert "```yaml permissions.yaml" in prompt
    assert "```yaml dry_run_fixtures/happy_path.yaml" in prompt
    assert "```yaml dry_run_fixtures/rejection_path.yaml" in prompt
    assert "```markdown rollback.md" in prompt
    assert "rollback.md must be manual, non-executable" in prompt
    assert "Do not include API, CLI, shell, curl, Terraform, kubectl, cloud CLI, or token examples in rollback.md" in prompt
    assert "Brokered mutating actions require approval_required: true" in prompt
    assert "Design-only actions must use execution_mode: design_only and must not be called by workflow steps" in prompt
    assert "Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false" in prompt
    assert "Only external create, update, delete, send, publish, or provision actions should be mutating" in prompt
    assert "outputs.allowed" in prompt
    assert "workflow.yaml must include design metadata" in prompt
    assert "appv24 only designs workflow packages; it does not execute workflows" in prompt


def test_package_stage_prompt_includes_shared_fragments() -> None:
    prompt = build_workflow_stage_prompt(
        "design a workflow",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert render_required_package_blocks_fragment() in prompt
    assert render_schema_reminders_fragment() in prompt


def test_build_package_stage_prompt_includes_schema_micro_examples() -> None:
    prompt = build_workflow_stage_prompt(
        "Create a human-approved dependency triage workflow.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert "Micro examples for exact shape only:" in prompt
    assert "design:" in prompt
    assert "side_effects:" in prompt
    assert "- id: create_ticket" in prompt
    assert "side_effect_class: create" in prompt
    assert "approval: approve_create_ticket" in prompt
    assert "next: done" in prompt
    assert "execution_mode: brokered" in prompt
    assert "approval_required: true" in prompt
    assert "dry_run_supported: true" in prompt
    assert "outputs:" in prompt
    assert "allowed:" in prompt
    assert "required_for: [create_ticket]" in prompt
    assert "branches:" in prompt
    assert "decision:" in prompt
    assert "decide_path: approved" in prompt
    assert 'api_token: "secretRef: API_TOKEN_REF"' in prompt
    assert "Manual rollback prose only" in prompt


def test_run_workflow_draft_stages_runs_default_stages_in_order() -> None:
    prompts: list[str] = []
    events: list[tuple[int, int, str]] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)

    result = run_workflow_draft_stages(
        "Build a weekly dependency alert workflow.",
        run_turn,
        on_stage=lambda index, total, stage_id: events.append((index, total, stage_id)),
    )

    assert result.staged
    assert result.stage_ids == ("intent", "skeleton", "package")
    assert result.prompts == tuple(prompts)
    assert events == [(1, 3, "intent"), (2, 3, "skeleton"), (3, 3, "package")]
    assert "intent brief" in prompts[0]
    assert "workflow skeleton" in prompts[1]
    assert "complete workflow package draft" in prompts[2]


def test_run_workflow_draft_stages_noops_when_disabled() -> None:
    prompts: list[str] = []

    result = run_workflow_draft_stages(
        "Build a workflow.",
        lambda prompt: prompts.append(prompt),
        config=WorkflowDraftStageConfig(enabled=False),
    )

    assert not result.staged
    assert result.stage_ids == ()
    assert result.prompts == ()
    assert prompts == []
