from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from appv24.workflows.prompt_fragments import (
    render_capability_catalog_fragment,
    render_passive_safety_fragment,
    render_secret_reference_fragment,
    render_schema_reminders_fragment,
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
    schema = render_schema_reminders_fragment()

    assert "Do not call tools" in safety
    assert "Do not run shell commands" in safety
    assert "Brokered mutating actions require approval_required: true" in side_effects
    assert "Design-only actions must use execution_mode: design_only" in side_effects
    assert "secretRef values must be reference names only" in secrets
    assert (
        "workflow.yaml top-level shape: version: appv24.workflow/v1, name:, "
        "description:, design:, triggers:, entrypoint:, steps:."
    ) in schema


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
        required_scopes:
          - dependabot:read
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


def _missing_design_rollback_draft() -> str:
    return _valid_draft().replace(
        "  rollback:\n    - Close the drafted issue manually if it was created.\n",
        "",
        1,
    )


def _missing_design_core_lists_draft() -> str:
    draft = _valid_draft()
    for block in (
        "  inputs:\n    - GitHub dependency alert list.\n",
        "  outputs:\n    - Human-reviewed issue draft.\n",
        "  failure_modes:\n    - GitHub API unavailable.\n    - Human rejects the issue draft.\n",
    ):
        draft = draft.replace(block, "", 1)
    return draft


def _missing_design_assumptions_side_effects_draft() -> str:
    draft = _valid_draft()
    for block in (
        "  assumptions:\n    - GitHub credentials are provided by secret reference at execution time.\n",
        "  side_effects:\n    - Create a GitHub issue only after explicit human approval.\n",
    ):
        draft = draft.replace(block, "", 1)
    return draft


def _missing_policy_checks_draft() -> str:
    return (
        _valid_draft()
        .replace(
            "    uses: github.list_dependabot_alerts\n    next: draft_issue",
            "    uses: github.list_dependabot_alerts\n    next: check_policy"
            "\n  - id: check_policy\n"
            "    uses: policy.check\n"
            "    with:\n"
            "      input_ref: inspect_alerts.alerts\n"
            "    side_effect_class: read\n"
            "    next: draft_issue",
            1,
        )
        .replace(
            "connectors:\n  - id: github",
            "connectors:\n"
            "  - id: policy\n"
            "    adapter: manual\n"
            "    description: Deterministic policy checks.\n"
            "    actions:\n"
            "      - id: check\n"
            "        side_effect: false\n"
            "        outputs:\n"
            "          allowed:\n"
            "            - approved\n"
            "            - reason\n"
            "  - id: github",
            1,
        )
        .replace(
            "permissions:\n  - connector: github",
            "permissions:\n"
            "  - connector: policy\n"
            "    actions:\n"
            "      - check\n"
            "    scopes: []\n"
            "  - connector: github",
            1,
        )
    )


def _missing_next_transition_draft() -> str:
    return _valid_draft().replace("    next: done\n  - id: done", "  - id: done", 1)


def _target_step_fixture_decision_draft() -> str:
    return (
        _valid_draft()
        .replace(
            "    next: draft_issue\n  - id: draft_issue",
            "    next: decide_path\n"
            "  - id: decide_path\n"
            "    type: decision\n"
            "    branches:\n"
            "      approved: draft_issue\n"
            "      rejected: done\n"
            "  - id: draft_issue",
            1,
        )
        .replace(
            "input:\n  repo: owner/project",
            "decisions:\n  decide_path: draft_issue",
            1,
        )
        .replace(
            "approval:\n  human-review: rejected",
            "decisions:\n  decide_path: done",
            1,
        )
    )


def _wrong_fixture_decision_key_draft() -> str:
    return (
        _valid_draft()
        .replace(
            "    next: draft_issue\n  - id: draft_issue",
            "    next: check_eligibility\n"
            "  - id: check_eligibility\n"
            "    type: decision\n"
            "    branches:\n"
            "      eligible: draft_issue\n"
            "      rejected: done\n"
            "  - id: draft_issue",
            1,
        )
        .replace(
            "input:\n  repo: owner/project",
            "decision:\n  check_path: eligible",
            1,
        )
        .replace(
            "approval:\n  human-review: rejected",
            "decision:\n  check_path: rejected",
            1,
        )
    )


def _wrong_fixture_decision_key_with_sibling_decision_draft() -> str:
    return (
        _valid_draft()
        .replace(
            "    next: draft_issue\n  - id: draft_issue",
            "    next: check_eligibility\n"
            "  - id: check_eligibility\n"
            "    type: decision\n"
            "    branches:\n"
            "      eligible: route_priority\n"
            "      rejected: done\n"
            "  - id: route_priority\n"
            "    type: decision\n"
            "    branches:\n"
            "      high: draft_issue\n"
            "      low: done\n"
            "  - id: draft_issue",
            1,
        )
        .replace(
            "input:\n  repo: owner/project",
            "decision:\n  check_path: eligible\n  route_priority: high",
            1,
        )
        .replace(
            "approval:\n  human-review: rejected",
            "decision:\n  check_path: rejected\n  route_priority: low",
            1,
        )
    )


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


def _collection_scalar_input_draft() -> str:
    return """
Here is the workflow package draft.

```yaml workflow.yaml
version: appv24.workflow/v1
name: telegram-financial-alerts
description: Draft and send approved financial alerts.
design:
  intent: Draft and send approved financial alerts.
  trigger: Scheduled scan.
  inputs:
    - Subscriber chat IDs.
  outputs:
    - Approved Telegram alert.
  assumptions:
    - Subscribers are fixture input.
  side_effects:
    - Send Telegram alert after approval.
  approval_gates:
    - approve_broadcast
  failure_modes:
    - Broadcast target is invalid.
  rollback:
    - Send a correction manually if needed.
triggers:
  - id: scheduled
    type: schedule
    schedule: "0 9 * * 1-5"
entrypoint: send_broadcast
steps:
  - id: send_broadcast
    uses: telegram.send_message
    side_effect_class: send
    approval: approve_broadcast
    with:
      chat_id: "{{ inputs.subscribers }}"
      text: Market catalyst alert.
    next: done
  - id: done
    type: terminal
```

```yaml connectors.yaml
version: appv24.connectors/v1
connectors:
  - id: telegram
    adapter: direct_api
    actions:
      - id: send_message
        side_effect: true
        side_effect_class: send
        execution_mode: brokered
        approval_required: true
        dry_run_supported: true
        idempotency:
          key_fields:
            - chat_id
            - text
        outputs:
          allowed:
            - message_id
            - chat_id
        required_scopes:
          - telegram:send
```

```yaml permissions.yaml
version: appv24.permissions/v1
permissions:
  - connector: telegram
    actions:
      - send_message
    scopes:
      - telegram:send
approvals:
  - id: approve_broadcast
    mode: explicit
    required_for:
      - send_broadcast
```

```yaml dry_run_fixtures/happy_path.yaml
inputs:
  subscribers:
    - 1000000001
approval:
  approve_broadcast: approved
```

```yaml dry_run_fixtures/rejection_path.yaml
inputs:
  subscribers:
    - 1000000001
approval:
  approve_broadcast: rejected
```

```markdown rollback.md
# Rollback

Send a correction manually if needed.
```
"""


def _schema_drift_draft() -> str:
    return (
        _valid_draft()
        .replace("    type: schedule", "    type: signal", 1)
        .replace("    uses: github.list_dependabot_alerts", "    uses: list_dependabot_alerts", 1)
        .replace("        execution_mode: brokered", "        execution_mode: sync", 1)
    )


def _unsupported_decision_syntax_draft() -> str:
    return _valid_draft().replace(
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: draft_issue",
        "entrypoint: decide_path\nsteps:\n  - id: decide_path\n    type: decision\n    condition: inspect_alerts.has_alerts == true\n    next: draft_issue\n    reject_next: done",
    )


def _missing_fixture_decision_draft() -> str:
    return _valid_draft().replace(
        "entrypoint: inspect_alerts\nsteps:\n  - id: inspect_alerts\n    uses: github.list_dependabot_alerts\n    next: draft_issue",
        "entrypoint: decide_path\nsteps:\n  - id: decide_path\n    type: decision\n    branches:\n      approved: draft_issue\n      rejected: done",
    )


def _missing_step_transition_draft() -> str:
    return _valid_draft().replace(
        "    side_effect_class: create\n    approval: human-review\n    next: done",
        "    side_effect_class: create\n    approval: human-review",
        1,
    )


def _unknown_catalog_action_draft() -> str:
    return _valid_draft().replace("list_dependabot_alerts", "invented_read")


def _package_draft(package: Path) -> str:
    sections = [
        ("yaml", "workflow.yaml", package / "workflow.yaml"),
        ("yaml", "connectors.yaml", package / "connectors.yaml"),
        ("yaml", "permissions.yaml", package / "permissions.yaml"),
        ("yaml", "dry_run_fixtures/happy_path.yaml", package / "dry_run_fixtures" / "happy_path.yaml"),
        ("yaml", "dry_run_fixtures/rejection_path.yaml", package / "dry_run_fixtures" / "rejection_path.yaml"),
        ("markdown", "rollback.md", package / "rollback.md"),
    ]
    rendered = ["Here is the workflow package draft."]
    for language, label, path in sections:
        rendered.append(f"```{language} {label}")
        rendered.append(path.read_text(encoding="utf-8").rstrip())
        rendered.append("```")
        rendered.append("")
    return "\n".join(rendered)


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
    assert render_capability_catalog_fragment() in prompt
    assert "prefer compact with.output_contract over inline with.output_schema" in prompt
    assert "Financial catalyst workflows must be informational alerts only" not in prompt
    assert "buy/sell/hold recommendations" not in prompt


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
    assert "Correct connector metadata for built-in action github.create_issue" in prompt
    assert "Keep the workflow.yaml step that uses github.create_issue; do not remove or rename it" in prompt
    assert "Set connectors.yaml github.create_issue execution_mode to brokered" in prompt
    assert "Remove the workflow step call to design-only action github.create_issue" not in prompt


def test_build_workflow_repair_prompt_repairs_builtin_llm_design_only_metadata(tmp_path: Path) -> None:
    from test_workflow_packages import write_intelligence_package

    package = tmp_path / "package"
    write_intelligence_package(package)
    draft = _package_draft(package).replace(
        "      - id: extract_structured\n"
        "        side_effect: false\n"
        "        side_effect_class: read\n"
        "        execution_mode: brokered",
        "      - id: extract_structured\n"
        "        side_effect: false\n"
        "        side_effect_class: read\n"
        "        execution_mode: design_only",
        1,
    )
    result = validate_workflow_draft_text(draft)

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "design_only_action_used workflow.yaml:steps.extract_catalysts" in prompt
    assert "Correct connector metadata for built-in action llm.extract_structured" in prompt
    assert "Keep the workflow.yaml step that uses llm.extract_structured; do not remove or rename it" in prompt
    assert "Set connectors.yaml llm.extract_structured execution_mode to brokered" in prompt
    assert "Remove the workflow step call to design-only action llm.extract_structured" not in prompt


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


def test_build_workflow_repair_prompt_includes_decision_branch_directive() -> None:
    result = validate_workflow_draft_text(_unsupported_decision_syntax_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_decision_branches workflow.yaml:steps.decide_path.branches" in prompt
    assert "unsupported_decision_syntax workflow.yaml:steps.decide_path.reject_next" in prompt
    assert "Replace the decision step runtime syntax with branches" in prompt
    assert "Remove the unsupported decision transition key" in prompt
    assert "Decision branch and fixture shape:" in prompt
    assert "branches:" in prompt
    assert "decision:" in prompt


def test_build_workflow_repair_prompt_includes_fixture_decision_directive() -> None:
    result = validate_workflow_draft_text(_missing_fixture_decision_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_fixture_decision dry_run_fixtures/happy_path.yaml:decision.decide_path" in prompt
    assert "missing_fixture_decision dry_run_fixtures/rejection_path.yaml:decision.decide_path" in prompt
    assert "Add or correct the dry-run fixture decision map entry" in prompt
    assert "Use one of the branch names from workflow.yaml steps[].branches" in prompt
    assert "Stripe dispute" not in prompt


def test_build_workflow_repair_prompt_includes_missing_transition_directive() -> None:
    result = validate_workflow_draft_text(_missing_step_transition_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_step_transition workflow.yaml:steps.draft_issue.next" in prompt
    assert "Add a runtime transition for this non-terminal workflow step" in prompt
    assert "Non-terminal action/manual steps must use next: <target-step>" in prompt
    assert "next: done" in prompt


def test_build_workflow_repair_prompt_includes_catalog_action_directive() -> None:
    result = validate_workflow_draft_text(_unknown_catalog_action_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "unknown_catalog_action workflow.yaml:steps.0.uses" in prompt
    assert "github.invented_read is not in the built-in workflow capability catalog" in prompt
    assert "Use only built-in workflow capability catalog actions" in prompt
    assert "github.list_dependabot_alerts" in prompt
    assert "telegram.send_message" in prompt
    assert "If the non-catalog action represents an incoming trigger or fixture input" in prompt


def test_build_workflow_repair_prompt_includes_llm_contract_directive(tmp_path: Path) -> None:
    from test_workflow_packages import write_intelligence_package

    package = tmp_path / "package"
    write_intelligence_package(package)
    draft = _package_draft(package).replace("      output_contract: event_alerts_v1\n", "")
    result = validate_workflow_draft_text(draft)

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_llm_output_contract workflow.yaml:steps.extract_catalysts.with" in prompt
    assert "Fix the LLM step output contract without redesigning the workflow" in prompt
    assert "Prefer compact with.output_contract names over inline with.output_schema" in prompt
    assert "event_alerts_v1" in prompt
    assert "Set exactly one of with.output_contract or with.output_schema" in prompt


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


def test_build_workflow_repair_prompt_includes_missing_design_field_directive() -> None:
    result = validate_workflow_draft_text(_missing_design_rollback_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=4)

    assert "missing_design_contract workflow.yaml:design.rollback" in prompt
    assert "Add the missing workflow.yaml design.rollback field" in prompt
    assert "workflow.yaml design must be a top-level mapping" in prompt
    assert "Put design before triggers and indent every design list item under design" in prompt
    assert "design.rollback must be a YAML list of manual rollback note strings" in prompt
    assert "Do not confuse design.rollback with rollback.md; both are required" in prompt


def test_build_workflow_repair_prompt_repairs_missing_design_lists_without_redesign() -> None:
    result = validate_workflow_draft_text(_missing_design_core_lists_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_design_contract workflow.yaml:design.inputs" in prompt
    assert "missing_design_contract workflow.yaml:design.outputs" in prompt
    assert "missing_design_contract workflow.yaml:design.failure_modes" in prompt
    assert "Add the missing workflow.yaml design.inputs field" in prompt
    assert "Add the missing workflow.yaml design.outputs field" in prompt
    assert "Add the missing workflow.yaml design.failure_modes field" in prompt
    assert "Use concise non-placeholder strings derived from the current workflow intent" in prompt
    assert "Preserve every existing workflow.yaml design field" in prompt


def test_build_workflow_repair_prompt_includes_generic_policy_check_shape() -> None:
    result = validate_workflow_draft_text(_missing_policy_checks_draft())

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=2, max_issues=8)

    assert "missing_policy_checks workflow.yaml:steps.check_policy.with.checks" in prompt
    assert "Add deterministic policy checks to the policy.check step" in prompt
    assert "Policy check shape:" in prompt
    assert "checks:" in prompt
    assert "stable_snake_case_check" in prompt
    assert "Each check key must be a stable snake_case policy name" in prompt
    assert "Each check value must be deterministic data" in prompt
    assert "source URLs, timestamps, no trading advice" not in prompt


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


def test_build_workflow_repair_prompt_includes_validation_missing_transition_directive() -> None:
    result = validate_workflow_draft_text(_missing_next_transition_draft())
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "missing_step_transition workflow.yaml:steps.draft_issue.next" in prompt
    assert "Add a runtime transition for this non-terminal workflow step" in prompt


def test_build_workflow_repair_prompt_explains_fixture_decisions_use_branch_keys() -> None:
    result = validate_workflow_draft_text(_target_step_fixture_decision_draft())
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "missing_fixture_decision" in prompt
    assert "Use top-level `decision:` in the fixture, not `decisions:`" in prompt
    assert "Use branch keys as values, not target step ids" in prompt
    assert "`decide_path: approved`" in prompt
    assert "not `decide_path: draft_issue`" in prompt


def test_build_workflow_repair_prompt_replaces_wrong_fixture_decision_key() -> None:
    result = validate_workflow_draft_text(_wrong_fixture_decision_key_draft())
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "missing_fixture_decision" in prompt
    assert "Decision mapping key must be exactly `check_eligibility`." in prompt
    assert "Replace `check_path: eligible` with `check_eligibility: eligible`." in prompt
    assert "Replace `check_path: rejected` with `check_eligibility: rejected`." in prompt
    assert "decision: {check_path:" not in prompt


def test_build_workflow_repair_prompt_preserves_sibling_fixture_decisions() -> None:
    result = validate_workflow_draft_text(_wrong_fixture_decision_key_with_sibling_decision_draft())
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "Decision mapping key must be exactly `check_eligibility`." in prompt
    assert "Replace `check_path: eligible` with `check_eligibility: eligible`." in prompt
    assert "Replace `check_path: rejected` with `check_eligibility: rejected`." in prompt
    assert "Preserve existing decision entries for other decision steps" in prompt
    assert "Replace `route_priority:" not in prompt
    assert "Remove any fixture decision keys" not in prompt


def test_build_workflow_repair_prompt_includes_connector_contract_scope_directive() -> None:
    result = validate_workflow_draft_text(
        _valid_draft().replace("        required_scopes:\n          - issues:write\n", "", 1)
    )
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "connector_contract_scope_mismatch connectors.yaml:connectors.github.actions.create_issue.required_scopes" in prompt
    assert "Update connectors.yaml metadata for github.create_issue to match the built-in connector contract" in prompt
    assert "Add required_scopes entry `issues:write`" in prompt


def test_build_workflow_repair_prompt_includes_scalar_connector_input_directive() -> None:
    result = validate_workflow_draft_text(_collection_scalar_input_draft())
    assert not result.ok

    prompt = build_workflow_repair_prompt(result, attempt=1, max_attempts=None, max_issues=8)

    assert "connector_scalar_input_collection workflow.yaml:steps.send_broadcast.with.chat_id" in prompt
    assert "telegram.send_message chat_id must resolve to a scalar value" in prompt
    assert "Do not pass arrays, maps, subscriber collections, or broadcast lists into scalar connector fields." in prompt
    assert "Until a cataloged broadcast/fanout action exists" in prompt


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


def test_repair_runs_when_latest_draft_fails_validation() -> None:
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
    assert "missing_step_transition" in prompts[0]


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


def test_repair_result_carries_attempt_report_data() -> None:
    messages = [_assistant_message(_invalid_draft())]

    def run_turn(_prompt: str) -> None:
        messages.append(_assistant_message(_valid_draft()))

    result = repair_workflow_draft_messages(
        messages,
        run_turn,
        config=WorkflowDraftRepairConfig(max_attempts=2, max_issues=6),
    )

    assert result.repair_report is not None
    assert result.repair_report.attempts
    assert result.repair_report.attempts[0].status == "failed"
    assert result.repair_report.attempts[-1].status == "passed"
    assert any("connectors.yaml" in fingerprint for fingerprint in result.repair_report.attempts[0].issue_fingerprints)


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


def test_repair_autofixes_missing_design_lists_without_reprompting() -> None:
    messages = [_assistant_message(_missing_design_core_lists_draft())]

    def fail_run_turn(prompt: str) -> None:
        raise AssertionError(f"repair turn should not run: {prompt}")

    result = repair_workflow_draft_messages(messages, fail_run_turn)

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 0
    assert result.repaired
    assert result.stop_reason is None
    assert not result.feedback_prompts
    latest_result = validate_workflow_draft_text(messages[-1].content[0].text)
    assert latest_result.ok
    assert latest_result.documents is not None
    design = latest_result.documents.workflow["design"]
    assert design["inputs"]
    assert design["outputs"]
    assert design["failure_modes"]


def test_repair_autofixes_missing_design_metadata_without_model_turn() -> None:
    messages = [_assistant_message(_missing_design_assumptions_side_effects_draft())]

    def fail_run_turn(prompt: str) -> None:
        raise AssertionError(f"repair turn should not run: {prompt}")

    result = repair_workflow_draft_messages(messages, fail_run_turn)

    assert result.validation is not None
    assert result.validation.ok
    assert result.attempts == 0
    assert result.repaired
    assert not result.exhausted
    latest_result = validate_workflow_draft_text(messages[-1].content[0].text)
    assert latest_result.ok
    assert latest_result.documents is not None
    design = latest_result.documents.workflow["design"]
    assert design["assumptions"]
    assert design["side_effects"]


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
