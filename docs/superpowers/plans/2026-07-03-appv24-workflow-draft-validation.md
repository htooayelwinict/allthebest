# appv24 Workflow Draft Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect `workflow-designer-lite` LLM drafts to the existing appv24 workflow validator, then allow explicit user-approved saving of the last valid draft.

**Architecture:** Add a workflow draft layer under `appV2.4/appv24/workflows/` that extracts labeled fenced blocks, parses YAML safely in memory, validates `PackageDocuments`, and formats a validation report. Wire the report into profile-specific CLI/TUI post-turn rendering without changing agent loop, provider streaming, compaction, session store, or `app.py`.

**Tech Stack:** Python 3.13, PyYAML `safe_load`/`safe_dump`, existing `jsonschema` validator path, appv24 profile registry, argparse CLI, appv24 TUI components, pytest.

---

## Scope Boundaries

This plan is appv24-only.

Do not touch appv23, appv231, npm publish, GHCR, or release packaging.

Do not touch red-zone appv24 files:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Allowed yellow-zone changes:

```text
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
```

Allowed green-zone changes:

```text
appV2.4/appv24/workflows/
appV2.4/appv24/profiles/builtins.py
appV2.4/tests/
docs/
```

No workflow execution, external API calls, shell commands, cloud provisioning, message sending, or secret requests are introduced.

## File Structure

Create:

```text
appV2.4/appv24/workflows/drafts.py
appV2.4/appv24/workflows/live_validation.py
appV2.4/tests/test_workflow_drafts.py
```

Modify:

```text
appV2.4/appv24/workflows/__init__.py
appV2.4/appv24/workflows/validator.py
appV2.4/appv24/profiles/builtins.py
appV2.4/appv24/cli.py
appV2.4/appv24/tui/interactive_mode.py
appV2.4/tests/test_workflow_packages.py
appV2.4/tests/test_profiles.py
appV2.4/tests/test_cli.py
appV2.4/tests/test_tui.py
docs/appv24/workflow-package-validator.md
```

## Design Units

`drafts.py`

- Extract supported workflow package blocks from assistant Markdown.
- Parse extracted YAML with `yaml.safe_load`.
- Convert parsed blocks into `PackageDocuments`.
- Save a valid draft to a package directory only when explicitly asked.

`validator.py`

- Add `validate_workflow_documents(documents)`.
- Keep `validate_workflow_package(path)` behavior stable.
- Add `validate_workflow_draft_text(text)`.

`live_validation.py`

- Decide whether a selected profile should validate assistant drafts.
- Extract assistant text from app messages.
- Format validation reports for CLI/TUI.
- Hold lightweight state for the last valid draft.

`cli.py`

- For `workflow-designer-lite`, print validation status after the assistant response.
- In plain loop, support `/workflow-save relative/path`.

`tui/interactive_mode.py`

- For `workflow-designer-lite`, render validation status below the assistant response after a turn.
- Support `/workflow-save relative/path`.

## Task 1: Add In-Memory Document Validation API

**Files:**

- Modify: `appV2.4/appv24/workflows/validator.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add failing tests for document-based validation**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
def test_validate_workflow_documents_returns_ok_for_valid_documents(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)

    from appv24.workflows import validate_workflow_documents

    result = validate_workflow_documents(documents)

    assert result.ok
    assert result.root == documents.root
    assert result.issues == ()


def test_validate_workflow_documents_skips_semantics_when_schema_fails(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow.pop("entrypoint")
    workflow["steps"][0]["next"] = "missing_step"
    broken = replace_documents(documents, workflow=workflow)

    from appv24.workflows import validate_workflow_documents

    result = validate_workflow_documents(broken)

    assert not result.ok
    assert_has_issue(result.issues, file="workflow.yaml", path=("entrypoint",))
    assert not any(issue.code == "unknown_transition" for issue in result.issues)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_validate_workflow_documents_returns_ok_for_valid_documents appV2.4/tests/test_workflow_packages.py::test_validate_workflow_documents_skips_semantics_when_schema_fails -q
```

Expected:

```text
ImportError: cannot import name 'validate_workflow_documents'
```

- [ ] **Step 3: Implement `validate_workflow_documents`**

Modify `appV2.4/appv24/workflows/validator.py`:

```python
"""Public workflow package validator API."""

from __future__ import annotations

from pathlib import Path

from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult, sorted_issues
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.semantic import validate_semantics


def validate_workflow_documents(documents: PackageDocuments) -> WorkflowValidationResult:
    schema_issues = validate_schema_documents(documents)
    if schema_issues:
        return WorkflowValidationResult(root=documents.root, issues=sorted_issues(schema_issues))
    return WorkflowValidationResult(root=documents.root, issues=sorted_issues(validate_semantics(documents)))


def validate_workflow_package(path: str | Path) -> WorkflowValidationResult:
    root = Path(path).expanduser()
    resolved_root = root.resolve()
    try:
        documents = load_package_documents(root)
    except WorkflowPackageError as error:
        return WorkflowValidationResult(
            root=resolved_root,
            issues=(
                WorkflowIssue(
                    code="load_error",
                    file=str(resolved_root),
                    path=(),
                    message=str(error),
                ),
            ),
        )

    return validate_workflow_documents(documents)
```

- [ ] **Step 4: Export the new API**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
"""Offline workflow package validation for appv24."""

from __future__ import annotations

from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult
from appv24.workflows.validator import validate_workflow_documents, validate_workflow_package

__all__ = [
    "WorkflowIssue",
    "WorkflowPackageError",
    "WorkflowValidationResult",
    "validate_workflow_documents",
    "validate_workflow_package",
]
```

- [ ] **Step 5: Run the focused tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_validate_workflow_documents_returns_ok_for_valid_documents appV2.4/tests/test_workflow_packages.py::test_validate_workflow_documents_skips_semantics_when_schema_fails -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Run existing workflow package tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add appV2.4/appv24/workflows/validator.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): validate workflow documents in memory"
```

