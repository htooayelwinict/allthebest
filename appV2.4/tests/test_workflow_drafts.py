from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import appv24.workflows.drafts as drafts
from appv24.workflows.drafts import (
    WorkflowDraftBlocks,
    WorkflowDraftSaveError,
    extract_workflow_draft_blocks,
    parse_workflow_draft_blocks,
)
from appv24.workflows.live_validation import (
    format_workflow_validation_report,
    should_validate_workflow_drafts,
)
from appv24.workflows.validator import validate_workflow_draft_text


WORKFLOW_YAML = """
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

REJECTION_FIXTURE_YAML = """
approval:
  human-review: rejected
""".lstrip()

ROLLBACK_MARKDOWN = "# Rollback\n\nClose the drafted issue if it was created.\n"


def assert_has_issue(issues: tuple, *, code: str, file: str, message_substring: str | None = None) -> None:
    matches = [issue for issue in issues if issue.code == code and issue.file == file and issue.path == ()]
    if message_substring is not None:
        matches = [issue for issue in matches if message_substring in issue.message]
    assert matches, f"expected issue not found: code={code!r} file={file!r}"


def valid_workflow_draft_text(*, workflow: str = WORKFLOW_YAML) -> str:
    return f"""
Here is the package draft.

```yaml workflow.yaml
{workflow}```

```yaml connectors.yaml
{CONNECTORS_YAML}```

```yaml permissions.yaml
{PERMISSIONS_YAML}```

```yaml dry_run_fixtures/happy_path.yaml
{FIXTURE_YAML}```

```yaml dry_run_fixtures/rejection_path.yaml
{REJECTION_FIXTURE_YAML}```

