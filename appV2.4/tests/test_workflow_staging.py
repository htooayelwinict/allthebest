from __future__ import annotations

from appv24.workflows.prompt_fragments import (
    render_capability_catalog_fragment,
    render_required_package_blocks_fragment,
    render_schema_reminders_fragment,
)
from appv24.workflows.capability_catalog import WorkflowCapability, WorkflowCapabilityCatalog
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

    assert "Use only these exact labeled fenced sections:" in fragment
    assert "```yaml workflow.yaml" in fragment
    assert "```yaml connectors.yaml" in fragment
    assert "```yaml permissions.yaml" in fragment
    assert "```yaml dry_run_fixtures/happy_path.yaml" in fragment
    assert "```markdown rollback.md" in fragment
    assert "dry_run_fixtures/rejection_path.yaml" not in fragment


def test_prompt_fragments_render_schema_reminders() -> None:
    fragment = render_schema_reminders_fragment()

    assert "workflow.yaml version must be appv24.workflow/v1" in fragment
    assert "connectors.yaml connectors must be a list" in fragment
    assert "permissions.yaml permissions must be a list" in fragment
    assert "permissions.yaml approvals[].required_for must be workflow step id strings" in fragment
    assert "permissions[].scopes must not include scopes beyond connector-owned required_scopes" in fragment
    assert "Non-terminal action and manual approval steps must set next:" in fragment
    assert "Decision steps must use branches:" in fragment
    assert "Do not use condition plus next/reject_next as runtime branch syntax" in fragment
    assert "dry_run_fixtures/*.yaml must include decision:" in fragment
    assert "adapter must be one of mcp, openapi, asyncapi, webhook, direct_api, iac, llm, manual" in fragment


def test_prompt_fragments_render_builtin_capability_catalog() -> None:
    fragment = render_capability_catalog_fragment()

    assert "Built-in executable workflow capability catalog:" in fragment
    assert "github.create_issue" in fragment
    assert "source.fetch_items" in fragment
    assert "llm.extract_structured" in fragment
    assert "policy.check" in fragment
    assert "notification.create_draft" in fragment
    assert "telegram.send_message" in fragment
    assert "ticket.create_draft" in fragment
    assert "Incoming events, webhooks, and fixture inputs are triggers or input data" in fragment
    assert "Do not invent connector action names" in fragment
    assert "prefer compact with.output_contract over inline with.output_schema" in fragment
    assert "event_alerts_v1" in fragment
    assert "Do not use llm.do_anything, agent.run, http.request, finance.*, trading.*" in fragment
    assert "Financial catalyst workflows" not in fragment
    assert "informational alerts only" not in fragment
    assert "policy.check steps must use deterministic with.checks" in fragment
    assert "End users describe workflows in plain language" in fragment
    assert "If no built-in or active extension catalog action matches" in fragment
    assert "generated_by: coding_profile" in fragment
    assert "qa_required: true" in fragment
    assert "Do not embed appv24.extension_qa/v1 returned QA evidence" in fragment
    assert "Do not use execution_mode: design_only for an action used by a workflow step" in fragment
    assert "If no catalog action matches, model the need as trigger metadata" not in fragment


