from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.schema import validate_schema_documents
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.semantic import validate_semantics
from appv24.workflows import WorkflowValidationResult, validate_workflow_package


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


def replace_documents(
    documents: PackageDocuments,
    *,
    workflow: object | None = None,
    connectors: object | None = None,
    permissions: object | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=documents.root,
        workflow=documents.workflow if workflow is None else workflow,
        connectors=documents.connectors if connectors is None else connectors,
        permissions=documents.permissions if permissions is None else permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )


def assign_path_value(document: object, path: tuple[str | int, ...], value: object) -> None:
    target = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def assert_has_issue(
    issues: list,
    *,
    file: str,
    code: str = "schema",
    path: tuple[str | int, ...] | None = None,
    location: str | None = None,
    message_substring: str | None = None,
) -> None:
    matches = [issue for issue in issues if issue.file == file and issue.code == code]
    if path is not None:
        matches = [issue for issue in matches if issue.path == path]
    if location is not None:
        matches = [issue for issue in matches if issue.location == location]
    if message_substring is not None:
        matches = [issue for issue in matches if message_substring in issue.message]
    assert matches, f"expected issue not found: file={file!r} code={code!r} path={path!r} location={location!r}"


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


def test_schema_validation_accepts_minimal_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    documents = load_package_documents(package)
    issues = validate_schema_documents(documents)

    assert issues == []