## Task 2: Extract And Parse Workflow Draft Blocks

**Files:**

- Create: `appV2.4/appv24/workflows/drafts.py`
- Create: `appV2.4/tests/test_workflow_drafts.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`

- [ ] **Step 1: Add failing draft extraction tests**

Create `appV2.4/tests/test_workflow_drafts.py`:

````python
from __future__ import annotations

from pathlib import Path

from appv24.workflows.drafts import (
    WorkflowDraftBlocks,
    extract_workflow_draft_blocks,
    parse_workflow_draft_blocks,
)


VALID_WORKFLOW = """
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
triggers:
  - id: weekday
    type: schedule
    schedule: "0 9 * * 1-5"
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
"""

VALID_CONNECTORS = """
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
"""

VALID_PERMISSIONS = """
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
"""


def valid_draft_text() -> str:
    return f'''
```yaml workflow.yaml
{VALID_WORKFLOW}
```

```yaml connectors.yaml
{VALID_CONNECTORS}
```

```yaml permissions.yaml
{VALID_PERMISSIONS}
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

Close the drafted issue if one was created.
```
'''.strip()


def test_extract_workflow_draft_blocks_reads_labeled_fences() -> None:
    extraction = extract_workflow_draft_blocks(valid_draft_text())

    assert extraction.issues == ()
    assert extraction.blocks.workflow.strip().startswith("version: appv24.workflow/v1")
    assert "GitHub REST API" in extraction.blocks.connectors
    assert "human-review" in extraction.blocks.permissions
    assert "dry_run_fixtures/happy_path.yaml" in extraction.blocks.dry_run_fixtures
    assert extraction.blocks.rollback_text.startswith("# Rollback")


def test_extract_workflow_draft_blocks_uses_heading_fallback() -> None:
    text = f"""
### workflow.yaml
```yaml
{VALID_WORKFLOW}
```
### connectors.yaml
```yaml
{VALID_CONNECTORS}
```
### permissions.yaml
```yaml
{VALID_PERMISSIONS}
```
"""

    extraction = extract_workflow_draft_blocks(text)

    assert extraction.issues == ()
    assert extraction.blocks.workflow.strip().startswith("version: appv24.workflow/v1")


def test_extract_workflow_draft_blocks_reports_duplicate_required_file() -> None:
    text = valid_draft_text() + "\n\n```yaml workflow.yaml\nversion: appv24.workflow/v1\n```"

    extraction = extract_workflow_draft_blocks(text)

    assert any(issue.code == "duplicate_block" and issue.file == "workflow.yaml" for issue in extraction.issues)


def test_parse_workflow_draft_blocks_builds_package_documents() -> None:
    extraction = extract_workflow_draft_blocks(valid_draft_text())

    parsed = parse_workflow_draft_blocks(extraction.blocks)

    assert parsed.issues == ()
    assert parsed.documents is not None
    assert parsed.documents.root == Path("<workflow-draft>")
    assert parsed.documents.workflow["name"] == "dependency-security-review"


def test_parse_workflow_draft_blocks_reports_yaml_errors() -> None:
    blocks = WorkflowDraftBlocks(
        workflow="version: [",
        connectors=VALID_CONNECTORS,
        permissions=VALID_PERMISSIONS,
    )

    parsed = parse_workflow_draft_blocks(blocks)

    assert parsed.documents is None
    assert any(issue.code == "parse_error" and issue.file == "workflow.yaml" for issue in parsed.issues)


def test_parse_workflow_draft_blocks_requires_mapping_documents() -> None:
    blocks = WorkflowDraftBlocks(
        workflow="- not\n- a\n- mapping\n",
        connectors=VALID_CONNECTORS,
        permissions=VALID_PERMISSIONS,
    )

    parsed = parse_workflow_draft_blocks(blocks)

    assert parsed.documents is None
    assert any(issue.code == "invalid_document" and issue.file == "workflow.yaml" for issue in parsed.issues)
````