def test_package_stage_prompt_routes_plain_missing_connector_request_to_coder_addon() -> None:
    prompt = build_workflow_stage_prompt(
        "Look up each sender in our CRM before sending the digest.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert "Original user request:" in prompt
    assert "Look up each sender in our CRM before sending the digest." in prompt
    assert "Do not ask the end user to provide connector schema" in prompt
    assert "connector-owned extension interface" in prompt
    assert "workflow_hints" in prompt
    assert "The app writes the coder-profile PRD and dispatch handoff after validation" in prompt
    assert "Do not embed appv24.extension_qa/v1 returned QA evidence" in prompt
    assert "If no catalog action matches, model the need as trigger metadata" not in prompt


def test_build_package_stage_prompt_includes_active_extension_catalog_context() -> None:
    catalog = WorkflowCapabilityCatalog(
        capabilities=(
            WorkflowCapability(
                group="Read data",
                label="Lookup CRM contact",
                action_ref="crm.lookup_contact",
                input_fields=("email",),
                required_input_fields=("email",),
                required_input_any_groups=(),
                output_fields=("contact", "artifact_path"),
                mode_labels=("local", "live-read"),
                side_effect_class="read",
                approval_required=False,
                required_env_names=("APPV24_CRM_MCP_SERVER",),
            ),
        ),
        package_authoring_context=(
            "Reusable extension package declarations\n"
            "connectors.yaml entries to copy:\n"
            "connectors:\n"
            "- id: crm\n"
            "  actions:\n"
            "  - id: lookup_contact\n"
            "    custom: true\n"
            "    source: mcp://crm.lookup_contact@v1\n"
            "    qa:\n"
            "      qa:\n"
            "        adapter_contract: passed"
        ),
    )

    prompt = build_workflow_stage_prompt(
        "Build a CRM lookup workflow.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
        capability_catalog=catalog,
    )

    assert "Active reusable workflow extension catalog:" in prompt
    assert "Lookup CRM contact (crm.lookup_contact)" in prompt
    assert "required inputs: email" in prompt
    assert "outputs: contact, artifact_path" in prompt
    assert "env: APPV24_CRM_MCP_SERVER" in prompt
    assert "Reusable extension package declarations" in prompt
    assert "connectors.yaml entries to copy:" in prompt
    assert "custom: true" in prompt
    assert "source: mcp://crm.lookup_contact@v1" in prompt
    assert "adapter_contract: passed" in prompt


def test_build_intent_stage_prompt_omits_extension_package_authoring_context() -> None:
    catalog = WorkflowCapabilityCatalog(
        capabilities=(
            WorkflowCapability(
                group="Read data",
                label="Lookup CRM contact",
                action_ref="crm.lookup_contact",
                input_fields=("email",),
                required_input_fields=("email",),
                required_input_any_groups=(),
                output_fields=("contact",),
                mode_labels=("live-read",),
                side_effect_class="read",
                approval_required=False,
                required_env_names=(),
            ),
        ),
        package_authoring_context="connectors.yaml entries to copy:\nconnectors: []",
    )

    prompt = build_workflow_stage_prompt(
        "Build a CRM lookup workflow.",
        stage_id="intent",
        stage_index=1,
        total_stages=3,
        capability_catalog=catalog,
    )

    assert "Lookup CRM contact (crm.lookup_contact)" in prompt
    assert "connectors.yaml entries to copy" not in prompt


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
    assert "```markdown rollback.md" in prompt
    assert "dry_run_fixtures/rejection_path.yaml" not in render_required_package_blocks_fragment()
    assert "rollback.md must be manual, non-executable" in prompt
    assert "Do not include API, CLI, shell, curl, Terraform, kubectl, cloud CLI, or token examples in rollback.md" in prompt
    assert "Brokered mutating actions require approval_required: true" in prompt
    assert "Design-only actions must use execution_mode: design_only and must not be called by workflow steps" in prompt
    assert "Read, classify, analyze, summarize, draft, format, and local artifact steps must use side_effect: false" in prompt
    assert "Only external create, update, delete, send, publish, or provision actions should be mutating" in prompt
    assert "prefer compact with.output_contract over inline with.output_schema" in prompt
    assert "Financial catalyst workflows must be informational alerts only" not in prompt
    assert "buy/sell/hold recommendations" not in prompt
    assert "outputs.allowed" in prompt
    assert "workflow.yaml must include design metadata" in prompt
    assert "appv24 only designs workflow packages; it does not execute workflows" in prompt
    assert render_capability_catalog_fragment() in prompt


def test_package_stage_prompt_includes_shared_fragments() -> None:
    prompt = build_workflow_stage_prompt(
        "design a workflow",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert render_required_package_blocks_fragment() in prompt
    assert render_schema_reminders_fragment(initial_package_generation=True) in prompt
    assert render_capability_catalog_fragment() in prompt


def test_package_stage_prompt_pins_ai_news_telegram_minimal_reference_contract() -> None:
    prompt = build_workflow_stage_prompt(
        "Scrape AI news and send from telegram bot to registered users.",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert 'appv24 workflow references must be quoted double-brace strings without a dollar sign' in prompt
    assert '"{{ steps.search_ai_news.outputs.items }}"' in prompt
    assert 'recipients: "{{ steps.list_subscribers.outputs.recipients }}"' in prompt
    assert 'audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}"' in prompt
    assert 'text: "{{ steps.summarize_news.outputs.summary }}"' in prompt
    assert 'never use GitHub-style "${{ ... }}"' in prompt
    assert "dry_run_fixtures/rejection_path.yaml" not in render_required_package_blocks_fragment()
    assert "Include only workflow.yaml, connectors.yaml, permissions.yaml, dry_run_fixtures/happy_path.yaml, and rollback.md." in prompt
    assert "Do not include GitHub-style ${{ }} references anywhere." in prompt
    assert "Add extra dry_run_fixtures/*.yaml only when" not in prompt
    assert "middlewares.yaml" not in prompt


def test_package_stage_prompt_mentions_terminal_traversal_contract() -> None:
    prompt = build_workflow_stage_prompt(
        "design a workflow",
        stage_id="package",
        stage_index=3,
        total_stages=3,
    )

    assert "Every non-terminal action step must include next" in prompt
    assert "every decision step must include branches" in prompt
    assert "every dry-run fixture must traverse from entrypoint to a terminal step" in prompt


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
    assert "Missing connector coder-addon shape:" in prompt
    assert "uses: crm.lookup_contact" in prompt
    assert "source: mcp://crm.lookup_contact@v1" in prompt
    assert "generated_by: coding_profile" in prompt
    assert "qa_required: true" in prompt


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
