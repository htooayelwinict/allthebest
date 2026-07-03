# appv24 Workflow Validator v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline appv24 workflow package validator that checks AI-authored workflow-as-code packages before any runner, cloud executor, or external integration exists.

**Architecture:** Keep the validator beside the runtime under `appV2.4/appv24/workflows/`. The validator loads a package directory, runs JSON Schema Draft 2020-12 structural checks, then runs Python semantic checks for graph integrity, connector references, permissions, approvals, raw secrets, dry-run fixtures, and rollback requirements. v1 exposes a Python API only; the CLI command is deferred to keep yellow-zone `cli.py` untouched until the package contract is stable.

**Tech Stack:** Python 3.13, `PyYAML.safe_load`, `jsonschema.Draft202012Validator`, existing pytest suite, appv24 green-zone package/tests/docs.

---

## Product Boundary

This plan keeps appv24 as a separate product line. It does not merge into `main`, does not touch appv23/appv231, and does not publish npm or GHCR.

Red-zone files are not touched:

```text
appV2.4/appv24/agent/agent.py
appV2.4/appv24/agent/agent_loop.py
appV2.4/appv24/agent/types.py
appV2.4/appv24/ai/types.py
appV2.4/appv24/ai/stream.py
appV2.4/appv24/ai/validation.py
appV2.4/appv24/compaction/compressor.py
appV2.4/appv24/compaction/timing.py
appV2.4/appv24/coding_agent/session_store.py
appV2.4/appv24/app.py
```

Yellow-zone `appV2.4/appv24/cli.py` is also not touched in v1. A CLI command such as `appv24 --validate-workflow-package PATH` can be planned after the API and package schema prove stable.

## External Documentation Findings Used

- `jsonschema` supports `Draft202012Validator.check_schema(schema)` and `Draft202012Validator(schema).iter_errors(instance)` for collecting all validation errors with paths.
- `PyYAML` recommends `yaml.safe_load()` for untrusted YAML because it constructs only simple Python objects.
- Pydantic v2 remains available in appv24, but v1 does not need it. JSON Schema plus focused semantic checks are simpler for this contract.
- OpenAPI, AsyncAPI, CloudEvents, MCP, Serverless Workflow, and LangGraph remain adapter/export/runtime references, not the native v1 package schema.

## File Structure

Create:

```text
appV2.4/appv24/workflows/__init__.py
appV2.4/appv24/workflows/errors.py
appV2.4/appv24/workflows/loader.py
appV2.4/appv24/workflows/schema.py
appV2.4/appv24/workflows/semantic.py
appV2.4/appv24/workflows/validator.py
appV2.4/tests/test_workflow_packages.py
docs/appv24/workflow-package-validator.md
examples/appv24/workflows/dependency-security-review/
```

Modify:

```text
appV2.4/pyproject.toml
appV2.4/uv.lock
```

## Task 1: Dependencies And Package Shell

**Files:**
- Modify: `appV2.4/pyproject.toml`
- Modify: `appV2.4/uv.lock`
- Create: `appV2.4/appv24/workflows/__init__.py`
- Create: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Write the failing dependency smoke test**

Create `appV2.4/tests/test_workflow_packages.py`:

```python
from __future__ import annotations


def test_workflow_validator_dependencies_are_importable() -> None:
    import yaml
    from jsonschema import Draft202012Validator

    assert yaml.safe_load("enabled: true\n") == {"enabled": True}
    assert Draft202012Validator.META_SCHEMA["$id"].endswith("/draft/2020-12/schema")
```

- [ ] **Step 2: Run the dependency test and verify it fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_workflow_validator_dependencies_are_importable -q
```

Expected:

```text
FAIL or ERROR because jsonschema is not installed as an appv24 dependency
```

- [ ] **Step 3: Add direct validator dependencies**

Modify `appV2.4/pyproject.toml` dependencies to include direct dependencies for the validator:

```toml
dependencies = [
  "httpx>=0.27",
  "jsonschema>=4.25",
  "langgraph>=0.6.0",
  "openrouter>=0.9.1",
  "pydantic>=2.0",
  "PyYAML>=6.0",
]
```

- [ ] **Step 4: Refresh the appv24 lockfile**

Run:

```bash
uv lock --project appV2.4
```

Expected:

```text
Resolved ... packages
```

- [ ] **Step 5: Create the workflow package shell**

Create `appV2.4/appv24/workflows/__init__.py`:

```python
"""Offline workflow package validation for appv24."""

