"""Public workflow package validator API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.drafts import WorkflowDraftBlocks, extract_workflow_draft_blocks, parse_workflow_draft_blocks
from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult, sorted_issues
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.middleware_contracts import validate_middleware_contracts
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.semantic import validate_semantics


@dataclass(frozen=True)
class WorkflowDraftValidationResult:
    blocks: WorkflowDraftBlocks
    documents: PackageDocuments | None
    validation: WorkflowValidationResult

    @property
    def ok(self) -> bool:
        return self.validation.ok


def validate_workflow_documents(documents: PackageDocuments) -> WorkflowValidationResult:
    schema_issues = validate_schema_documents(documents)
    if schema_issues:
        return WorkflowValidationResult(root=documents.root, issues=sorted_issues(schema_issues))

    return WorkflowValidationResult(
        root=documents.root,
        issues=sorted_issues((*validate_semantics(documents), *validate_middleware_contracts(documents))),
    )


def validate_workflow_draft_text(text: str) -> WorkflowDraftValidationResult:
    extraction = extract_workflow_draft_blocks(text)
    parsed = parse_workflow_draft_blocks(extraction.blocks)

    if parsed.documents is None:
        return WorkflowDraftValidationResult(
            blocks=extraction.blocks,
            documents=None,
            validation=WorkflowValidationResult(
                root=Path("<workflow-draft>"),
                issues=sorted_issues((*extraction.issues, *parsed.issues)),
            ),
        )

    validation = validate_workflow_documents(parsed.documents)
    return WorkflowDraftValidationResult(
        blocks=extraction.blocks,
        documents=parsed.documents,
        validation=WorkflowValidationResult(
            root=parsed.documents.root,
            issues=sorted_issues((*extraction.issues, *validation.issues)),
        ),
    )


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
