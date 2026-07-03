"""Public workflow package validator API."""

from __future__ import annotations

from pathlib import Path

from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult, sorted_issues
from appv24.workflows.loader import load_package_documents
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.semantic import validate_semantics


def validate_workflow_package(path: str | Path) -> WorkflowValidationResult:
    root = Path(path).expanduser()
    try:
        documents = load_package_documents(root)
    except WorkflowPackageError as error:
        return WorkflowValidationResult(
            root=root.resolve(),
            issues=(
                WorkflowIssue(
                    code="load_error",
                    file=str(root),
                    path=(),
                    message=str(error),
                ),
            ),
        )

    issues = [*validate_schema_documents(documents), *validate_semantics(documents)]
    return WorkflowValidationResult(root=documents.root, issues=sorted_issues(issues))