from __future__ import annotations

__all__: tuple[str, ...] = ()
```

- [ ] **Step 6: Run the dependency test and verify it passes**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_workflow_validator_dependencies_are_importable -q
```

Expected:

```text
1 passed
```

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add appV2.4/pyproject.toml appV2.4/uv.lock appV2.4/appv24/workflows/__init__.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): add workflow validator dependencies"
```

## Task 2: Loader And Error Types

**Files:**
- Create: `appV2.4/appv24/workflows/errors.py`
- Create: `appV2.4/appv24/workflows/loader.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add loader tests**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
from pathlib import Path

import pytest

from appv24.workflows.errors import WorkflowPackageError
from appv24.workflows.loader import load_package_documents


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
```

- [ ] **Step 2: Run the loader tests and verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_load_package_documents_reads_required_files appV2.4/tests/test_workflow_packages.py::test_load_package_documents_rejects_missing_required_file -q
```

Expected:

```text
ERROR because appv24.workflows.errors and appv24.workflows.loader do not exist
```

- [ ] **Step 3: Add error and result types**

Create `appV2.4/appv24/workflows/errors.py`:

```python
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
```

- [ ] **Step 4: Add package loader**

Create `appV2.4/appv24/workflows/loader.py`:

```python
"""Load appv24 workflow package documents from disk."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from appv24.workflows.errors import WorkflowPackageError

REQUIRED_PACKAGE_FILES = ("workflow.yaml", "connectors.yaml", "permissions.yaml")


@dataclass(frozen=True)
class PackageDocuments:
    root: Path
    workflow: Mapping[str, Any]
    connectors: Mapping[str, Any]
    permissions: Mapping[str, Any]
    dry_run_fixtures: Mapping[str, Mapping[str, Any]]
    rollback_text: str | None


def load_package_documents(path: str | Path) -> PackageDocuments:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise WorkflowPackageError(f"package path does not exist: {root}")
    if not root.is_dir():
        raise WorkflowPackageError(f"package path is not a directory: {root}")

    for filename in REQUIRED_PACKAGE_FILES:
        if not (root / filename).is_file():
            raise WorkflowPackageError(f"missing required file: {filename}")

    return PackageDocuments(
        root=root,
        workflow=_read_mapping(root / "workflow.yaml"),
        connectors=_read_mapping(root / "connectors.yaml"),
        permissions=_read_mapping(root / "permissions.yaml"),
        dry_run_fixtures=_read_fixtures(root / "dry_run_fixtures"),
        rollback_text=_read_optional_text(root / "rollback.md"),
    )


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        if path.suffix == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8"))
        else:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise WorkflowPackageError(f"failed to parse {path.name}: {error}") from error
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise WorkflowPackageError(f"{path.name} must contain a mapping document")
    return parsed


def _read_fixtures(path: Path) -> Mapping[str, Mapping[str, Any]]:
    if not path.exists():
        return {}
    if not path.is_dir():
        raise WorkflowPackageError("dry_run_fixtures must be a directory")
    fixtures: dict[str, Mapping[str, Any]] = {}
    for fixture_path in sorted(path.iterdir()):
        if fixture_path.name.startswith(".") or fixture_path.is_dir():
            continue
        if fixture_path.suffix not in {".yaml", ".yml", ".json"}:
            continue
        fixtures[fixture_path.name] = _read_mapping(fixture_path)
    return fixtures


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowPackageError(f"failed to read {path.name}: {error}") from error
```

- [ ] **Step 5: Run the loader tests and verify they pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_load_package_documents_reads_required_files appV2.4/tests/test_workflow_packages.py::test_load_package_documents_rejects_missing_required_file -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add appV2.4/appv24/workflows/errors.py appV2.4/appv24/workflows/loader.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): load workflow package documents"
```

## Task 3: Draft 2020-12 Structural Schemas

**Files:**
- Create: `appV2.4/appv24/workflows/schema.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add schema validation tests**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
from appv24.workflows.schema import validate_schema_documents


