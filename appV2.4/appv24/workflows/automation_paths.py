"""Path selection helpers for guided workflow automation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from appv24.workflows.validator import WorkflowDraftValidationResult


def suggest_workflow_package_relative_path(
    validation: WorkflowDraftValidationResult,
    *,
    cwd: str | Path,
    fallback: str = "workflow-package",
) -> str:
    base = slugify_workflow_package_name(_workflow_name(validation), fallback=fallback)
    cwd_path = Path(cwd).expanduser().resolve()
    candidate = base
    suffix = 2
    while (cwd_path / candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def slugify_workflow_package_name(value: Any, *, fallback: str = "workflow-package") -> str:
    text = str(value or "").strip().lower()
    parts = re.findall(r"[a-z0-9]+", text)
    slug = "-".join(parts).strip("-")
    if not slug:
        slug = fallback
    return slug[:80].strip("-") or fallback


def _workflow_name(validation: WorkflowDraftValidationResult) -> Any:
    documents = validation.documents
    if documents is None or not isinstance(documents.workflow, Mapping):
        return None
    return documents.workflow.get("name")
