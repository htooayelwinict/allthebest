"""Workflow package validation errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class WorkflowPackageError(Exception):
    """Raised when a workflow package cannot be loaded."""


@dataclass(frozen=True)
class WorkflowIssue:
    code: str
    file: str
    path: tuple[str | int, ...]
    message: str

    @property
    def location(self) -> str:
        if not self.path:
            return self.file
        rendered = ".".join(str(part) for part in self.path)
        return f"{self.file}:{rendered}"


@dataclass(frozen=True)
class WorkflowValidationResult:
    root: Path
    issues: tuple[WorkflowIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def messages(self) -> list[str]:
        return [f"{issue.code} {issue.location}: {issue.message}" for issue in self.issues]

    def raise_for_errors(self) -> None:
        if self.issues:
            raise WorkflowPackageError("\n".join(self.messages()))


def sorted_issues(issues: Iterable[WorkflowIssue]) -> tuple[WorkflowIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda issue: (issue.file, tuple(str(part) for part in issue.path), issue.code, issue.message),
        )
    )
