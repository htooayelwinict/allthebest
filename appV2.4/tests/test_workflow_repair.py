from __future__ import annotations

from types import SimpleNamespace

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


def _valid_draft() -> str:
    return """
Here is the workflow package draft.

```yaml workflow.yaml
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
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


def test_validate_latest_workflow_draft_returns_none_without_assistant_text() -> None:
    assert validate_latest_workflow_draft([]) is None


def test_compact_workflow_validation_issues_caps_issue_count() -> None:
    result = validate_workflow_draft_text(_invalid_draft())

    rendered = compact_workflow_validation_issues(result, max_issues=1)

    assert rendered.startswith("- ")
    assert "connectors.yaml" in rendered
    assert "side_effect" in rendered
    assert len([line for line in rendered.splitlines() if "side_effect" in line]) == 1


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