- [ ] **Step 2: Run draft tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv24.workflows.drafts'
```

- [ ] **Step 3: Implement draft extraction and parsing**

Create `appV2.4/appv24/workflows/drafts.py`:

```python
"""Extract and parse passive workflow package drafts from assistant text."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.loader import PackageDocuments

VIRTUAL_DRAFT_ROOT = Path("<workflow-draft>")
REQUIRED_DRAFT_FILES = ("workflow.yaml", "connectors.yaml", "permissions.yaml")
_FENCE_RE = re.compile(r"```(?P<info>[^\n`]*)\n(?P<body>.*?)(?:\n```|$)", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{2,6}\s+(?P<name>[A-Za-z0-9_./-]+\.(?:ya?ml|md))\s*$", re.MULTILINE)
_COMMENT_NAME_RE = re.compile(r"^\s*#\s*(?P<name>[A-Za-z0-9_./-]+\.(?:ya?ml|md))\s*$")
_SUPPORTED_FILE_RE = re.compile(
    r"^(workflow\.ya?ml|connectors\.ya?ml|permissions\.ya?ml|rollback\.md|dry_run_fixtures/[A-Za-z0-9_.-]+\.ya?ml)$"
)


@dataclass(frozen=True)
class WorkflowDraftBlocks:
    workflow: str = ""
    connectors: str = ""
    permissions: str = ""
    dry_run_fixtures: Mapping[str, str] = field(default_factory=dict)
    rollback_text: str | None = None


@dataclass(frozen=True)
class WorkflowDraftExtraction:
    blocks: WorkflowDraftBlocks
    issues: tuple[WorkflowIssue, ...] = ()


@dataclass(frozen=True)
class WorkflowDraftParseResult:
    documents: PackageDocuments | None
    issues: tuple[WorkflowIssue, ...] = ()


def extract_workflow_draft_blocks(text: str) -> WorkflowDraftExtraction:
    headings = _heading_names_by_position(text)
    values: dict[str, str] = {}
    issues: list[WorkflowIssue] = []

    for match in _FENCE_RE.finditer(text):
        body = match.group("body").strip()
        filename = _filename_for_block(match, body, headings)
        if filename is None:
            continue
        normalized = _normalize_supported_filename(filename)
        if normalized is None:
            continue
        if normalized in values:
            issues.append(WorkflowIssue("duplicate_block", normalized, (), f"duplicate workflow draft block: {normalized}"))
            continue
        values[normalized] = body

    blocks = WorkflowDraftBlocks(
        workflow=values.get("workflow.yaml", values.get("workflow.yml", "")),
        connectors=values.get("connectors.yaml", values.get("connectors.yml", "")),
        permissions=values.get("permissions.yaml", values.get("permissions.yml", "")),
        dry_run_fixtures={
            filename: content for filename, content in sorted(values.items()) if filename.startswith("dry_run_fixtures/")
        },
        rollback_text=values.get("rollback.md"),
    )
    return WorkflowDraftExtraction(blocks=blocks, issues=tuple(sorted_issues(issues)))


def parse_workflow_draft_blocks(blocks: WorkflowDraftBlocks) -> WorkflowDraftParseResult:
    issues: list[WorkflowIssue] = []
    workflow = _parse_mapping("workflow.yaml", blocks.workflow, issues)
    connectors = _parse_mapping("connectors.yaml", blocks.connectors, issues)
    permissions = _parse_mapping("permissions.yaml", blocks.permissions, issues)
    fixtures: dict[str, Mapping[str, Any]] = {}
    for filename, content in blocks.dry_run_fixtures.items():
        parsed = _parse_mapping(filename, content, issues)
        if parsed is not None:
            fixtures[Path(filename).name] = parsed

    missing = [
        filename
        for filename, value in (
            ("workflow.yaml", blocks.workflow),
            ("connectors.yaml", blocks.connectors),
            ("permissions.yaml", blocks.permissions),
        )
        if not value.strip()
    ]
    for filename in missing:
        issues.append(WorkflowIssue("missing_block", filename, (), f"missing required workflow draft block: {filename}"))

    if issues or workflow is None or connectors is None or permissions is None:
        return WorkflowDraftParseResult(documents=None, issues=tuple(sorted_issues(issues)))

    return WorkflowDraftParseResult(
        documents=PackageDocuments(
            root=VIRTUAL_DRAFT_ROOT,
            workflow=workflow,
            connectors=connectors,
            permissions=permissions,
            dry_run_fixtures=fixtures,
            rollback_text=blocks.rollback_text,
        ),
        issues=(),
    )


def _parse_mapping(file: str, content: str, issues: list[WorkflowIssue]) -> Mapping[str, Any] | None:
    if not content.strip():
        return None
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as error:
        issues.append(WorkflowIssue("parse_error", file, (), f"failed to parse {file}: {error}"))
        return None
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        issues.append(WorkflowIssue("invalid_document", file, (), f"{file} must contain a mapping document"))
        return None
    return parsed


def _heading_names_by_position(text: str) -> dict[int, str]:
    return {match.end(): match.group("name") for match in _HEADING_RE.finditer(text)}


def _filename_for_block(match: re.Match[str], body: str, headings: Mapping[int, str]) -> str | None:
    info_parts = match.group("info").strip().split()
    for part in info_parts:
        if "." in part:
            return part.strip()
    previous_headings = [position for position in headings if position <= match.start()]
    if previous_headings:
        return headings[max(previous_headings)]
    first_line = body.splitlines()[0] if body.splitlines() else ""
    comment_match = _COMMENT_NAME_RE.match(first_line)
    return comment_match.group("name") if comment_match else None


def _normalize_supported_filename(value: str) -> str | None:
    candidate = value.strip().strip("`").lstrip("./")
    if not _SUPPORTED_FILE_RE.match(candidate):
        return None
    if candidate.endswith(".yml"):
        candidate = candidate[:-4] + ".yaml"
    return candidate
```

- [ ] **Step 4: Export draft helpers**

Modify `appV2.4/appv24/workflows/__init__.py`:

```python
from appv24.workflows.drafts import (
    WorkflowDraftBlocks,
    WorkflowDraftExtraction,
    WorkflowDraftParseResult,
    extract_workflow_draft_blocks,
    parse_workflow_draft_blocks,
)
```

Add those names to `__all__`.

- [ ] **Step 5: Run draft tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add appV2.4/appv24/workflows/drafts.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_drafts.py
git commit -m "feat(appv24): extract workflow draft blocks"
```

## Task 3: Validate Draft Text And Format Reports

**Files:**

- Modify: `appV2.4/appv24/workflows/validator.py`
- Create: `appV2.4/appv24/workflows/live_validation.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Modify: `appV2.4/tests/test_workflow_drafts.py`

- [ ] **Step 1: Add failing draft validation tests**

Append to `appV2.4/tests/test_workflow_drafts.py`:

```python
from appv24.workflows import validate_workflow_draft_text
from appv24.workflows.live_validation import format_workflow_validation_report, should_validate_workflow_drafts


def test_validate_workflow_draft_text_passes_valid_draft() -> None:
    result = validate_workflow_draft_text(valid_draft_text())

    assert result.validation.ok
    assert result.documents is not None
    assert result.blocks.workflow.strip().startswith("version: appv24.workflow/v1")


def test_validate_workflow_draft_text_reports_semantic_issues() -> None:
    text = valid_draft_text().replace("next: done", "next: missing_step", 1)

    result = validate_workflow_draft_text(text)

    assert not result.validation.ok
    assert any(issue.code == "unknown_transition" for issue in result.validation.issues)


def test_validate_workflow_draft_text_reports_missing_blocks() -> None:
    result = validate_workflow_draft_text("No fenced package blocks here.")

    assert result.documents is None
    assert not result.validation.ok
    assert any(issue.code == "missing_block" for issue in result.validation.issues)


def test_format_workflow_validation_report_for_pass() -> None:
    result = validate_workflow_draft_text(valid_draft_text())

    report = format_workflow_validation_report(result)

    assert report == "Workflow draft validation: passed"


def test_format_workflow_validation_report_for_failure() -> None:
    result = validate_workflow_draft_text("No fenced package blocks here.")

    report = format_workflow_validation_report(result)

    assert report.startswith("Workflow draft validation: failed")
    assert "missing_block workflow.yaml" in report


def test_should_validate_workflow_drafts_only_for_workflow_profile() -> None:
    assert should_validate_workflow_drafts("workflow-designer-lite")
    assert should_validate_workflow_drafts("WORKFLOW_DESIGNER_LITE")
    assert not should_validate_workflow_drafts("coding")
    assert not should_validate_workflow_drafts(None)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py -q
```

Expected:

```text
ImportError: cannot import name 'validate_workflow_draft_text'
```

- [ ] **Step 3: Add draft validation result type and API**

Modify `appV2.4/appv24/workflows/validator.py`:

```python
from dataclasses import dataclass

from appv24.workflows.drafts import (
    WorkflowDraftBlocks,
    extract_workflow_draft_blocks,
    parse_workflow_draft_blocks,
)
```

Add:

```python
@dataclass(frozen=True)
class WorkflowDraftValidationResult:
    blocks: WorkflowDraftBlocks
    documents: PackageDocuments | None
    validation: WorkflowValidationResult

    @property
    def ok(self) -> bool:
        return self.validation.ok
```

Add:

```python
def validate_workflow_draft_text(text: str) -> WorkflowDraftValidationResult:
    extraction = extract_workflow_draft_blocks(text)
    parsed = parse_workflow_draft_blocks(extraction.blocks)
    if parsed.documents is None:
        issues = sorted_issues((*extraction.issues, *parsed.issues))
        return WorkflowDraftValidationResult(
            blocks=extraction.blocks,
            documents=None,
            validation=WorkflowValidationResult(root=Path("<workflow-draft>"), issues=issues),
        )
    validation = validate_workflow_documents(parsed.documents)
    issues = sorted_issues((*extraction.issues, *validation.issues))
    return WorkflowDraftValidationResult(
        blocks=extraction.blocks,
        documents=parsed.documents if not issues else parsed.documents,
        validation=WorkflowValidationResult(root=parsed.documents.root, issues=issues),
    )
```

- [ ] **Step 4: Add report formatting helpers**

Create `appV2.4/appv24/workflows/live_validation.py`:

```python
"""Profile-specific workflow draft validation helpers."""

from __future__ import annotations

from appv24.workflows.validator import WorkflowDraftValidationResult

WORKFLOW_DESIGNER_PROFILE_ID = "workflow-designer-lite"


def should_validate_workflow_drafts(profile_id: str | None) -> bool:
    if profile_id is None:
        return False
    return profile_id.strip().lower().replace("_", "-") == WORKFLOW_DESIGNER_PROFILE_ID


def assistant_text_from_messages(messages: list[object]) -> str:
    for message in reversed(messages):
        if getattr(message, "role", None) != "assistant":
            continue
        texts = [block.text for block in getattr(message, "content", []) if getattr(block, "type", None) == "text"]
        return "".join(texts)
    return ""


def format_workflow_validation_report(result: WorkflowDraftValidationResult, *, max_issues: int = 12) -> str:
    if result.validation.ok:
        return "Workflow draft validation: passed"
    lines = ["Workflow draft validation: failed"]
    for issue in result.validation.issues[:max_issues]:
        lines.append(f"- {issue.code} {issue.location}: {issue.message}")
    remaining = len(result.validation.issues) - max_issues
    if remaining > 0:
        lines.append(f"- ... {remaining} more issue(s)")
    return "\n".join(lines)
```

- [ ] **Step 5: Export validation names**

Modify `appV2.4/appv24/workflows/__init__.py` to export:

```python
from appv24.workflows.validator import (
    WorkflowDraftValidationResult,
    validate_workflow_documents,
    validate_workflow_draft_text,
    validate_workflow_package,
)
```

Add `WorkflowDraftValidationResult` and `validate_workflow_draft_text` to `__all__`.

- [ ] **Step 6: Run draft tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Run workflow package tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add appV2.4/appv24/workflows/validator.py appV2.4/appv24/workflows/live_validation.py appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_drafts.py
git commit -m "feat(appv24): validate workflow drafts in memory"
```

## Task 4: Tighten Workflow Profile Output Contract

**Files:**

- Modify: `appV2.4/appv24/profiles/builtins.py`
- Modify: `appV2.4/tests/test_profiles.py`

- [ ] **Step 1: Add failing profile prompt assertions**

Modify `test_workflow_designer_lite_profile_is_passive_and_read_only` in `appV2.4/tests/test_profiles.py`:

```python
    assert "labeled fenced code blocks" in prompt
    assert "```yaml workflow.yaml" in prompt
    assert "```yaml connectors.yaml" in prompt
    assert "```yaml permissions.yaml" in prompt
    assert "```yaml dry_run_fixtures/happy_path.yaml" in prompt
    assert "```markdown rollback.md" in prompt
```

- [ ] **Step 2: Run profile tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py::test_workflow_designer_lite_profile_is_passive_and_read_only -q
```

Expected:

```text
AssertionError because the prompt does not yet require labeled fenced blocks
```

- [ ] **Step 3: Update workflow profile prompt**

Modify `_WORKFLOW_DESIGNER_LITE_APPEND` in `appV2.4/appv24/profiles/builtins.py` by adding:

```python
    "When drafting package files, use labeled fenced code blocks exactly like "
    "```yaml workflow.yaml, ```yaml connectors.yaml, ```yaml permissions.yaml, "
    "```yaml dry_run_fixtures/happy_path.yaml, "
    "```yaml dry_run_fixtures/rejection_path.yaml, and ```markdown rollback.md. "
```

Keep all existing passive safety text.

- [ ] **Step 4: Run profile tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_profiles.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add appV2.4/appv24/profiles/builtins.py appV2.4/tests/test_profiles.py
git commit -m "fix(appv24): require labeled workflow draft blocks"
```

## Task 5: Print Validation Reports In Non-Interactive And Plain CLI

**Files:**

- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Add CLI tests for profile-specific validation output**

Append tests to `appV2.4/tests/test_cli.py` using the existing CLI monkeypatch style:

```python
def test_cli_workflow_profile_prints_validation_report(monkeypatch, tmp_path, capsys) -> None:
    from appv24 import cli
    from appv24.ai.types import Model
    from appv24.agent.types import AssistantMessage, TextContent

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(
                AssistantMessage(
                    content=[TextContent(text=_valid_workflow_draft_text())],
                    timestamp=1,
                )
            )

    monkeypatch.setattr(cli, "CodingApp", lambda **kwargs: FakeApp(**kwargs))
    monkeypatch.setattr(cli, "_startup_model_from_env", lambda *a, **k: cli._StartupModelSelection(Model(id="fake", provider="openrouter")))
    monkeypatch.setattr(cli, "register_builtin_providers", lambda **kwargs: None)
    monkeypatch.setattr(cli, "_load_persisted_auth_credentials", lambda: None)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "design workflow"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft validation: passed" in captured.out


def test_cli_coding_profile_does_not_print_workflow_validation(monkeypatch, tmp_path, capsys) -> None:
    from appv24 import cli
    from appv24.ai.types import Model
    from appv24.agent.types import AssistantMessage, TextContent

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

        def run_turn(self, prompt):
            self.messages.append(AssistantMessage(content=[TextContent(text=_valid_workflow_draft_text())], timestamp=1))

    monkeypatch.setattr(cli, "CodingApp", lambda **kwargs: FakeApp(**kwargs))
    monkeypatch.setattr(cli, "_startup_model_from_env", lambda *a, **k: cli._StartupModelSelection(Model(id="fake", provider="openrouter")))
    monkeypatch.setattr(cli, "register_builtin_providers", lambda **kwargs: None)
    monkeypatch.setattr(cli, "_load_persisted_auth_credentials", lambda: None)

    exit_code = cli.main(["--cwd", str(tmp_path), "--profile", "coding", "design workflow"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Workflow draft validation:" not in captured.out
```

Add helper `_valid_workflow_draft_text()` in `test_cli.py` or import the fixture from `test_workflow_drafts.py` only if local test style already allows test imports. Prefer a local helper to avoid test coupling.

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_workflow_profile_prints_validation_report appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_print_workflow_validation -q
```

Expected:

```text
failure because CLI does not print workflow validation yet
```

- [ ] **Step 3: Add CLI validation helper**

Modify `appV2.4/appv24/cli.py` imports:

```python
from appv24.workflows.live_validation import (
    assistant_text_from_messages,
    format_workflow_validation_report,
    should_validate_workflow_drafts,
)
from appv24.workflows.validator import validate_workflow_draft_text
```

Add helper near `_print_last_assistant`:

```python
def _print_workflow_validation_if_needed(app: CodingApp, profile_id: str) -> None:
    if not should_validate_workflow_drafts(profile_id):
        return
    text = assistant_text_from_messages(app.messages)
    if not text.strip():
        return
    print(format_workflow_validation_report(validate_workflow_draft_text(text)))
```

- [ ] **Step 4: Call helper after assistant output**

In prompt mode:

```python
    if prompt:
        app.run_turn(prompt)
        _print_last_assistant(app)
        _print_workflow_validation_if_needed(app, selected_profile.id)
        return 0
```

In plain loop after `_print_last_assistant(app)`:

```python
        _print_workflow_validation_if_needed(app, selected_profile.id)
```

- [ ] **Step 5: Run focused CLI tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py::test_cli_workflow_profile_prints_validation_report appV2.4/tests/test_cli.py::test_cli_coding_profile_does_not_print_workflow_validation -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Run full CLI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): report workflow draft validation in cli"
```

## Task 6: Render Validation Reports In TUI

**Files:**

- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Modify: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Add focused TUI test for post-turn validation report**

Add a test to `appV2.4/tests/test_tui.py` following existing `InteractiveMode` fake-app patterns:

```python
def test_interactive_mode_renders_workflow_validation_after_turn(monkeypatch, tmp_path) -> None:
    from appv24.agent.types import AssistantMessage, TextContent
    from appv24.tui.interactive_mode import InteractiveMode

    app = make_fake_interactive_app(tmp_path)
    app.profile_id = "workflow-designer-lite"
    app.messages = []

    def run_turn(prompt, **kwargs):
        app.messages.append(AssistantMessage(content=[TextContent(text=_valid_workflow_draft_text())], timestamp=1))

    app.run_turn = run_turn
    mode = InteractiveMode(app, input_fn=iter(["design workflow", "/exit"]).__next__)

    exit_code = mode.run()

    assert exit_code == 0
    rendered = "\n".join(str(component) for component in mode.history.children)
    assert "Workflow draft validation: passed" in rendered
```

If existing helpers are named differently, adapt to local `test_tui.py` helper style. Do not introduce a broad new fake framework.

- [ ] **Step 2: Run the focused TUI test and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py::test_interactive_mode_renders_workflow_validation_after_turn -q
```

Expected:

```text
failure because TUI does not render workflow validation yet
```

- [ ] **Step 3: Add TUI validation imports and state**

Modify `appV2.4/appv24/tui/interactive_mode.py` imports:

```python
from appv24.workflows.live_validation import (
    assistant_text_from_messages,
    format_workflow_validation_report,
    should_validate_workflow_drafts,
)
from appv24.workflows.validator import WorkflowDraftValidationResult, validate_workflow_draft_text
```

In `InteractiveMode.__init__`, add:

```python
        self._last_workflow_validation: WorkflowDraftValidationResult | None = None
```

- [ ] **Step 4: Add profile id resolver**

Add method:

```python
    def _profile_id(self) -> str | None:
        return getattr(self.app, "profile_id", getattr(self.app.session, "profile_id", None))
```

- [ ] **Step 5: Render validation after turn completion**

In `_finish_turn_thread`, before setting Idle, add:

```python
        self._render_workflow_validation_if_needed()
```

Add method:

```python
    def _render_workflow_validation_if_needed(self) -> None:
        if not should_validate_workflow_drafts(self._profile_id()):
            return
        text = assistant_text_from_messages(self.app.messages)
        if not text.strip():
            return
        result = validate_workflow_draft_text(text)
        self._last_workflow_validation = result if result.validation.ok else None
        kind = "info" if result.validation.ok else "error"
        self.history.add(StatusLine(format_workflow_validation_report(result), kind=kind))
```

- [ ] **Step 6: Ensure CLI passes profile id to app/interactive mode**

In `appV2.4/appv24/cli.py`, after `app = CodingApp(...)`, set a harmless dynamic attribute:

```python
    app.profile_id = selected_profile.id
```

This avoids red-zone changes to `CodingApp`.

- [ ] **Step 7: Run focused TUI test and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py::test_interactive_mode_renders_workflow_validation_after_turn -q
```

Expected:

```text
1 passed
```

- [ ] **Step 8: Run relevant TUI and CLI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py appV2.4/tests/test_cli.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 9: Commit Task 6**

Run:

```bash
git add appV2.4/appv24/tui/interactive_mode.py appV2.4/appv24/cli.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): render workflow validation in tui"
```

## Task 7: Add Explicit Workflow Save Command

**Files:**

- Modify: `appV2.4/appv24/workflows/drafts.py`
- Modify: `appV2.4/appv24/workflows/live_validation.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Modify: `appV2.4/tests/test_workflow_drafts.py`
- Modify: `appV2.4/tests/test_cli.py`
- Modify: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Add draft save tests**

Append to `appV2.4/tests/test_workflow_drafts.py`:

```python
from appv24.workflows.drafts import WorkflowDraftSaveError, save_workflow_draft_blocks


def test_save_workflow_draft_blocks_writes_package_under_cwd(tmp_path: Path) -> None:
    result = validate_workflow_draft_text(valid_draft_text())

    saved_path = save_workflow_draft_blocks(result.blocks, cwd=tmp_path, relative_path="security/deps")

    assert saved_path == (tmp_path / "security/deps").resolve()
    assert (saved_path / "workflow.yaml").is_file()
    assert (saved_path / "connectors.yaml").is_file()
    assert (saved_path / "permissions.yaml").is_file()
    assert (saved_path / "dry_run_fixtures/happy_path.yaml").is_file()
    assert (saved_path / "rollback.md").read_text(encoding="utf-8").startswith("# Rollback")


def test_save_workflow_draft_blocks_rejects_absolute_path(tmp_path: Path) -> None:
    result = validate_workflow_draft_text(valid_draft_text())

    with pytest.raises(WorkflowDraftSaveError, match="relative path"):
        save_workflow_draft_blocks(result.blocks, cwd=tmp_path, relative_path=str(tmp_path / "out"))


def test_save_workflow_draft_blocks_rejects_parent_traversal(tmp_path: Path) -> None:
    result = validate_workflow_draft_text(valid_draft_text())

    with pytest.raises(WorkflowDraftSaveError, match="must stay inside"):
        save_workflow_draft_blocks(result.blocks, cwd=tmp_path, relative_path="../outside")


def test_save_workflow_draft_blocks_rejects_overwrite(tmp_path: Path) -> None:
    result = validate_workflow_draft_text(valid_draft_text())
    target = tmp_path / "security/deps"
    target.mkdir(parents=True)
    (target / "workflow.yaml").write_text("existing: true\n", encoding="utf-8")

    with pytest.raises(WorkflowDraftSaveError, match="already exists"):
        save_workflow_draft_blocks(result.blocks, cwd=tmp_path, relative_path="security/deps")
```

- [ ] **Step 2: Run save tests and verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py::test_save_workflow_draft_blocks_writes_package_under_cwd appV2.4/tests/test_workflow_drafts.py::test_save_workflow_draft_blocks_rejects_absolute_path appV2.4/tests/test_workflow_drafts.py::test_save_workflow_draft_blocks_rejects_parent_traversal appV2.4/tests/test_workflow_drafts.py::test_save_workflow_draft_blocks_rejects_overwrite -q
```

Expected:

```text
ImportError: cannot import name 'WorkflowDraftSaveError'
```

- [ ] **Step 3: Implement safe draft save**

Modify `appV2.4/appv24/workflows/drafts.py`:

```python
class WorkflowDraftSaveError(Exception):
    """Raised when a workflow draft cannot be saved safely."""
```

Add:

```python
def save_workflow_draft_blocks(blocks: WorkflowDraftBlocks, *, cwd: str | Path, relative_path: str) -> Path:
    base = Path(cwd).expanduser().resolve()
    requested = Path(relative_path).expanduser()
    if requested.is_absolute():
        raise WorkflowDraftSaveError("workflow save path must be a relative path")
    target = (base / requested).resolve()
    if base not in (target, *target.parents):
        raise WorkflowDraftSaveError("workflow save path must stay inside cwd")
    existing_files = [path for path in _planned_output_paths(target, blocks) if path.exists()]
    if existing_files:
        raise WorkflowDraftSaveError(f"workflow save target already exists: {existing_files[0]}")

    parsed = parse_workflow_draft_blocks(blocks)
    if parsed.documents is None:
        messages = "; ".join(issue.message for issue in parsed.issues)
        raise WorkflowDraftSaveError(f"workflow draft is not parseable: {messages}")

    target.mkdir(parents=True, exist_ok=True)
    _write_yaml(target / "workflow.yaml", parsed.documents.workflow)
    _write_yaml(target / "connectors.yaml", parsed.documents.connectors)
    _write_yaml(target / "permissions.yaml", parsed.documents.permissions)
    if parsed.documents.dry_run_fixtures:
        fixtures_dir = target / "dry_run_fixtures"
        fixtures_dir.mkdir()
        for filename, fixture in sorted(parsed.documents.dry_run_fixtures.items()):
            _write_yaml(fixtures_dir / filename, fixture)
    if blocks.rollback_text is not None:
        (target / "rollback.md").write_text(blocks.rollback_text.rstrip() + "\n", encoding="utf-8")
    return target


def _planned_output_paths(target: Path, blocks: WorkflowDraftBlocks) -> list[Path]:
    paths = [target / "workflow.yaml", target / "connectors.yaml", target / "permissions.yaml"]
    paths.extend(target / "dry_run_fixtures" / Path(filename).name for filename in blocks.dry_run_fixtures)
    if blocks.rollback_text is not None:
        paths.append(target / "rollback.md")
    return paths


def _write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(value), sort_keys=False), encoding="utf-8")
```

- [ ] **Step 4: Run save tests and verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Add plain CLI `/workflow-save` tests**

Add a CLI test that runs plain input mode with two inputs: a prompt and `/workflow-save saved-workflow`. Use fake `CodingApp` to return a valid draft, then assert the files exist under `tmp_path / "saved-workflow"`.

Expected CLI behavior:

```text
Workflow draft saved: /absolute/path/to/saved-workflow
Workflow draft validation: passed
```

Add a negative test for `/workflow-save ../outside` and assert the output contains:

```text
Workflow draft save failed:
```

- [ ] **Step 6: Implement CLI save command**

Modify `appV2.4/appv24/cli.py` imports:

```python
from appv24.workflows.drafts import WorkflowDraftSaveError, save_workflow_draft_blocks
```

In plain loop, before `app.run_turn(prompt)`:

```python
        if prompt.startswith("/workflow-save "):
            _handle_workflow_save_command(app, selected_profile.id, cwd_path, prompt)
            continue
```

Add helper:

```python
def _handle_workflow_save_command(app: CodingApp, profile_id: str, cwd_path: Path, prompt: str) -> None:
    if not should_validate_workflow_drafts(profile_id):
        print("Workflow draft save failed: active profile is not workflow-designer-lite")
        return
    relative_path = prompt.removeprefix("/workflow-save").strip()
    if not relative_path:
        print("Workflow draft save failed: missing relative path")
        return
    result = validate_workflow_draft_text(assistant_text_from_messages(app.messages))
    if not result.validation.ok:
        print("Workflow draft save failed: last workflow draft is not valid")
        print(format_workflow_validation_report(result))
        return
    try:
        saved_path = save_workflow_draft_blocks(result.blocks, cwd=cwd_path, relative_path=relative_path)
    except WorkflowDraftSaveError as error:
        print(f"Workflow draft save failed: {error}")
        return
    disk_result = validate_workflow_package(saved_path)
    print(f"Workflow draft saved: {saved_path}")
    print(format_workflow_validation_report(result if disk_result.ok else validate_workflow_draft_text(assistant_text_from_messages(app.messages))))
```

Also import `validate_workflow_package`.

- [ ] **Step 7: Add TUI `/workflow-save` tests**

Add TUI tests for:

- valid save after valid workflow draft;
- save rejected before any valid draft;
- save rejected for `../outside`.

Use existing `InteractiveMode` input function test style.

- [ ] **Step 8: Implement TUI save command**

In `InteractiveMode.create_base_autocomplete_provider`, add:

```python
{"name": "workflow-save", "description": "Save the last valid workflow draft under the current cwd"},
```

In the slash command handling path, add a branch equivalent to:

```python
        if prompt.startswith("/workflow-save "):
            self._handle_workflow_save(prompt)
            return True
```

Add method:

```python
    def _handle_workflow_save(self, prompt: str) -> None:
        if not should_validate_workflow_drafts(self._profile_id()):
            self.history.add(StatusLine("Workflow draft save failed: active profile is not workflow-designer-lite", kind="error"))
            return
        if self._last_workflow_validation is None or not self._last_workflow_validation.validation.ok:
            self.history.add(StatusLine("Workflow draft save failed: no valid workflow draft is available", kind="error"))
            return
        relative_path = prompt.removeprefix("/workflow-save").strip()
        if not relative_path:
            self.history.add(StatusLine("Workflow draft save failed: missing relative path", kind="error"))
            return
        try:
            saved_path = save_workflow_draft_blocks(
                self._last_workflow_validation.blocks,
                cwd=self.app.cwd,
                relative_path=relative_path,
            )
        except WorkflowDraftSaveError as error:
            self.history.add(StatusLine(f"Workflow draft save failed: {error}", kind="error"))
            return
        disk_result = validate_workflow_package(saved_path)
        if disk_result.ok:
            self.history.add(StatusLine(f"Workflow draft saved: {saved_path}", kind="info"))
        else:
            self.history.add(StatusLine("Workflow draft saved but disk validation failed", kind="error"))
            self.history.add(StatusLine("\n".join(disk_result.messages()), kind="error"))
```

- [ ] **Step 9: Run draft, CLI, and TUI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 10: Commit Task 7**

Run:

```bash
git add appV2.4/appv24/workflows/drafts.py appV2.4/appv24/workflows/live_validation.py appV2.4/appv24/cli.py appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_cli.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): save validated workflow drafts"
```

## Task 8: Documentation And Final Verification

**Files:**

- Modify: `docs/appv24/workflow-package-validator.md`
- Optionally modify: `docs/architecture/appv24-built-in-profiles.md` if present in this branch

- [ ] **Step 1: Document live draft validation**

Append to `docs/appv24/workflow-package-validator.md`:

````markdown
## Live Draft Validation

When appv24 runs with `--profile workflow-designer-lite`, assistant responses that contain labeled workflow package blocks are validated after the assistant turn finishes.

The draft validator looks for fenced blocks labeled:

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/happy_path.yaml
dry_run_fixtures/rejection_path.yaml
rollback.md
```

Validation is in-memory and passive. It does not write files, run workflows, call APIs, send messages, provision resources, or read secrets.

In interactive mode, save the last valid draft explicitly:

```text
/workflow-save relative/path
```

The save path must be relative to the active `--cwd`. Existing files are not overwritten.
````

- [ ] **Step 2: Run focused workflow/profile verification**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py appV2.4/tests/test_workflow_drafts.py appV2.4/tests/test_profiles.py -q
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Run full appv24 Python suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
all tests pass
```

- [ ] **Step 4: Run appv24 CLI npm tests**

Run:

```bash
npm --prefix packages/appv24-cli test
```

Expected:

```text
all tests pass
```

- [ ] **Step 5: Run appv24 npm pack dry-run**

Run:

```bash
npm --prefix packages/appv24-cli run pack:dry-run
```

Expected:

```text
npm pack --dry-run completes successfully
```

- [ ] **Step 6: Run boundary audit**

Run:

```bash
git diff --name-only HEAD~8..HEAD | rg '^(appV2\.3|appV2\.3\.1|packages/appv23-cli|packages/appv231-cli)/'
```

Expected:

```text
no output
```

Run:

```bash
git diff --name-only HEAD~8..HEAD | rg '^(appV2\.4/appv24/(agent/agent\.py|agent/agent_loop\.py|agent/types\.py|ai/types\.py|ai/stream\.py|ai/validation\.py|compaction/compressor\.py|compaction/timing\.py|coding_agent/session_store\.py|app\.py))$'
```

Expected:

```text
no output
```

- [ ] **Step 7: Run live TUI workflow profile validation smoke**

Start:

```bash
APPV24_WORKER_LLM_ENABLED=true APPV24_MODEL_CATALOG_STARTUP_FETCH=true PYTHONPATH=appV2.4 .venv/bin/python -m appv24.cli --cwd tmp/appv24_tui_workflow_draft_validation --dotenv .env --profile workflow-designer-lite --provider openrouter --model xiaomi/mimo-v2.5-pro --thinking off --temperature 0.2 --max-tokens 2500 --timeout-seconds 120
```

Prompt:

```text
Design a passive appv24 workflow package for weekday GitHub dependency alert triage. Include GitHub issue and Telegram notification as design targets only. Use labeled fenced blocks for workflow.yaml, connectors.yaml, permissions.yaml, dry_run_fixtures/happy_path.yaml, dry_run_fixtures/rejection_path.yaml, and rollback.md.
```

Expected:

```text
TUI shows xiaomi/mimo-v2.5-pro
assistant drafts workflow package blocks
TUI renders Workflow draft validation: passed or a concrete failed issue list
no files are created unless /workflow-save is used
```

- [ ] **Step 8: Commit Task 8**

Run:

```bash
git add docs/appv24/workflow-package-validator.md
git commit -m "docs(appv24): document live workflow draft validation"
```

## Plan Self-Review

Spec coverage:

- Live draft extraction: Tasks 2 and 3.
- In-memory validation: Task 1 and Task 3.
- Profile prompt contract: Task 4.
- CLI report: Task 5.
- TUI report: Task 6.
- Explicit save after validation: Task 7.
- Docs and verification: Task 8.
- No execution/API/cloud/secrets: covered by scope boundaries, prompt contract, and tests.

Placeholder scan:

- No `TBD`, `TODO`, or unspecified implementation steps are intentionally left.

Type consistency:

- `WorkflowDraftBlocks`, `WorkflowDraftExtraction`, `WorkflowDraftParseResult`, and `WorkflowDraftValidationResult` are introduced before use.
- `validate_workflow_documents()` is introduced before `validate_workflow_draft_text()`.
- `save_workflow_draft_blocks()` uses `WorkflowDraftBlocks`, which Task 2 defines.

Risk notes:

- TUI tests may need local helper adaptation because `test_tui.py` already has its own fake app style. Keep changes narrow and follow existing helper patterns.
- If `InteractiveMode` command dispatch is less direct than shown, preserve existing slash-command structure and add `/workflow-save` at the smallest existing branch point.
- Do not solve auto-repair in this phase. Invalid drafts should surface issues and let the user ask for repair.
