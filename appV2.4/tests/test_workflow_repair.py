from __future__ import annotations

from types import SimpleNamespace

from appv24.workflows.prompt_fragments import (
    render_passive_safety_fragment,
    render_secret_reference_fragment,
    render_side_effect_contract_fragment,
)
from appv24.workflows.repair import (
    WorkflowDraftRepairConfig,
    build_workflow_repair_prompt,
    compact_workflow_validation_issues,
    repair_workflow_draft_messages,
    validate_latest_workflow_draft,
)
from appv24.workflows.validator import validate_workflow_draft_text


def _assistant_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(role="assistant", content=[SimpleNamespace(type="text", text=text)])


def test_prompt_fragments_render_repair_safety_rules() -> None:
    safety = render_passive_safety_fragment()
    side_effects = render_side_effect_contract_fragment()
    secrets = render_secret_reference_fragment()

    assert "Do not call tools" in safety
    assert "Do not run shell commands" in safety
    assert "Brokered mutating actions require approval_required: true" in side_effects
    assert "Design-only actions must use execution_mode: design_only" in side_effects
    assert "secretRef values must be reference names only" in secrets


def _valid_draft() -> str:
    return """
Here is the workflow package draft.

```yaml workflow.yaml
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
design:
  intent: Review dependency alerts and draft a human-approved issue.
  trigger: Weekly scheduled dependency alert review.
  inputs:
    - GitHub dependency alert list.
  outputs:
    - Human-reviewed issue draft.
  assumptions:
    - GitHub credentials are provided by secret reference at execution time.
  side_effects:
    - Create a GitHub issue only after explicit human approval.
  approval_gates:
    - human-review
  failure_modes:
    - GitHub API unavailable.
    - Human rejects the issue draft.
  rollback:
    - Close the drafted issue manually if it was created.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
entrypoint: inspect_alerts
steps:
  - id: inspect_alerts
    uses: github.list_dependabot_alerts
    next: draft_issue
  - id: draft_issue
    uses: github.create_issue
    side_effect_class: create
    approval: human-review
    next: done
  - id: done
    type: terminal
```

```yaml connectors.yaml
version: appv24.connectors/v1
connectors:
  - id: github
    adapter: openapi
    description: GitHub REST API.
    actions:
      - id: list_dependabot_alerts
        operationId: dependabot_list_alerts
        side_effect: false
      - id: create_issue
        operationId: issues_create
        side_effect: true
        side_effect_class: create
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - repo
            - alert_id
        outputs:
          allowed:
            - issue_number
            - issue_url
        required_scopes:
          - issues:write
```

```yaml permissions.yaml
version: appv24.permissions/v1
approvals:
  - id: human-review
    mode: explicit
    required_for:
      - draft_issue
permissions:
  - connector: github
    actions:
      - list_dependabot_alerts
      - create_issue
    scopes:
      - dependabot:read
      - issues:write
```

```yaml dry_run_fixtures/happy_path.yaml
input:
  repo: owner/project
```

```yaml dry_run_fixtures/rejection_path.yaml
approval:
  human-review: rejected
```

```markdown rollback.md
# Rollback

Close the drafted issue if it was created.
```
"""


def _invalid_draft() -> str:
    return _valid_draft().replace("side_effect: true", "side_effect: send")


def _missing_next_transition_draft() -> str:
    return _valid_draft().replace("    next: done\n  - id: done", "  - id: done", 1)


def _missing_workflow_step_side_effect_class_draft() -> str:
    return _valid_draft().replace(
        "    side_effect_class: create\n    approval: human-review",
        "    approval: human-review",
        1,
    )


def _read_action_marked_mutating_without_approval_draft() -> str:
    return _valid_draft().replace(
        "        operationId: dependabot_list_alerts\n        side_effect: false",
        "        operationId: dependabot_list_alerts\n        side_effect: true",
        1,
    )


def _raw_secret_fixture_draft() -> str:
    return _valid_draft().replace(
        "input:\n  repo: owner/project",
        "input:\n  repo: owner/project\n  context:\n    api_token: old-secret-value",
    )


def _design_only_action_used_draft() -> str:
    return _valid_draft().replace(
        "        execution_mode: brokered",
        "        execution_mode: design_only",
        1,
    )


def _unsafe_rollback_draft() -> str:
    return _valid_draft().replace(
        "# Rollback\n\nClose the drafted issue if it was created.",
        "# Rollback\n\n```bash\nkubectl delete deployment checkout\n```",
    )


