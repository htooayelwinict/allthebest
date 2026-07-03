"""Public workflow package validator API."""

from __future__ import annotations

from pathlib import Path

from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult, sorted_issues
from appv24.workflows.loader import load_package_documents
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.semantic import validate_semantics


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

    schema_issues = validate_schema_documents(documents)
    if schema_issues:
        return WorkflowValidationResult(root=documents.root, issues=sorted_issues(schema_issues))

    return WorkflowValidationResult(root=documents.root, issues=sorted_issues(validate_semantics(documents)))