def test_schema_validation_accepts_minimal_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    documents = load_package_documents(package)

    issues = validate_schema_documents(documents)

    assert issues == []


def test_schema_validation_rejects_missing_workflow_entrypoint(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("entrypoint: inspect_alerts\n", ""),
        encoding="utf-8",
    )
    documents = load_package_documents(package)

    issues = validate_schema_documents(documents)

    assert any(issue.file == "workflow.yaml" and issue.code == "schema" for issue in issues)
    assert any("'entrypoint' is a required property" in issue.message for issue in issues)
```

- [ ] **Step 2: Run schema tests and verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_schema_validation_accepts_minimal_package appV2.4/tests/test_workflow_packages.py::test_schema_validation_rejects_missing_workflow_entrypoint -q
```

Expected:

```text
ERROR because appv24.workflows.schema does not exist
```

- [ ] **Step 3: Add modular JSON Schemas**

Create `appV2.4/appv24/workflows/schema.py`:

```python
"""Draft 2020-12 schemas for appv24 workflow packages."""

from __future__ import annotations

from typing import Any, Mapping

from jsonschema import Draft202012Validator

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.loader import PackageDocuments

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

WORKFLOW_SCHEMA: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/workflow.schema.json",
    "type": "object",
    "required": ["version", "name", "triggers", "entrypoint", "steps"],
    "properties": {
        "version": {"const": "appv24.workflow/v1"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "triggers": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["schedule", "webhook", "manual", "event"]},
                    "schedule": {"type": "string"},
                    "event": {"type": "object"},
                },
                "additionalProperties": True,
            },
        },
        "entrypoint": {"type": "string", "minLength": 1},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["action", "terminal", "decision", "manual_approval"]},
                    "uses": {"type": "string", "pattern": "^[A-Za-z0-9_.-]+\\.[A-Za-z0-9_.-]+$"},
                    "with": {"type": "object"},
                    "next": {"type": ["string", "null"]},
                    "branches": {"type": "object", "additionalProperties": {"type": "string"}},
                    "side_effect_class": {
                        "type": "string",
                        "enum": ["read", "create", "update", "delete", "send", "publish", "provision"],
                    },
                    "approval": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

CONNECTORS_SCHEMA: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/connectors.schema.json",
    "type": "object",
    "required": ["version", "connectors"],
    "properties": {
        "version": {"const": "appv24.connectors/v1"},
        "connectors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "adapter", "actions"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "adapter": {
                        "type": "string",
                        "enum": ["mcp", "openapi", "asyncapi", "webhook", "direct_api", "iac", "manual", "platform_export"],
                    },
                    "description": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "operationId": {"type": "string"},
                                "tool": {"type": "string"},
                                "side_effect": {"type": "boolean"},
                                "required_scopes": {"type": "array", "items": {"type": "string"}},
                            },
                            "additionalProperties": True,
                        },
                    },
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

PERMISSIONS_SCHEMA: dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/permissions.schema.json",
    "type": "object",
    "required": ["version", "permissions"],
    "properties": {
        "version": {"const": "appv24.permissions/v1"},
        "approvals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "mode", "required_for"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "mode": {"type": "string", "enum": ["explicit"]},
                    "required_for": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["connector", "actions"],
                "properties": {
                    "connector": {"type": "string", "minLength": 1},
                    "actions": {"type": "array", "items": {"type": "string"}},
                    "scopes": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


def validate_schema_documents(documents: PackageDocuments) -> list[WorkflowIssue]:
    for schema in (WORKFLOW_SCHEMA, CONNECTORS_SCHEMA, PERMISSIONS_SCHEMA):
        Draft202012Validator.check_schema(schema)
    issues: list[WorkflowIssue] = []
    issues.extend(_validate_document("workflow.yaml", WORKFLOW_SCHEMA, documents.workflow))
    issues.extend(_validate_document("connectors.yaml", CONNECTORS_SCHEMA, documents.connectors))
    issues.extend(_validate_document("permissions.yaml", PERMISSIONS_SCHEMA, documents.permissions))
    return issues


def _validate_document(filename: str, schema: Mapping[str, Any], document: Mapping[str, Any]) -> list[WorkflowIssue]:
    validator = Draft202012Validator(schema)
    return [
        WorkflowIssue(
            code="schema",
            file=filename,
            path=tuple(error.path),
            message=error.message,
        )
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    ]
```