```markdown rollback.md
{ROLLBACK_MARKDOWN}```
"""


def valid_workflow_draft_blocks(*, rollback_text: str | None = ROLLBACK_MARKDOWN) -> WorkflowDraftBlocks:
    return WorkflowDraftBlocks(
        workflow=WORKFLOW_YAML,
        connectors=CONNECTORS_YAML,
        permissions=PERMISSIONS_YAML,
        dry_run_fixtures={
            "dry_run_fixtures/happy_path.yml": FIXTURE_YAML,
            "dry_run_fixtures/rejection_path.yaml": REJECTION_FIXTURE_YAML,
        },
        rollback_text=rollback_text,
    )


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


def test_validate_workflow_draft_text_accepts_valid_draft() -> None:
    result = validate_workflow_draft_text(valid_workflow_draft_text())

    assert result.ok
    assert result.documents is not None
    assert result.blocks.workflow == WORKFLOW_YAML
    assert result.validation.issues == ()


def test_validate_workflow_draft_text_accepts_scalar_secret_ref_values() -> None:
    fixture = FIXTURE_YAML.replace(
        "input:\n  repo: owner/project",
        'input:\n  repo: owner/project\n  github_token: "secretRef: GITHUB_TOKEN_REF"',
    )
    draft = valid_workflow_draft_text().replace(FIXTURE_YAML, fixture, 1)

    result = validate_workflow_draft_text(draft)

    assert result.ok
    assert result.documents is not None
    assert result.validation.issues == ()


def test_validate_workflow_draft_text_rejects_raw_secret_ref_values() -> None:
    fixture = FIXTURE_YAML.replace(
        "input:\n  repo: owner/project",
        "input:\n  repo: owner/project\n  github_token:\n    secretRef: sk-or-v1-abc123abc123abc123",
    )
    draft = valid_workflow_draft_text().replace(FIXTURE_YAML, fixture, 1)

    result = validate_workflow_draft_text(draft)

    assert not result.ok
    assert any(issue.code == "raw_secret" for issue in result.validation.issues)


def test_validate_workflow_draft_text_reports_semantic_issues() -> None:
    broken_workflow = WORKFLOW_YAML.replace("next: done", "next: missing_step", 1)

    result = validate_workflow_draft_text(valid_workflow_draft_text(workflow=broken_workflow))

    assert not result.ok
    assert result.documents is not None
    assert any(issue.code == "unknown_transition" for issue in result.validation.issues)


def test_validate_workflow_draft_text_reports_missing_blocks_without_documents() -> None:
    result = validate_workflow_draft_text("No fenced package blocks here.\n")

    assert result.documents is None
    assert not result.ok
    assert_has_issue(result.validation.issues, code="missing_block", file="workflow.yaml")


def test_save_workflow_draft_blocks_writes_package_under_cwd(tmp_path) -> None:
    saved_path = drafts.save_workflow_draft_blocks(
        valid_workflow_draft_blocks(rollback_text=ROLLBACK_MARKDOWN.rstrip("\n")),
        cwd=tmp_path,
        relative_path="drafts/dependency-security-review",
    )

    assert saved_path == (tmp_path / "drafts" / "dependency-security-review").resolve()
    assert yaml.safe_load((saved_path / "workflow.yaml").read_text(encoding="utf-8"))["name"] == (
        "dependency-security-review"
    )
    assert yaml.safe_load((saved_path / "connectors.yaml").read_text(encoding="utf-8"))["connectors"][0]["id"] == (
        "github"
    )
    assert yaml.safe_load((saved_path / "permissions.yaml").read_text(encoding="utf-8"))["approvals"][0]["id"] == (
        "human-review"
    )
    assert (saved_path / "dry_run_fixtures" / "happy_path.yaml").is_file()
    assert yaml.safe_load((saved_path / "dry_run_fixtures" / "rejection_path.yaml").read_text(encoding="utf-8")) == {
        "approval": {"human-review": "rejected"}
    }
    assert (saved_path / "rollback.md").read_text(encoding="utf-8") == ROLLBACK_MARKDOWN


def test_save_workflow_draft_blocks_rejects_absolute_path(tmp_path) -> None:
    with pytest.raises(WorkflowDraftSaveError, match="relative path"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path=str((tmp_path / "absolute").resolve()),
        )


def test_save_workflow_draft_blocks_rejects_parent_traversal(tmp_path) -> None:
    with pytest.raises(WorkflowDraftSaveError, match="must stay inside"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="../outside",
        )


def test_save_workflow_draft_blocks_rejects_overwrite(tmp_path) -> None:
    package_path = tmp_path / "drafts" / "existing"
    package_path.mkdir(parents=True)
    (package_path / "workflow.yaml").write_text("existing: true\n", encoding="utf-8")

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts/existing",
        )

    assert (package_path / "workflow.yaml").read_text(encoding="utf-8") == "existing: true\n"


def test_save_workflow_draft_blocks_rejects_existing_target_file(tmp_path) -> None:
    target_path = tmp_path / "drafts"
    target_path.write_text("existing draft file\n", encoding="utf-8")

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts",
        )

    assert target_path.read_text(encoding="utf-8") == "existing draft file\n"


def test_save_workflow_draft_blocks_rejects_existing_fixture_directory_file_before_writing(tmp_path) -> None:
    target_path = tmp_path / "drafts" / "existing"
    target_path.mkdir(parents=True)
    (target_path / "dry_run_fixtures").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts/existing",
        )

    assert not (target_path / "workflow.yaml").exists()
    assert not (target_path / "connectors.yaml").exists()
    assert not (target_path / "permissions.yaml").exists()


def test_save_workflow_draft_blocks_cleans_up_after_write_failure(monkeypatch, tmp_path) -> None:
    target_path = tmp_path / "drafts" / "write-failure"
    original_write_text = drafts._write_text_exclusive

    def fail_connectors_write(path: Path, text: str):
        if path.name == "connectors.yaml":
            raise OSError("simulated write failure")
        return original_write_text(path, text)

    monkeypatch.setattr(drafts, "_write_text_exclusive", fail_connectors_write)

    with pytest.raises(WorkflowDraftSaveError, match="simulated write failure"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts/write-failure",
        )

    assert not target_path.exists()
    assert list((tmp_path / "drafts").glob(".workflow-draft-save-*")) == []


def test_save_workflow_draft_blocks_rejects_target_created_before_publish(monkeypatch, tmp_path) -> None:
    target_path = tmp_path / "drafts" / "race"
    original_mkdir = Path.mkdir

    def create_target_before_publish(path: Path, *args, **kwargs):
        if path == target_path and not target_path.exists():
            original_mkdir(target_path, parents=True)
            (target_path / "marker.txt").write_text("do not overwrite\n", encoding="utf-8")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", create_target_before_publish)

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts/race",
        )

    assert (target_path / "marker.txt").read_text(encoding="utf-8") == "do not overwrite\n"
    assert not (target_path / "workflow.yaml").exists()
    assert list((tmp_path / "drafts").glob(".workflow-draft-save-*")) == []


def test_save_workflow_draft_blocks_rejects_empty_target_directory_created_before_publish(monkeypatch, tmp_path) -> None:
    target_path = tmp_path / "drafts" / "empty-race"
    original_mkdir = Path.mkdir

    def create_empty_target_before_publish(path: Path, *args, **kwargs):
        if path == target_path and not target_path.exists():
            original_mkdir(target_path, parents=True)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", create_empty_target_before_publish)

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        drafts.save_workflow_draft_blocks(
            valid_workflow_draft_blocks(),
            cwd=tmp_path,
            relative_path="drafts/empty-race",
        )

    assert target_path.is_dir()
    assert list(target_path.iterdir()) == []
    assert list((tmp_path / "drafts").glob(".workflow-draft-save-*")) == []


def test_format_workflow_validation_report_passes_exactly() -> None:
    result = validate_workflow_draft_text(valid_workflow_draft_text())

    assert format_workflow_validation_report(result) == "Workflow draft validation: passed"


def test_format_workflow_validation_report_failure_includes_missing_block() -> None:
    result = validate_workflow_draft_text("No fenced package blocks here.\n")

    report = format_workflow_validation_report(result)

    assert report.startswith("Workflow draft validation: failed")
    assert "missing_block workflow.yaml" in report


def test_should_validate_workflow_drafts_only_matches_workflow_designer_lite() -> None:
    assert should_validate_workflow_drafts("workflow-designer-lite")
    assert should_validate_workflow_drafts(" WORKFLOW_DESIGNER_LITE ")
    assert not should_validate_workflow_drafts("coding")
    assert not should_validate_workflow_drafts(None)
