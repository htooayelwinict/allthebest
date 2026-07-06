"""Workflow repair scoping helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def workflow_issue_fingerprint(issue: Any) -> str:
    code = str(getattr(issue, "code", "unknown_issue"))
    file_name = str(getattr(issue, "file", "<unknown>"))
    location = getattr(issue, "location", None)
    if location is None:
        path = getattr(issue, "path", ())
        if path:
            location = ".".join(str(part) for part in path)
        else:
            location = file_name
    else:
        location = str(location)
        prefix = f"{file_name}:"
        if location.startswith(prefix):
            location = location[len(prefix) :]
    return f"{code}:{file_name}:{location}"


def workflow_validation_fingerprint(validation_result: Any) -> tuple[str, ...]:
    issues = getattr(validation_result, "issues", ())
    return tuple(workflow_issue_fingerprint(issue) for issue in issues)


def repeated_validation_count(history: Iterable[tuple[str, ...]], current: tuple[str, ...]) -> int:
    return sum(1 for prior in history if prior == current)