- [ ] **Step 4: Run schema tests and verify they pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_schema_validation_accepts_minimal_package appV2.4/tests/test_workflow_packages.py::test_schema_validation_rejects_missing_workflow_entrypoint -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add appV2.4/appv24/workflows/schema.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): add workflow package schemas"
```

## Task 4: Semantic Validator

**Files:**
- Create: `appV2.4/appv24/workflows/semantic.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add semantic validation tests**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
from appv24.workflows.semantic import validate_semantics


def test_semantic_validation_rejects_unknown_transition_target(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)
    workflow_path = package / "workflow.yaml"
    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace("next: done", "next: missing_step", 1),
        encoding="utf-8",
    )
    issues = validate_semantics(load_package_documents(package))

    assert any(issue.code == "unknown_transition" and issue.message == "step draft_issue points to missing_step" for issue in issues)


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
```

- [ ] **Step 2: Run semantic tests and verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_unknown_transition_target appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_unknown_connector_action appV2.4/tests/test_workflow_packages.py::test_semantic_validation_requires_approval_for_mutation appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_raw_secret_values appV2.4/tests/test_workflow_packages.py::test_semantic_validation_requires_rollback_and_fixtures_for_mutation -q
```

Expected:

```text
ERROR because appv24.workflows.semantic does not exist
```

- [ ] **Step 3: Add semantic validator**

Create `appV2.4/appv24/workflows/semantic.py`:

```python
"""Semantic checks for appv24 workflow packages."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.loader import PackageDocuments

MUTATING_SIDE_EFFECTS = {"create", "update", "delete", "send", "publish", "provision"}
SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_]+)")


def validate_semantics(documents: PackageDocuments) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    workflow = documents.workflow
    connectors = _connector_actions(documents.connectors)
    approval_ids = _approval_ids(documents.permissions)
    permission_actions = _permission_actions(documents.permissions)
    steps = _steps(workflow)
    step_ids = [str(step.get("id", "")) for step in steps]
    known_steps = set(step_ids)

    issues.extend(_duplicate_step_issues(step_ids))
    issues.extend(_entrypoint_issues(workflow, known_steps))
    issues.extend(_transition_issues(steps, known_steps))
    issues.extend(_connector_reference_issues(steps, connectors, permission_actions))

    mutating_steps = _mutating_steps(steps, connectors)
    issues.extend(_approval_issues(mutating_steps, approval_ids))
    issues.extend(_raw_secret_issues("workflow.yaml", workflow))
    issues.extend(_raw_secret_issues("connectors.yaml", documents.connectors))
    issues.extend(_raw_secret_issues("permissions.yaml", documents.permissions))
    for fixture_name, fixture in documents.dry_run_fixtures.items():
        issues.extend(_raw_secret_issues(f"dry_run_fixtures/{fixture_name}", fixture))

    if mutating_steps:
        if not documents.rollback_text or not documents.rollback_text.strip():
            issues.append(WorkflowIssue("missing_rollback", "rollback.md", (), "mutating workflows require rollback.md"))
        for required_fixture in ("happy_path.yaml", "rejection_path.yaml"):
            if required_fixture not in documents.dry_run_fixtures:
                issues.append(
                    WorkflowIssue(
                        "missing_fixture",
                        "dry_run_fixtures",
                        (),
                        f"mutating workflows require dry_run_fixtures/{required_fixture}",
                    )
                )

    return issues


def _steps(workflow: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = workflow.get("steps")
    return [step for step in value if isinstance(step, dict)] if isinstance(value, list) else []


def _duplicate_step_issues(step_ids: list[str]) -> list[WorkflowIssue]:
    counts = Counter(step_ids)
    return [
        WorkflowIssue("duplicate_step", "workflow.yaml", ("steps",), f"duplicate step id: {step_id}")
        for step_id, count in sorted(counts.items())
        if step_id and count > 1
    ]


def _entrypoint_issues(workflow: Mapping[str, Any], known_steps: set[str]) -> list[WorkflowIssue]:
    entrypoint = workflow.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint not in known_steps:
        return [WorkflowIssue("unknown_entrypoint", "workflow.yaml", ("entrypoint",), f"entrypoint does not exist: {entrypoint}")]
    return []


def _transition_issues(steps: Iterable[Mapping[str, Any]], known_steps: set[str]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(steps):
        source = str(step.get("id", f"steps[{index}]"))
        targets: list[str] = []
        next_step = step.get("next")
        if isinstance(next_step, str):
            targets.append(next_step)
        branches = step.get("branches")
        if isinstance(branches, dict):
            targets.extend(str(target) for target in branches.values())
        for target in targets:
            if target not in known_steps:
                issues.append(
                    WorkflowIssue(
                        "unknown_transition",
                        "workflow.yaml",
                        ("steps", index),
                        f"step {source} points to {target}",
                    )
                )
    return issues


def _connector_actions(connectors_document: Mapping[str, Any]) -> dict[str, dict[str, Mapping[str, Any]]]:
    connectors: dict[str, dict[str, Mapping[str, Any]]] = {}
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, dict):
            continue
        connector_id = connector.get("id")
        if not isinstance(connector_id, str):
            continue
        actions: dict[str, Mapping[str, Any]] = {}
        for action in connector.get("actions", []):
            if isinstance(action, dict) and isinstance(action.get("id"), str):
                actions[str(action["id"])] = action
        connectors[connector_id] = actions
    return connectors


def _connector_reference_issues(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
    permission_actions: Mapping[str, set[str]],
) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(steps):
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        connector_id, _, action_id = uses.partition(".")
        if connector_id not in connectors:
            issues.append(WorkflowIssue("unknown_connector", "workflow.yaml", ("steps", index, "uses"), f"unknown connector: {connector_id}"))
            continue
        if action_id not in connectors[connector_id]:
            issues.append(WorkflowIssue("unknown_action", "workflow.yaml", ("steps", index, "uses"), f"unknown action: {uses}"))
            continue
        allowed_actions = permission_actions.get(connector_id, set())
        if action_id not in allowed_actions and "*" not in allowed_actions:
            issues.append(
                WorkflowIssue(
                    "permission_missing_action",
                    "permissions.yaml",
                    ("permissions",),
                    f"permissions for {connector_id} do not allow {action_id}",
                )
            )
    return issues


def _permission_actions(permissions_document: Mapping[str, Any]) -> dict[str, set[str]]:
    actions: dict[str, set[str]] = {}
    for permission in permissions_document.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        raw_actions = permission.get("actions", [])
        actions[connector_id] = {str(action) for action in raw_actions if isinstance(action, str)}
    return actions


def _approval_ids(permissions_document: Mapping[str, Any]) -> set[str]:
    approvals = permissions_document.get("approvals", [])
    return {str(approval["id"]) for approval in approvals if isinstance(approval, dict) and isinstance(approval.get("id"), str)}


def _mutating_steps(
    steps: Iterable[Mapping[str, Any]],
    connectors: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    mutating: list[Mapping[str, Any]] = []
    for step in steps:
        side_effect_class = step.get("side_effect_class")
        uses = step.get("uses")
        action_side_effect = False
        if isinstance(uses, str):
            connector_id, _, action_id = uses.partition(".")
            action = connectors.get(connector_id, {}).get(action_id, {})
            action_side_effect = bool(action.get("side_effect"))
        if side_effect_class in MUTATING_SIDE_EFFECTS or action_side_effect:
            mutating.append(step)
    return mutating


def _approval_issues(mutating_steps: Iterable[Mapping[str, Any]], approval_ids: set[str]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for step in mutating_steps:
        step_id = str(step.get("id", "unknown"))
        side_effect_class = step.get("side_effect_class")
        approval = step.get("approval")
        if side_effect_class not in MUTATING_SIDE_EFFECTS:
            issues.append(WorkflowIssue("missing_side_effect_class", "workflow.yaml", ("steps", step_id), f"{step_id} requires side_effect_class"))
        if not isinstance(approval, str) or approval not in approval_ids:
            issues.append(WorkflowIssue("missing_approval", "workflow.yaml", ("steps", step_id), f"{step_id} requires an explicit approval gate"))
    return issues


def _raw_secret_issues(filename: str, value: Any, path: tuple[str | int, ...] = ()) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    if isinstance(value, dict):
        if set(value) == {"secretRef"} and isinstance(value.get("secretRef"), str):
            return []
        for key, child in value.items():
            child_path = (*path, str(key))
            if SECRET_KEY_RE.search(str(key)) and isinstance(child, str):
                issues.append(WorkflowIssue("raw_secret", filename, child_path, "use secretRef instead of inline secret value"))
            issues.extend(_raw_secret_issues(filename, child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_raw_secret_issues(filename, child, (*path, index)))
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        issues.append(WorkflowIssue("raw_secret", filename, path, "inline secret-looking value is not allowed"))
    return issues
```