def _missing_broker_contract_draft() -> str:
    return _valid_draft().replace(
        "        idempotency:\n          key_fields:\n            - repo\n            - alert_id\n",
        "",
        1,
    )


def _schema_drift_draft() -> str:
    return (
        _valid_draft()
        .replace("    type: schedule", "    type: signal", 1)
        .replace("    uses: github.list_dependabot_alerts", "    uses: list_dependabot_alerts", 1)
        .replace("        execution_mode: brokered", "        execution_mode: sync", 1)
    )


def test_validate_latest_workflow_draft_returns_none_without_assistant_text() -> None:
    assert validate_latest_workflow_draft([]) is None


def test_compact_workflow_validation_issues_caps_issue_count() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    rendered = compact_workflow_validation_issues(result, max_issues=1)

    assert rendered.startswith("- ")
    assert "connectors.yaml" in rendered
    assert "side_effect" in rendered
    assert len([line for line in rendered.splitlines() if "side_effect" in line]) == 1


def test_compact_workflow_validation_issues_includes_expected_shape_and_repair_hint() -> None:
    result = validate_workflow_draft_text(_valid_draft().replace("triggers:", "triggers_broken:", 1))

    rendered = compact_workflow_validation_issues(result, max_issues=4)

    assert "schema_required workflow.yaml:triggers" in rendered
    assert "Expected: required property" in rendered
    assert "Repair: Add workflow.yaml triggers." in rendered


def test_build_workflow_repair_prompt_is_bounded_and_schema_specific() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair attempt 1/2" in prompt
    assert "latest workflow draft failed appv24 workflow validation" in prompt
    assert "Return a complete corrected workflow package draft" in prompt
    assert "workflow.yaml" in prompt
    assert "connectors.yaml" in prompt
    assert "permissions.yaml" in prompt
    assert "dry_run_fixtures/happy_path.yaml" in prompt
    assert "dry_run_fixtures/rejection_path.yaml" in prompt
    assert "rollback.md" in prompt
    assert "Do not call tools" in prompt
    assert "side_effect must be boolean true or false" in prompt
    assert "approval: <approval-id>; never use approval_gate:" in prompt
    assert "permissions[].actions must be connector action id strings" in prompt
    assert "permissions[].scopes must grant every required_scopes value" in prompt
    assert "approvals[].required_for must include the exact mutating workflow step id" in prompt
    assert "Telegram" not in prompt


def test_build_workflow_repair_prompt_scopes_the_fix_to_failed_paths() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=4)

    assert "Scoped repair contract:" in prompt
    assert "Failed edit scope:" in prompt
    assert "- connectors.yaml:connectors.0.actions.1.side_effect" in prompt
    assert "Use the latest workflow package draft already in this conversation as the source document." in prompt
    assert "Edit only the files and paths listed under Failed edit scope and Repair directives." in prompt
    assert "Preserve every unrelated file, field, id, name, step order, approval, fixture, and rollback line exactly." in prompt
    assert "Return the full package as a patched copy for validation; do not output a diff." in prompt
    assert "Output the full package again, not a patch." not in prompt


def test_repair_prompt_includes_shared_contract_fragments() -> None:
    result = validate_workflow_draft_text(_invalid_draft())
    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None)

    assert render_side_effect_contract_fragment() in prompt
    assert render_secret_reference_fragment() in prompt


def test_repeated_repair_prompt_tells_model_to_revert_unrelated_drift() -> None:
    result = validate_workflow_draft_text(_schema_drift_draft())

    prompt = build_workflow_repair_prompt(
        result,
        attempt=3,
        max_attempts=None,
        max_issues=8,
        repeated_validation_count=2,
    )

    assert "The latest repair returned the same validation failure pattern as an earlier attempt." in prompt
    assert "Do not redesign the package." in prompt
    assert "If your last repair changed unrelated sections, revert those unrelated changes" in prompt
    assert "then fix only the failed paths below." in prompt


def test_build_workflow_repair_prompt_uses_convergence_wording_without_attempt_cap() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=4)

    assert "Repair attempt 1." in prompt
    assert "Repair attempt 1/" not in prompt
    assert "This repair loop continues until the workflow validates" in prompt


