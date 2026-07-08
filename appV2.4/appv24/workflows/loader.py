"""Load appv24 workflow package documents from disk."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    middlewares: Mapping[str, Any] = field(default_factory=dict)


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
        middlewares=_read_optional_mapping(root, ("middlewares.yaml", "middlewares.yml", "middlewares.json")),
    )


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        if path.suffix == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8"))
        else:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as error:
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


def _read_optional_mapping(root: Path, filenames: tuple[str, ...]) -> Mapping[str, Any]:
    for filename in filenames:
        path = root / filename
        if path.is_file():
            return _read_mapping(path)
    return {}


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorkflowPackageError(f"failed to read {path.name}: {error}") from error