- [ ] **Step 4: Run semantic tests and verify they pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_unknown_transition_target appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_unknown_connector_action appV2.4/tests/test_workflow_packages.py::test_semantic_validation_requires_approval_for_mutation appV2.4/tests/test_workflow_packages.py::test_semantic_validation_rejects_raw_secret_values appV2.4/tests/test_workflow_packages.py::test_semantic_validation_requires_rollback_and_fixtures_for_mutation -q
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add appV2.4/appv24/workflows/semantic.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): validate workflow package semantics"
```

## Task 5: Public Validator API

**Files:**
- Create: `appV2.4/appv24/workflows/validator.py`
- Modify: `appV2.4/appv24/workflows/__init__.py`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add public API tests**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
from appv24.workflows import WorkflowValidationResult, validate_workflow_package


def test_validate_workflow_package_returns_ok_result_for_valid_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    write_minimal_package(package)

    result = validate_workflow_package(package)

    assert isinstance(result, WorkflowValidationResult)
    assert result.ok is True
    assert result.issues == ()


def test_validate_workflow_package_collects_schema_and_semantic_issues(tmp_path: Path) -> None:
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
    assert any(issue.code == "unknown_action" for issue in result.issues)
    assert result.messages() == sorted(result.messages())
```

- [ ] **Step 2: Run public API tests and verify they fail**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_validate_workflow_package_returns_ok_result_for_valid_package appV2.4/tests/test_workflow_packages.py::test_validate_workflow_package_collects_schema_and_semantic_issues -q
```

Expected:

```text
ERROR because appv24.workflows.validate_workflow_package is not exported
```

- [ ] **Step 3: Add validator orchestrator**

Create `appV2.4/appv24/workflows/validator.py`:

```python
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
```

- [ ] **Step 4: Export public validator API**

Replace `appV2.4/appv24/workflows/__init__.py` with:

```python
"""Offline workflow package validation for appv24."""

from __future__ import annotations

from appv24.workflows.errors import WorkflowIssue, WorkflowPackageError, WorkflowValidationResult
from appv24.workflows.validator import validate_workflow_package

__all__ = (
    "WorkflowIssue",
    "WorkflowPackageError",
    "WorkflowValidationResult",
    "validate_workflow_package",
)
```

- [ ] **Step 5: Run public API tests and verify they pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_validate_workflow_package_returns_ok_result_for_valid_package appV2.4/tests/test_workflow_packages.py::test_validate_workflow_package_collects_schema_and_semantic_issues -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add appV2.4/appv24/workflows/__init__.py appV2.4/appv24/workflows/validator.py appV2.4/tests/test_workflow_packages.py
git commit -m "feat(appv24): expose workflow package validator"
```

## Task 6: Example Workflow Package And Documentation