def test_build_workflow_repair_prompt_includes_step_side_effect_class_hint() -> None:
    result = validate_workflow_draft_text(_missing_workflow_step_side_effect_class_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair directives:" in prompt
    assert "workflow.yaml step draft_issue uses github.create_issue" in prompt
    assert "Add side_effect_class: create to workflow.yaml step draft_issue" in prompt


def test_build_workflow_repair_prompt_distinguishes_non_mutating_action_metadata() -> None:
    result = validate_workflow_draft_text(_read_action_marked_mutating_without_approval_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_approval workflow.yaml:steps.inspect_alerts" in prompt
    assert (
        "If the step only reads, classifies, analyzes, summarizes, drafts, formats, or writes local artifacts, "
        "set the connector action side_effect: false instead of adding approval."
    ) in prompt
    assert (
        "If the step truly creates, updates, deletes, sends, publishes, or provisions externally, "
        "keep it mutating and add an explicit approval gate."
    ) in prompt


def test_build_workflow_repair_prompt_includes_raw_secret_directive() -> None:
    result = validate_workflow_draft_text(_raw_secret_fixture_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair directives:" in prompt
    assert "raw_secret dry_run_fixtures/happy_path.yaml:input.context.api_token" in prompt
    assert "Replace the inline secret value at this exact path" in prompt
    assert "Use a secret reference field, checksum, redacted marker, or non-secret reference" in prompt
    assert 'Use normal input keys with scalar values like github_token: "secretRef: GITHUB_TOKEN_REF"' in prompt
    assert "Do not invent keys like github_token_secretRef: GITHUB_TOKEN" in prompt
    assert "Do not preserve or restate the inline secret value" in prompt
    assert "old-secret-value" not in prompt


def test_build_workflow_repair_prompt_includes_design_only_directive() -> None:
    result = validate_workflow_draft_text(_design_only_action_used_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair directives:" in prompt
    assert "design_only_action_used workflow.yaml:steps.draft_issue" in prompt
    assert "Remove the workflow step call to design-only action github.create_issue" in prompt
    assert "keep the intended output as a non-executing local artifact draft instead" in prompt


def test_build_workflow_repair_prompt_includes_broker_contract_directive() -> None:
    result = validate_workflow_draft_text(_missing_broker_contract_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair directives:" in prompt
    assert "missing_side_effect_contract connectors.yaml" in prompt
    assert "github.create_issue requires idempotency.key_fields" in prompt
    assert "Add the missing broker side-effect metadata to that connector action" in prompt


def test_build_workflow_repair_prompt_includes_targeted_micro_examples() -> None:
    result = validate_workflow_draft_text(_missing_broker_contract_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Targeted micro examples:" in prompt
    assert "Mutating brokered action shape:" in prompt
    assert "execution_mode: brokered" in prompt
    assert "approval_required: true" in prompt
    assert "dry_run_supported: true" in prompt
    assert "idempotency:" in prompt
    assert "key_fields:" in prompt
    assert "outputs:" in prompt
    assert "allowed:" in prompt
    assert "Approval wiring shape:" in prompt
    assert "approval: approve_create_ticket" in prompt
    assert "required_for: [create_ticket]" in prompt
    assert "Stripe dispute" not in prompt


def test_build_workflow_repair_prompt_includes_design_contract_micro_example() -> None:
    result = validate_workflow_draft_text(_valid_draft().replace("design:", "design_missing:", 1))

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Targeted micro examples:" in prompt
    assert "Design contract shape:" in prompt
    assert "intent: Describe the passive workflow goal." in prompt
    assert "side_effects:" in prompt
    assert "approval_gates:" in prompt
    assert "failure_modes:" in prompt
    assert "rollback:" in prompt


def test_build_workflow_repair_prompt_includes_unsafe_rollback_directive() -> None:
    result = validate_workflow_draft_text(_unsafe_rollback_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "Repair directives:" in prompt
    assert "unsafe_rollback rollback.md" in prompt
    assert "Rewrite rollback.md as manual prose only" in prompt
    assert "Remove command examples, shell snippets, API examples, auth headers, and destructive verbs" in prompt


def test_build_workflow_repair_prompt_includes_schema_drift_directives() -> None:
    result = validate_workflow_draft_text(_schema_drift_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "Repair directives:" in prompt
    assert "workflow.yaml trigger type must be one of: schedule, webhook, manual, event" in prompt
    assert "For event-like or signal-like triggers, use type: event" in prompt
    assert "workflow.yaml steps[].uses must be connector.action" in prompt
    assert "Do not use bare action ids such as list_dependabot_alerts" in prompt
    assert "connectors.yaml actions[].execution_mode must be brokered, manual, or design_only" in prompt
    assert "Replace sync, async, automatic, or auto with brokered for real external mutations" in prompt


def test_build_workflow_repair_prompt_includes_missing_transition_directive() -> None:
    result = validate_workflow_draft_text(_missing_next_transition_draft())
    assert result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "quality gate" in prompt
    assert "missing_transition workflow.yaml:steps.draft_issue.next" in prompt
    assert "Add the smallest correct next transition" in prompt


def test_repair_noops_when_latest_draft_is_valid() -> None:
    messages = [_assistant_message(_valid_draft())]

    def fail_run_turn(prompt: str) -> None:
        raise AssertionError(f"repair turn should not run: {prompt}")

    result = repair_workflow_draft_messages(messages, fail_run_turn)

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 0
    assert not result.repaired
    assert not result.exhausted


def test_repair_runs_when_latest_draft_fails_quality_gate() -> None:
    messages = [_assistant_message(_missing_next_transition_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=8),
    )

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 1
    assert result.repaired
    assert not result.exhausted
    assert "missing_transition" in prompts[0]


def test_repair_runs_once_when_second_draft_is_valid() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=6),
    )

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 1
    assert result.repaired
    assert not result.exhausted
    assert len(prompts) == 1
    assert prompts == list(result.feedback_prompts)


def test_repair_stops_after_max_attempts_when_invalid_persists() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_invalid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=3),
    )

    assert result.validation is not None
    assert not result.validation.ok
    assert result.attempts == 2
    assert not result.repaired
    assert result.exhausted
    assert len(prompts) == 2


def test_repair_config_stops_at_finite_attempt_bound() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    result = repair_workflow_draft_messages(
        messages,
        lambda prompt: prompts.append(prompt),
        config=WorkflowDraftRepairConfig(max_attempts=1, max_issues=8),
    )

    assert result.attempts == 1
    assert result.exhausted is True
    assert result.stop_reason == "max_attempts"
    assert len(prompts) == 1


def test_repair_config_unbounded_attempts_can_converge() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=None, max_issues=8),
    )

    assert result.attempts == 1
    assert result.repaired is True
    assert result.validation is not None
    assert result.validation.ok is True


def test_repair_stops_when_repair_turn_aborts_with_partial_draft() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(
            SimpleNamespace(
                role="assistant",
                stop_reason="aborted",
                error_message="Operation aborted",
                content=[
                    SimpleNamespace(
                        type="text",
                        text="```yaml workflow.yaml\nversion: appv24.workflow/v1\nname: partial\n```",
                    )
                ],
            )
        )

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=3, max_repeated_validation_results=99),
    )

    assert result.validation is not None
    assert not result.repaired
    assert result.exhausted
    assert result.attempts == 1
    assert result.stop_reason == "aborted"
    assert len(prompts) == 1


