"""Extract appv24 workflow package drafts from assistant Markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
import uuid

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


class WorkflowDraftSaveError(Exception):
    """Raised when a workflow draft cannot be safely saved to disk."""


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


def save_workflow_draft_blocks(blocks: WorkflowDraftBlocks, *, cwd: str | Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise WorkflowDraftSaveError("workflow draft save path must be a relative path")

    cwd_path = Path(cwd).expanduser().resolve()
    target_path = (cwd_path / relative).resolve()
    try:
        target_path.relative_to(cwd_path)
    except ValueError as error:
        raise WorkflowDraftSaveError("workflow draft save path must stay inside cwd") from error

    parsed = parse_workflow_draft_blocks(blocks)
    if parsed.documents is None:
        messages = "; ".join(issue.message for issue in parsed.issues) or "workflow draft is not parseable"
        raise WorkflowDraftSaveError(f"workflow draft is not parseable: {messages}")
    documents = parsed.documents

    planned_paths = [
        target_path,
        target_path / "workflow.yaml",
        target_path / "connectors.yaml",
        target_path / "permissions.yaml",
    ]
    if documents.dry_run_fixtures:
        planned_paths.append(target_path / "dry_run_fixtures")
    planned_paths.extend(
        target_path / "dry_run_fixtures" / Path(filename).name for filename in documents.dry_run_fixtures
    )
    if blocks.rollback_text is not None:
        planned_paths.append(target_path / "rollback.md")

    existing_paths = [path for path in planned_paths if path.exists()]
    if existing_paths:
        first = existing_paths[0]
        raise WorkflowDraftSaveError(f"workflow draft output file already exists: {first}")

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WorkflowDraftSaveError(f"failed to prepare workflow draft save directory: {error}") from error

    staging_path = _create_staging_directory(target_path.parent)
    try:
        _write_text_exclusive(
            staging_path / "workflow.yaml",
            yaml.safe_dump(documents.workflow, sort_keys=False),
        )
        _write_text_exclusive(
            staging_path / "connectors.yaml",
            yaml.safe_dump(documents.connectors, sort_keys=False),
        )
        _write_text_exclusive(
            staging_path / "permissions.yaml",
            yaml.safe_dump(documents.permissions, sort_keys=False),
        )

        if documents.dry_run_fixtures:
            fixture_dir = staging_path / "dry_run_fixtures"
            fixture_dir.mkdir(parents=True, exist_ok=False)
            for filename, document in documents.dry_run_fixtures.items():
                _write_text_exclusive(
                    fixture_dir / Path(filename).name,
                    yaml.safe_dump(document, sort_keys=False),
                )

        if blocks.rollback_text is not None:
            rollback_text = blocks.rollback_text.rstrip("\r\n") + "\n"
            _write_text_exclusive(staging_path / "rollback.md", rollback_text)

        if target_path.exists():
            raise WorkflowDraftSaveError(f"workflow draft output file already exists: {target_path}")
        try:
            staging_path.rename(target_path)
        except OSError as error:
            if target_path.exists():
                raise WorkflowDraftSaveError(f"workflow draft output file already exists: {target_path}") from error
            raise WorkflowDraftSaveError(f"failed to publish workflow draft: {error}") from error
    except WorkflowDraftSaveError:
        _remove_tree(staging_path)
        raise
    except OSError as error:
        _remove_tree(staging_path)
        raise WorkflowDraftSaveError(str(error)) from error

    return target_path


def _create_staging_directory(parent: Path) -> Path:
    for _ in range(10):
        staging_path = parent / f".workflow-draft-save-{uuid.uuid4().hex}"
        try:
            staging_path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as error:
            raise WorkflowDraftSaveError(f"failed to create workflow draft staging directory: {error}") from error
        return staging_path
    raise WorkflowDraftSaveError("failed to create workflow draft staging directory")


def _write_text_exclusive(path: Path, text: str) -> None:
    if path.exists():
        raise WorkflowDraftSaveError(f"workflow draft output file already exists: {path}")
    path.write_text(text, encoding="utf-8")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except OSError:
        return


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