**Files:**
- Create: `examples/appv24/workflows/dependency-security-review/workflow.yaml`
- Create: `examples/appv24/workflows/dependency-security-review/connectors.yaml`
- Create: `examples/appv24/workflows/dependency-security-review/permissions.yaml`
- Create: `examples/appv24/workflows/dependency-security-review/dry_run_fixtures/happy_path.yaml`
- Create: `examples/appv24/workflows/dependency-security-review/dry_run_fixtures/rejection_path.yaml`
- Create: `examples/appv24/workflows/dependency-security-review/rollback.md`
- Create: `docs/appv24/workflow-package-validator.md`
- Modify: `appV2.4/tests/test_workflow_packages.py`

- [ ] **Step 1: Add example validation test**

Append to `appV2.4/tests/test_workflow_packages.py`:

```python
def test_example_dependency_security_review_package_is_valid() -> None:
    package = Path("examples/appv24/workflows/dependency-security-review")

    result = validate_workflow_package(package)

    assert result.ok is True, result.messages()
```

- [ ] **Step 2: Run example test and verify it fails**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_example_dependency_security_review_package_is_valid -q
```

Expected:

```text
FAIL because the example package does not exist
```

- [ ] **Step 3: Create the example package**

Create `examples/appv24/workflows/dependency-security-review/workflow.yaml`:

```yaml
version: appv24.workflow/v1
name: dependency-security-review
description: Review dependency alerts and draft a human-approved tracking issue.
triggers:
  - id: weekly
    type: schedule
    schedule: "0 9 * * 5"
entrypoint: inspect_alerts
steps:
  - id: inspect_alerts
    uses: github.list_dependabot_alerts
    with:
      repo:
        inputRef: repo
    next: draft_issue
  - id: draft_issue
    uses: github.create_issue
    side_effect_class: create
    approval: human-review
    with:
      repo:
        inputRef: repo
      title: Dependency security review
      body:
        fromStep: inspect_alerts
    next: done
  - id: done
    type: terminal
```

Create `examples/appv24/workflows/dependency-security-review/connectors.yaml`:

```yaml
version: appv24.connectors/v1
connectors:
  - id: github
    adapter: openapi
    description: GitHub REST API adapter design target.
    actions:
      - id: list_dependabot_alerts
        operationId: dependabot_list_alerts
        side_effect: false
        required_scopes:
          - dependabot:read
      - id: create_issue
        operationId: issues_create
        side_effect: true
        required_scopes:
          - issues:write
```

Create `examples/appv24/workflows/dependency-security-review/permissions.yaml`:

```yaml
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
```

Create `examples/appv24/workflows/dependency-security-review/dry_run_fixtures/happy_path.yaml`:

```yaml
input:
  repo: owner/project
alerts:
  high: 1
  critical: 0
approval:
  human-review: approved
expected:
  terminal_step: done
```

Create `examples/appv24/workflows/dependency-security-review/dry_run_fixtures/rejection_path.yaml`:

```yaml
input:
  repo: owner/project
alerts:
  high: 1
  critical: 0
approval:
  human-review: rejected
expected:
  mutation_allowed: false
```

Create `examples/appv24/workflows/dependency-security-review/rollback.md`:

```markdown
# Rollback

If the issue is created incorrectly, close the issue and leave an audit comment that the workflow draft was rejected or superseded.
```

- [ ] **Step 4: Add validator documentation**

Create `docs/appv24/workflow-package-validator.md`:

````markdown
# appv24 Workflow Package Validator

The appv24 workflow validator checks workflow-as-code packages before appv24 has any runner or cloud executor.

The validator is offline only. It does not run workflows, call APIs, send messages, provision cloud resources, or read secrets.

## Package Layout

```text
workflow.yaml
connectors.yaml
permissions.yaml
dry_run_fixtures/
  happy_path.yaml
  rejection_path.yaml
