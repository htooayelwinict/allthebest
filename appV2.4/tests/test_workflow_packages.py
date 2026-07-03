from __future__ import annotations

from pathlib import Path

import pytest

from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.loader import load_package_documents


def test_workflow_validator_dependencies_are_importable() -> None:
    import yaml
    from jsonschema import Draft202012Validator

    assert yaml.safe_load("enabled: true\n") == {"enabled": True}
    assert Draft202012Validator.META_SCHEMA["$id"].endswith("/draft/2020-12/schema")


def write_minimal_package(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow.yaml").write_text(
        """
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved issue.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
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
""".lstrip(),
        encoding="utf-8",
    )
    (root / "connectors.yaml").write_text(
        """
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
""".lstrip(),
        encoding="utf-8",
    )
    (root / "permissions.yaml").write_text(
        """
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
""".lstrip(),
        encoding="utf-8",
    )
    fixtures = root / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  repo: owner/project\n", encoding="utf-8")
    (fixtures / "rejection_path.yaml").write_text("approval:\n  human-review: rejected\n", encoding="utf-8")
    (root / "rollback.md").write_text("# Rollback\n\nClose the drafted issue if it was created.\n", encoding="utf-8")


def test_load_package_documents_reads_required_files(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    documents = load_package_documents(package)

    assert documents.root == package.resolve()
    assert documents.workflow["name"] == "dependency-security-review"
    assert documents.connectors["connectors"][0]["id"] == "github"
    assert documents.permissions["approvals"][0]["id"] == "human-review"
    assert sorted(documents.dry_run_fixtures) == ["happy_path.yaml", "rejection_path.yaml"]
    assert documents.rollback_text and "Rollback" in documents.rollback_text


def test_load_package_documents_rejects_missing_required_file(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "workflow.yaml").write_text("version: appv24.workflow/v1\n", encoding="utf-8")
    (package / "connectors.yaml").write_text("version: appv24.connectors/v1\n", encoding="utf-8")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "missing required file: permissions.yaml" in str(excinfo.value)


def test_load_package_documents_rejects_invalid_utf8_in_workflow(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "workflow.yaml").write_bytes(b"\xff")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "failed to parse workflow.yaml" in str(excinfo.value)


def test_load_package_documents_rejects_invalid_utf8_in_rollback(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").write_bytes(b"\xff")

    with pytest.raises(WorkflowPackageError) as excinfo:
        load_package_documents(package)

    assert "failed to read rollback.md" in str(excinfo.value)