def test_schema_validation_rejects_missing_workflow_entrypoint(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow.pop("entrypoint")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(
        issues,
        file="workflow.yaml",
        path=("entrypoint",),
        location="workflow.yaml:entrypoint",
        message_substring="'entrypoint' is a required property",
    )


@pytest.mark.parametrize(
    ("attribute", "filename", "expected_version"),
    [
        ("workflow", "workflow.yaml", "appv24.workflow/v1"),
        ("connectors", "connectors.yaml", "appv24.connectors/v1"),
        ("permissions", "permissions.yaml", "appv24.permissions/v1"),
    ],
)
def test_schema_validation_rejects_invalid_version_literal(
    tmp_path: Path, attribute: str, filename: str, expected_version: str
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    document = deepcopy(getattr(documents, attribute))
    document["version"] = "appv24.invalid/v999"

    issues = validate_schema_documents(replace_documents(documents, **{attribute: document}))

    assert_has_issue(issues, file=filename, path=("version",))
    assert any(issue.file == filename and expected_version in issue.message for issue in issues)


def test_schema_validation_rejects_connector_action_missing_id(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0].pop("id")

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(
        issues,
        file="connectors.yaml",
        path=("connectors", 0, "actions", 0, "id"),
        message_substring="'id' is a required property",
    )


@pytest.mark.parametrize(
    ("attribute", "filename", "path"),
    [
        ("workflow", "workflow.yaml", ("triggers",)),
        ("workflow", "workflow.yaml", ("steps",)),
        ("connectors", "connectors.yaml", ("connectors",)),
        ("connectors", "connectors.yaml", ("connectors", 0, "actions")),
    ],
)
def test_schema_validation_rejects_empty_required_collections(
    tmp_path: Path, attribute: str, filename: str, path: tuple[str | int, ...]
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    document = deepcopy(getattr(documents, attribute))
    assign_path_value(document, path, [])

    issues = validate_schema_documents(replace_documents(documents, **{attribute: document}))

    assert_has_issue(issues, file=filename, path=path)


def test_schema_validation_accepts_permissions_without_approvals(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    permissions = deepcopy(documents.permissions)
    permissions.pop("approvals")

    issues = validate_schema_documents(replace_documents(documents, permissions=permissions))

    assert issues == []


def test_schema_validation_accepts_permission_without_scopes(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    permissions = deepcopy(documents.permissions)
    permissions["permissions"][0].pop("scopes")

    issues = validate_schema_documents(replace_documents(documents, permissions=permissions))

    assert issues == []


@pytest.mark.parametrize("path", [("name",), ("entrypoint",)])
def test_schema_validation_rejects_empty_workflow_names(
    tmp_path: Path, path: tuple[str | int, ...]
) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    assign_path_value(workflow, path, "")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=path)


def test_schema_validation_rejects_invalid_trigger_type(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["triggers"][0]["type"] = "polling"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("triggers", 0, "type"))


def test_schema_validation_rejects_invalid_step_type(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["type"] = "loop"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("steps", 0, "type"))


def test_schema_validation_rejects_non_dotted_step_uses(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["uses"] = "github"

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow))

    assert_has_issue(issues, file="workflow.yaml", path=("steps", 0, "uses"))


def test_schema_validation_rejects_invalid_connector_adapter(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["adapter"] = "sdk"

    issues = validate_schema_documents(replace_documents(documents, connectors=connectors))

    assert_has_issue(issues, file="connectors.yaml", path=("connectors", 0, "adapter"))


def test_schema_validation_returns_issues_in_deterministic_order(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)
    workflow = deepcopy(documents.workflow)
    workflow["version"] = "appv24.invalid/v999"
    workflow.pop("entrypoint")
    connectors = deepcopy(documents.connectors)
    connectors["connectors"][0]["actions"][0].pop("id")

    issues = validate_schema_documents(replace_documents(documents, workflow=workflow, connectors=connectors))

    expected = sorted(
        issues,
        key=lambda issue: (issue.file, tuple(str(part) for part in issue.path), issue.code, issue.message),
    )
    assert issues == expected


def test_semantic_validation_rejects_unknown_transition_target(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("next: done", "next: missing_step", 1),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(
        issue.code == "unknown_transition" and issue.message == "step draft_issue points to missing_step"
        for issue in issues
    )


def test_semantic_validation_rejects_unknown_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "unknown_action" and "github.close_issue" in issue.message for issue in issues)


def test_semantic_validation_requires_approval_for_mutation(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("    approval: human-review\n", ""),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_approval" and "draft_issue" in issue.message for issue in issues)


def test_semantic_validation_rejects_raw_secret_values(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8")
        + "\ncredentials:\n  api_token: sk-or-v1-this-value-must-not-be-inline\n",
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "raw_secret" and issue.file == "connectors.yaml" for issue in issues)


def test_semantic_validation_requires_rollback_and_fixtures_for_mutation(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "rollback.md").unlink()
    (package / "dry_run_fixtures" / "rejection_path.yaml").unlink()

    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_rollback" for issue in issues)
    assert any(issue.code == "missing_fixture" and "rejection_path.yaml" in issue.message for issue in issues)


def test_semantic_validation_requires_approval_to_cover_mutating_step(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    permissions_path = package / "permissions.yaml"
    permissions_path.write_text(
        permissions_path.read_text(encoding="utf-8").replace("      - draft_issue", "      - other_step"),
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "missing_approval" and "draft_issue" in issue.message for issue in issues)


def test_semantic_validation_requires_permission_scopes_for_connector_action(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8").replace(
            "          - issues:write\n",
            "          - issues:write\n          - contents:write\n",
        ),
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert any(
        issue.code == "permission_missing_scope" and "create_issue" in issue.message and "contents:write" in issue.message
        for issue in issues
    )


def test_semantic_validation_allows_token_url_metadata(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    connectors_path = package / "connectors.yaml"
    connectors_path.write_text(
        connectors_path.read_text(encoding="utf-8") + "\noauth:\n  token_url: https://example.test/oauth/token\n",
        encoding="utf-8",
    )

    issues = validate_semantics(load_package_documents(package))

    assert not any(issue.code == "raw_secret" for issue in issues)


def test_validate_workflow_package_returns_ok_result_for_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    result = validate_workflow_package(package)

    assert isinstance(result, WorkflowValidationResult)
    assert result.ok is True
    assert result.issues == ()


def test_validate_workflow_package_skips_semantics_when_schema_issues_exist(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8")
        .replace("entrypoint: inspect_alerts\n", "")
        .replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert any(issue.code == "schema" for issue in result.issues)
    assert not any(issue.code == "unknown_action" for issue in result.issues)
    assert all(issue.code == "schema" for issue in result.issues)
    assert result.messages() == sorted(result.messages())


def test_validate_workflow_package_collects_semantic_issues_for_schema_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("github.create_issue", "github.close_issue"),
        encoding="utf-8",
    )

    result = validate_workflow_package(package)

    assert result.ok is False
    assert any(issue.code == "unknown_action" for issue in result.issues)
    assert result.messages() == sorted(result.messages())


def test_validate_workflow_package_returns_schema_issue_for_structurally_invalid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    (package / "connectors.yaml").write_text(
        """
version: appv24.connectors/v1
connectors: null
""".lstrip(),
        encoding="utf-8",
    )

    try:
        result = validate_workflow_package(package)
    except Exception as error:
        pytest.fail(f"validate_workflow_package raised {error!r}")

    assert isinstance(result, WorkflowValidationResult)
    assert result.ok is False
    assert any(issue.code == "schema" for issue in result.issues)
    assert all(issue.code == "schema" for issue in result.issues)


def test_validate_workflow_package_load_error_uses_resolved_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = validate_workflow_package(Path("missing-package"))

    assert result.ok is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.code == "load_error"
    assert issue.file == str(result.root)