rollback.md
```

## Validation Layers

1. Required package files are present.
2. YAML files load through `yaml.safe_load`.
3. `workflow.yaml`, `connectors.yaml`, and `permissions.yaml` match appv24 JSON Schemas.
4. Workflow step IDs are unique.
5. Entrypoint and transitions point to existing steps.
6. `connector.action` references point to declared connectors and actions.
7. Permissions allow every connector action used by workflow steps.
8. Mutating steps require `side_effect_class` and an explicit approval gate.
9. Inline secret-looking values are rejected. Use `secretRef` instead.
10. Mutating workflows require `dry_run_fixtures/happy_path.yaml`, `dry_run_fixtures/rejection_path.yaml`, and `rollback.md`.

## Python API

```python
from appv24.workflows import validate_workflow_package

result = validate_workflow_package("examples/appv24/workflows/dependency-security-review")
if not result.ok:
    for message in result.messages():
        print(message)
```

## Adapter Targets

Connector adapters may reference MCP, OpenAPI, AsyncAPI, webhook, direct API, IaC, manual, or platform export targets. These are design-time targets in v1. They are not executed by the validator.
````

- [ ] **Step 5: Run example test and verify it passes**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py::test_example_dependency_security_review_package_is_valid -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: Commit Task 6**

Run:

```bash
git add docs/appv24/workflow-package-validator.md examples/appv24/workflows/dependency-security-review appV2.4/tests/test_workflow_packages.py
git commit -m "docs(appv24): add workflow validator example"
```

## Task 7: Full Verification And Boundary Check

**Files:**
- Modify only if tests reveal a real defect in files created by Tasks 1-6.

- [ ] **Step 1: Run focused workflow package tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_packages.py -q
```

Expected:

```text
all workflow package tests pass
```

- [ ] **Step 2: Run the full appv24 Python suite**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected:

```text
all appV2.4 tests pass
```

- [ ] **Step 3: Run appv24 npm CLI tests**

Run:

```bash
npm --prefix packages/appv24-cli test
```

Expected:

```text
15 passed
```

- [ ] **Step 4: Run appv24 package dry run**

Run:

```bash
npm --prefix packages/appv24-cli run pack:dry-run
```

Expected:

```text
npm pack --dry-run completes without publishing
```

- [ ] **Step 5: Verify red and yellow zones were not touched**

Run:

```bash
git diff --name-only HEAD~6..HEAD
```

Expected changed paths are limited to:

```text
appV2.4/appv24/workflows/
appV2.4/tests/test_workflow_packages.py
appV2.4/pyproject.toml
appV2.4/uv.lock
docs/appv24/workflow-package-validator.md
examples/appv24/workflows/dependency-security-review/
```

- [ ] **Step 6: Confirm no appv23/appv231 files changed**

Run:

```bash
git diff --name-only HEAD~6..HEAD | rg '^(appV2\.3|appV2\.3\.1|packages/appv23-cli|packages/appv231-cli)/'
```

Expected:

```text
no output
```

- [ ] **Step 7: Commit verification fixes if any were required**

If a verification fix was required, commit only the files related to this workflow validator:

```bash
git add appV2.4/appv24/workflows appV2.4/tests/test_workflow_packages.py appV2.4/pyproject.toml appV2.4/uv.lock docs/appv24/workflow-package-validator.md examples/appv24/workflows/dependency-security-review
git commit -m "test(appv24): verify workflow package validator"
```

If no files changed during verification, do not create an empty commit.

## Deferred Work

The following are intentionally not part of v1:

```text
appv24 CLI validation command
workflow runner
cloud runner
credential broker
MCP tool execution
OpenAPI client execution
LangGraph compilation
Serverless Workflow export
n8n or Pipedream export
mobile or web control plane
```

## Self-Review

- Spec coverage: The plan implements the offline package schema, loader, JSON Schema validation, semantic validation, public API, docs, example package, and verification.
- Boundary coverage: The plan touches green-zone appv24 workflow files, docs, examples, tests, and dependency metadata only.
- Red/yellow-zone impact: Red zone is untouched. Yellow-zone CLI is deferred.
- Type consistency: `PackageDocuments`, `WorkflowIssue`, `WorkflowValidationResult`, `validate_schema_documents`, `validate_semantics`, and `validate_workflow_package` are introduced before use by later tasks.
- Test strategy: Each implementation task starts with a failing focused test and ends with a focused pass check plus a commit.
