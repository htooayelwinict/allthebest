"""Reusable workflow extension catalog documents."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.extension_qa import validate_extension_qa_manifest
from appv24.workflows.connector_contracts import builtin_connector_contract_catalog
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.middleware_contracts import builtin_middleware_contract_catalog
from appv24.workflows.validator import validate_workflow_documents
from appv24.workflows.workflow_extensions import (
    WorkflowExtensionProfile,
    extension_specs_from_connectors,
    extension_specs_from_middlewares,
)


CATALOG_VERSION = "appv24.workflow-extension-catalog/v1"


@dataclass(frozen=True)
class WorkflowExtensionCatalogPromotion:
    status: str
    output_path: Path | None
    promoted_ids: tuple[str, ...]
    skipped_ids: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowExtensionCatalog:
    version: str
    path: Path
    package_fingerprint: str | None
    connectors: Mapping[str, Any]
    middlewares: Mapping[str, Any]
    extension_profile: WorkflowExtensionProfile


def format_workflow_extension_catalog_promotion(promotion: WorkflowExtensionCatalogPromotion) -> str:
    lines = [f"Workflow extension promotion: {promotion.status}"]
    if promotion.output_path is not None:
        lines.append(f"catalog: {promotion.output_path}")
    if promotion.promoted_ids:
        lines.append("promoted:")
        lines.extend(f"- {extension_id}" for extension_id in promotion.promoted_ids)
    if promotion.skipped_ids:
        lines.append("skipped:")
        lines.extend(f"- {extension_id}" for extension_id in promotion.skipped_ids)
    if promotion.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in promotion.issues)
    return "\n".join(lines)


def format_workflow_extension_catalog_authoring_context(catalog: WorkflowExtensionCatalog) -> str:
    """Render reusable extension declarations for package-generation prompts."""

    connectors = _redact_secret_values(_list_of_mappings(catalog.connectors.get("connectors")))
    middlewares = _redact_secret_values(_list_of_mappings(catalog.middlewares.get("middlewares")))
    permissions = _permissions_for_connectors(connectors)
    if not connectors and not middlewares and not permissions:
        return ""

    lines = [
        "Reusable extension package declarations:",
        "When using these action refs, copy the matching connector-owned declarations into the generated package files.",
        "Do not invent workflow-owned interfaces for reusable connector actions.",
    ]
    if connectors:
        lines.append("connectors.yaml entries to copy:")
        lines.append(_yaml_block({"connectors": connectors}))
    if permissions:
        lines.append("permissions.yaml entries to copy:")
        lines.append(_yaml_block({"permissions": permissions}))
    if middlewares:
        lines.append("middlewares.yaml entries to copy:")
        lines.append(_yaml_block({"middlewares": middlewares}))
    return "\n".join(lines)


def promote_package_extensions_to_catalog(
    documents: PackageDocuments,
    output_path: str | Path,
    *,
    extension_ids: tuple[str, ...] | None = None,
) -> WorkflowExtensionCatalogPromotion:
    """Write reusable catalog metadata for package extensions with passed QA."""

    validation = validate_workflow_documents(documents)
    if not validation.ok:
        return WorkflowExtensionCatalogPromotion(
            status="blocked",
            output_path=None,
            promoted_ids=(),
            skipped_ids=(),
            issues=tuple(issue.message for issue in validation.issues),
        )

    selected_ids = set(extension_ids or ())
    connectors, connector_ids, skipped_connector_ids, connector_issues = _promotable_connectors(
        documents,
        selected_ids=selected_ids,
    )
    middlewares, middleware_ids, skipped_middleware_ids, middleware_issues = _promotable_middlewares(
        documents,
        selected_ids=selected_ids,
    )
    promoted_ids = (*connector_ids, *middleware_ids)
    skipped_ids = (*skipped_connector_ids, *skipped_middleware_ids)
    issues = (*connector_issues, *middleware_issues)
    if not promoted_ids:
        return WorkflowExtensionCatalogPromotion(
            status="blocked",
            output_path=None,
            promoted_ids=(),
            skipped_ids=skipped_ids,
            issues=issues or ("no promotable extensions with passed QA found",),
        )

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            {
                "version": CATALOG_VERSION,
                "package_fingerprint": fingerprint_package_documents(documents),
                "connectors": connectors,
                "middlewares": middlewares,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return WorkflowExtensionCatalogPromotion(
        status="promoted",
        output_path=target,
        promoted_ids=promoted_ids,
        skipped_ids=skipped_ids,
        issues=issues,
    )


def load_workflow_extension_catalog(path: str | Path) -> WorkflowExtensionCatalog:
    root = Path(path).expanduser().resolve()
    document = _read_mapping(root)
    version = _string(document.get("version")) or ""
    if version != CATALOG_VERSION:
        raise ValueError(f"unsupported workflow extension catalog version: {version or '<missing>'}")
    connectors = {"version": "appv24.connectors/v1", "connectors": _list_of_mappings(document.get("connectors"))}
    middlewares = {"version": "appv24.middlewares/v1", "middlewares": _list_of_mappings(document.get("middlewares"))}
    connector_specs = extension_specs_from_connectors(connectors)
    middleware_specs = extension_specs_from_middlewares(middlewares)
    return WorkflowExtensionCatalog(
        version=version,
        path=root,
        package_fingerprint=_string(document.get("package_fingerprint")),
        connectors=connectors,
        middlewares=middlewares,
        extension_profile=WorkflowExtensionProfile(
            action_specs=connector_specs,
            middleware_specs=middleware_specs,
            transit_constraints=tuple(constraint for spec in connector_specs for constraint in spec.transit_constraints),
        ),
    )


def _promotable_connectors(
    documents: PackageDocuments,
    *,
    selected_ids: set[str],
) -> tuple[list[Mapping[str, Any]], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    promoted_connectors: list[Mapping[str, Any]] = []
    promoted_ids: list[str] = []
    skipped_ids: list[str] = []
    issues: list[str] = []
    raw_connectors = documents.connectors.get("connectors") if isinstance(documents.connectors, Mapping) else None
    if not isinstance(raw_connectors, list):
        return [], (), (), ()
    for connector in raw_connectors:
        if not isinstance(connector, Mapping):
            continue
        connector_id = _string(connector.get("id"))
        if connector_id is None:
            continue
        actions: list[Mapping[str, Any]] = []
        for action in _list_of_mappings(connector.get("actions")):
            action_id = _string(action.get("id"))
            if action_id is None:
                continue
            action_ref = f"{connector_id}.{action_id}"
            if not _is_promotable_connector_action(connector, action, action_ref):
                continue
            if selected_ids and action_ref not in selected_ids:
                continue
            qa_issue = _connector_qa_issue(action, action_ref)
            if qa_issue is not None:
                skipped_ids.append(action_ref)
                issues.append(qa_issue)
                continue
            action_copy = _without_runtime_secret_values(action)
            actions.append(action_copy)
            promoted_ids.append(action_ref)
        if actions:
            connector_copy = _without_runtime_secret_values(connector)
            connector_copy["actions"] = actions
            promoted_connectors.append(connector_copy)
    return promoted_connectors, tuple(promoted_ids), tuple(skipped_ids), tuple(issues)


def _promotable_middlewares(
    documents: PackageDocuments,
    *,
    selected_ids: set[str],
) -> tuple[list[Mapping[str, Any]], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    promoted: list[Mapping[str, Any]] = []
    promoted_ids: list[str] = []
    skipped_ids: list[str] = []
    issues: list[str] = []
    raw_middlewares = documents.middlewares.get("middlewares") if isinstance(documents.middlewares, Mapping) else None
    if not isinstance(raw_middlewares, list):
        return [], (), (), ()
    for entry in raw_middlewares:
        if not isinstance(entry, Mapping):
            continue
        middleware_id = _string(entry.get("id"))
        if middleware_id is None:
            continue
        if not _is_promotable_middleware(entry, middleware_id):
            continue
        if selected_ids and middleware_id not in selected_ids:
            continue
        qa_issue = _middleware_qa_issue(entry, middleware_id)
        if qa_issue is not None:
            skipped_ids.append(middleware_id)
            issues.append(qa_issue)
            continue
        promoted.append(_without_runtime_secret_values(entry))
        promoted_ids.append(middleware_id)
    return promoted, tuple(promoted_ids), tuple(skipped_ids), tuple(issues)


def _is_promotable_connector_action(
    connector: Mapping[str, Any],
    action: Mapping[str, Any],
    action_ref: str,
) -> bool:
    if action_ref in builtin_connector_contract_catalog():
        return False
    if action.get("custom") is True or connector.get("custom") is True:
        return True
    return isinstance(action.get("source"), str) or isinstance(connector.get("source"), str)


def _is_promotable_middleware(entry: Mapping[str, Any], middleware_id: str) -> bool:
    if middleware_id in builtin_middleware_contract_catalog() and entry.get("adapter") == "builtin":
        return False
    return True


def _connector_qa_issue(action: Mapping[str, Any], action_ref: str) -> str | None:
    qa = action.get("qa")
    if not isinstance(qa, Mapping):
        return f"{action_ref} requires passed extension QA evidence"
    report = validate_extension_qa_manifest(qa, expected_kind="connector", expected_extension_id=action_ref)
    return _qa_issue_message(action_ref, report.issues)


def _middleware_qa_issue(entry: Mapping[str, Any], middleware_id: str) -> str | None:
    qa = entry.get("qa")
    if not isinstance(qa, Mapping):
        return f"{middleware_id} requires passed extension QA evidence"
    report = validate_extension_qa_manifest(qa, expected_kind="middleware", expected_extension_id=middleware_id)
    return _qa_issue_message(middleware_id, report.issues)


def _qa_issue_message(extension_id: str, issues: tuple[WorkflowIssue, ...]) -> str | None:
    if not issues:
        return None
    joined = "; ".join(issue.message for issue in issues[:3])
    return f"{extension_id} extension QA failed: {joined}"


def _without_runtime_secret_values(value: Mapping[str, Any]) -> dict[str, Any]:
    return _redact_secret_values(copy.deepcopy(dict(value)))


def _redact_secret_values(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"value", "token", "secret", "api_key", "password"}:
                continue
            redacted[str(key)] = _redact_secret_values(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_values(item) for item in value]
    return value


def _permissions_for_connectors(connectors: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    permissions: list[Mapping[str, Any]] = []
    for connector in connectors:
        connector_id = _string(connector.get("id"))
        if connector_id is None:
            continue
        action_ids: list[str] = []
        scopes: list[str] = []
        for action in _list_of_mappings(connector.get("actions")):
            action_id = _string(action.get("id"))
            if action_id is not None:
                action_ids.append(action_id)
            for scope in _string_list(action.get("required_scopes")):
                if scope not in scopes:
                    scopes.append(scope)
        if action_ids or scopes:
            permissions.append(
                {
                    "connector": connector_id,
                    "actions": action_ids,
                    "scopes": scopes,
                }
            )
    return permissions


def _yaml_block(value: Mapping[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False).rstrip()


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        if path.suffix == ".json":
            parsed = json.loads(path.read_text(encoding="utf-8"))
        else:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"failed to read workflow extension catalog: {error}") from error
    if not isinstance(parsed, Mapping):
        raise ValueError("workflow extension catalog must be a mapping document")
    return parsed


def _list_of_mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
