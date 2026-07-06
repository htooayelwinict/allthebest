"""Shared workflow bundle freshness checks for live execution gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.bundle_export import check_workflow_bundle


@dataclass(frozen=True)
class WorkflowBundleGuardReport:
    status: str
    bundle_path: Path
    issues: tuple[str, ...]


def inspect_default_workflow_bundle(package_path: str | Path) -> WorkflowBundleGuardReport:
    """Inspect the default package bundle without requiring one to exist."""

    package_root = Path(package_path).expanduser().resolve()
    bundle_path = package_root / ".workflow-bundle"
    if not (bundle_path / "bundle.manifest.json").exists():
        return WorkflowBundleGuardReport("missing", bundle_path, ())
    report = check_workflow_bundle(bundle_path)
    if report.ok:
        return WorkflowBundleGuardReport("passed", bundle_path, ())
    issues = tuple(f"workflow bundle check failed: {issue}" for issue in report.issues)
    return WorkflowBundleGuardReport("failed", bundle_path, issues)