def test_repair_converges_after_more_than_two_attempts_by_default() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []
    attempt_events: list[tuple[int, int | None]] = []
    drafts = iter(
        [
            _schema_drift_draft(),
            _read_action_marked_mutating_without_approval_draft(),
            _missing_broker_contract_draft(),
            _valid_draft(),
        ]
    )

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(next(drafts)))

    def on_attempt(attempt: int, max_attempts: int | None, _validation) -> None:
        attempt_events.append((attempt, max_attempts))

    result = repair_workflow_draft_messages(messages, run_turn, on_attempt=on_attempt)

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 4
    assert result.repaired
    assert not result.exhausted
    assert result.stop_reason is None
    assert len(prompts) == 4
    assert attempt_events == [(1, None), (2, None), (3, None), (4, None)]
    assert "Repair attempt 1." in prompts[0]
    assert "Repair attempt 1/" not in prompts[0]


def test_repair_stops_when_validation_failure_repeats_without_progress() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(_invalid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_repeated_validation_results=2),
    )

    assert result.validation is not None
    assert not result.validation.ok
    assert result.attempts == 1
    assert not result.repaired
    assert result.exhausted
    assert result.stop_reason == "repeated_validation"
    assert len(prompts) == 1


def test_repair_loops_with_latest_validation_issues_until_valid() -> None:
    messages = [_assistant_message(_invalid_draft())]
    prompts: list[str] = []
    drafts = iter([_schema_drift_draft(), _valid_draft()])

    def run_turn(prompt: str) -> None:
        prompts.append(prompt)
        messages.append(_assistant_message(next(drafts)))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=3, max_issues=8),
    )

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 2
    assert result.repaired
    assert not result.exhausted
    assert len(prompts) == 2
    assert "side_effect must be boolean true or false" in prompts[0]
    assert "Replace sync, async, automatic, or auto with brokered for real external mutations" in prompts[1]
