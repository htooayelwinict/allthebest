"""Extract appv24 workflow package drafts from assistant Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.loader import PackageDocuments

REQUIRED_BLOCKS = (
    ("workflow.yaml", "workflow"),
    ("connectors.yaml", "connectors"),
    ("permissions.yaml", "permissions"),
)

FENCE_START_RE = re.compile(r"^[ \t]*(?P<fence>`{3,})(?P<info>[^\r\n]*)\r?\n?$")
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}\s+(?P<title>.*?)\s*#*\s*$")


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
    workflow = ""
    connectors = ""
    permissions = ""
    dry_run_fixtures: dict[str, str] = {}
    rollback_text: str | None = None
    issues: list[WorkflowIssue] = []
    seen: set[str] = set()

    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        start_match = FENCE_START_RE.match(lines[index])
        if start_match is None:
            index += 1
            continue

        fence = start_match.group("fence")
        close_index = _find_closing_fence(lines, index + 1, len(fence))
        if close_index is None:
            index += 1
            continue

        body = "".join(lines[index + 1 : close_index])
        filename = _filename_from_info(start_match.group("info"))
        if filename is None:
            filename = _filename_from_heading(lines[index - 1]) if index > 0 else None
        if filename is None:
            filename = _filename_from_first_comment(body)

        if filename is not None:
            if filename in seen:
                issues.append(
                    WorkflowIssue(
                        code="duplicate_block",
                        file=filename,
                        path=(),
                        message=f"duplicate workflow draft block: {filename}",
                    )
                )
            else:
                seen.add(filename)
                if filename == "workflow.yaml":
                    workflow = body
                elif filename == "connectors.yaml":
                    connectors = body
                elif filename == "permissions.yaml":
                    permissions = body
                elif filename == "rollback.md":
                    rollback_text = body
                elif filename.startswith("dry_run_fixtures/"):
                    dry_run_fixtures[filename] = body

        index = close_index + 1

    return WorkflowDraftExtraction(
        blocks=WorkflowDraftBlocks(
            workflow=workflow,
            connectors=connectors,
            permissions=permissions,
            dry_run_fixtures=dry_run_fixtures,
            rollback_text=rollback_text,
        ),
        issues=tuple(issues),
    )


def parse_workflow_draft_blocks(blocks: WorkflowDraftBlocks) -> WorkflowDraftParseResult:
    issues: list[WorkflowIssue] = []
    parsed_required: dict[str, Mapping[str, Any]] = {}

    for filename, attribute in REQUIRED_BLOCKS:
        content = getattr(blocks, attribute)
        if not content.strip():
            issues.append(_missing_block_issue(filename))
            continue
        parsed = _parse_mapping(filename, content)
        if isinstance(parsed, WorkflowIssue):
            issues.append(parsed)
            continue
        if parsed is None:
            issues.append(_missing_block_issue(filename))
            continue
        parsed_required[filename] = parsed

    parsed_fixtures: dict[str, Mapping[str, Any]] = {}
    for filename, content in blocks.dry_run_fixtures.items():
        normalized = _normalize_supported_filename(filename) or filename
        parsed = _parse_mapping(normalized, content)
        if isinstance(parsed, WorkflowIssue):
            issues.append(parsed)
            continue
        if parsed is None:
            issues.append(
                WorkflowIssue(
                    code="invalid_document",
                    file=normalized,
                    path=(),
                    message=f"{normalized} must contain a mapping document",
                )
            )
            continue
        parsed_fixtures[_base_fixture_filename(normalized)] = parsed

    if issues:
        return WorkflowDraftParseResult(documents=None, issues=tuple(issues))

    return WorkflowDraftParseResult(
        documents=PackageDocuments(
            root=Path("<workflow-draft>"),
            workflow=parsed_required["workflow.yaml"],
            connectors=parsed_required["connectors.yaml"],
            permissions=parsed_required["permissions.yaml"],
            dry_run_fixtures=parsed_fixtures,
            rollback_text=blocks.rollback_text,
        )
    )


def _find_closing_fence(lines: list[str], start: int, opening_length: int) -> int | None:
    close_re = re.compile(rf"^[ \t]*`{{{opening_length},}}[ \t]*\r?\n?$")
    for index in range(start, len(lines)):
        if close_re.match(lines[index]):
            return index
    return None


def _filename_from_info(info: str) -> str | None:
    for token in info.strip().split():
        filename = _normalize_supported_filename(token)
        if filename is not None:
            return filename
    return None


def _filename_from_heading(line: str) -> str | None:
    match = HEADING_RE.match(line.rstrip("\r\n"))
    if match is None:
        return None
    title = match.group("title").strip()
    filename = _normalize_supported_filename(title)
    if filename is not None:
        return filename
    tokens = title.split()
    if not tokens:
        return None
    return _normalize_supported_filename(tokens[0])


def _filename_from_first_comment(body: str) -> str | None:
    lines = body.splitlines()
    if not lines:
        return None
    first_line = lines[0].strip()
    if not first_line.startswith("#"):
        return None
    comment = first_line[1:].strip()
    if comment.lower().startswith("file:"):
        comment = comment[5:].strip()
    return _normalize_supported_filename(comment)


def _normalize_supported_filename(value: str) -> str | None:
    candidate = value.strip().strip("`'\"")
    if candidate in {"workflow.yaml", "workflow.yml"}:
        return "workflow.yaml"
    if candidate in {"connectors.yaml", "connectors.yml"}:
        return "connectors.yaml"
    if candidate in {"permissions.yaml", "permissions.yml"}:
        return "permissions.yaml"
    if candidate == "rollback.md":
        return "rollback.md"

    parts = candidate.split("/")
    if len(parts) != 2 or parts[0] != "dry_run_fixtures":
        return None

    basename = parts[1]
    if basename in {"", ".", ".."}:
        return None
    if basename.endswith(".yaml"):
        return f"dry_run_fixtures/{basename}"
    if basename.endswith(".yml"):
        return f"dry_run_fixtures/{basename.removesuffix('.yml')}.yaml"
    return None


def _parse_mapping(filename: str, content: str) -> Mapping[str, Any] | WorkflowIssue | None:
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as error:
        return WorkflowIssue(
            code="parse_error",
            file=filename,
            path=(),
            message=f"failed to parse {filename}: {error}",
        )

    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        return WorkflowIssue(
            code="invalid_document",
            file=filename,
            path=(),
            message=f"{filename} must contain a mapping document",
        )
    return parsed


def _missing_block_issue(filename: str) -> WorkflowIssue:
    return WorkflowIssue(
        code="missing_block",
        file=filename,
        path=(),
        message=f"missing required workflow draft block: {filename}",
    )


def _base_fixture_filename(filename: str) -> str:
    if filename.startswith("dry_run_fixtures/"):
        return filename.removeprefix("dry_run_fixtures/")
    return filename
