from __future__ import annotations

from pathlib import Path

from appv24.workflows.drafts import (
    WorkflowDraftBlocks,
    extract_workflow_draft_blocks,
    parse_workflow_draft_blocks,
)


WORKFLOW_YAML = """
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
""".lstrip()

CONNECTORS_YAML = """
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
""".lstrip()

PERMISSIONS_YAML = """
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
""".lstrip()

FIXTURE_YAML = """
input:
  repo: owner/project
""".lstrip()


def assert_has_issue(issues: tuple, *, code: str, file: str, message_substring: str | None = None) -> None:
    matches = [issue for issue in issues if issue.code == code and issue.file == file and issue.path == ()]
    if message_substring is not None:
        matches = [issue for issue in matches if message_substring in issue.message]
    assert matches, f"expected issue not found: code={code!r} file={file!r}"


def test_extract_workflow_draft_blocks_reads_labeled_fences() -> None:
    extraction = extract_workflow_draft_blocks(
        f"""
Here is the package draft.

```yaml workflow.yaml
{WORKFLOW_YAML}```

```yaml connectors.yaml
{CONNECTORS_YAML}```

```yaml permissions.yml
{PERMISSIONS_YAML}```

```yaml dry_run_fixtures/happy_path.yml
{FIXTURE_YAML}```

```markdown rollback.md
# Rollback

Close the drafted issue if it was created.
```
"""
    )

    assert extraction.issues == ()
    assert "dependency-security-review" in extraction.blocks.workflow
    assert "github" in extraction.blocks.connectors
    assert "human-review" in extraction.blocks.permissions
    assert extraction.blocks.dry_run_fixtures == {"dry_run_fixtures/happy_path.yaml": FIXTURE_YAML}
    assert extraction.blocks.rollback_text == "# Rollback\n\nClose the drafted issue if it was created.\n"


def test_extract_workflow_draft_blocks_uses_heading_fallback() -> None:
    extraction = extract_workflow_draft_blocks(
        f"""
### workflow.yml
```yaml
{WORKFLOW_YAML}```

### connectors.yaml
```yaml
{CONNECTORS_YAML}```

### permissions.yaml
```yaml
{PERMISSIONS_YAML}```
"""
    )

    assert extraction.issues == ()
    assert extraction.blocks.workflow == WORKFLOW_YAML
    assert extraction.blocks.connectors == CONNECTORS_YAML
    assert extraction.blocks.permissions == PERMISSIONS_YAML


def test_extract_workflow_draft_blocks_reports_duplicate_required_file() -> None:
    extraction = extract_workflow_draft_blocks(
        f"""
```yaml workflow.yml
{WORKFLOW_YAML}```

```yaml workflow.yaml
name: duplicate
```
"""
    )

    assert extraction.blocks.workflow == WORKFLOW_YAML
    assert_has_issue(extraction.issues, code="duplicate_block", file="workflow.yaml", message_substring="workflow.yaml")


def test_parse_workflow_draft_blocks_builds_package_documents() -> None:
    result = parse_workflow_draft_blocks(
        WorkflowDraftBlocks(
            workflow=WORKFLOW_YAML,
            connectors=CONNECTORS_YAML,
            permissions=PERMISSIONS_YAML,
            dry_run_fixtures={"dry_run_fixtures/happy_path.yaml": FIXTURE_YAML},
            rollback_text="# Rollback\n\nClose the drafted issue if it was created.\n",
        )
    )

    assert result.issues == ()
    assert result.documents is not None
    assert result.documents.root == Path("<workflow-draft>")
    assert result.documents.workflow["name"] == "dependency-security-review"
    assert result.documents.connectors["connectors"][0]["id"] == "github"
    assert result.documents.permissions["approvals"][0]["id"] == "human-review"
    assert result.documents.dry_run_fixtures == {"happy_path.yaml": {"input": {"repo": "owner/project"}}}
    assert result.documents.rollback_text == "# Rollback\n\nClose the drafted issue if it was created.\n"


def test_parse_workflow_draft_blocks_reports_yaml_errors() -> None:
    result = parse_workflow_draft_blocks(
        WorkflowDraftBlocks(
            workflow="version: [unterminated\n",
            connectors=CONNECTORS_YAML,
            permissions=PERMISSIONS_YAML,
        )
    )

    assert result.documents is None
    assert_has_issue(result.issues, code="parse_error", file="workflow.yaml", message_substring="failed to parse")


def test_parse_workflow_draft_blocks_requires_mapping_documents() -> None:
    result = parse_workflow_draft_blocks(
        WorkflowDraftBlocks(
            workflow="- not\n- a\n- mapping\n",
            connectors=CONNECTORS_YAML,
            permissions=PERMISSIONS_YAML,
        )
    )

    assert result.documents is None
    assert_has_issue(result.issues, code="invalid_document", file="workflow.yaml", message_substring="mapping")
