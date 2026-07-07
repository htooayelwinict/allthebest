"""Read-only workflow connector registry view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.credential_refs import credential_env_name
from appv24.workflows.execution import WorkflowRunMode
from appv24.workflows.live_transports import (
    build_live_draft_transports_from_env,
    build_live_production_transports_from_env,
    build_live_read_transports_from_env,
)
from appv24.workflows.loader import load_package_documents


@dataclass(frozen=True)
class WorkflowConnectorRegistryAction:
    action_ref: str
    connector_id: str
    action_id: str
    cataloged: bool
    side_effect: bool
    side_effect_class: str | None
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]
    live_modes: tuple[str, ...]
    configured_live_modes: tuple[str, ...]
    required_credential_env: tuple[str, ...]
    required_transport_env: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowConnectorRegistryReport:
    workflow_name: str
    package_path: Path
    actions: tuple[WorkflowConnectorRegistryAction, ...]
    issues: tuple[str, ...]

    def to_text(self) -> str:
        lines = [
            "Workflow connector registry:",
            f"workflow: {self.workflow_name or '<unknown>'}",
            f"package: {self.package_path}",
        ]
        if self.issues:
            lines.append("issues:")
            lines.extend(f"- {issue}" for issue in self.issues)
        if not self.actions:
            lines.append("actions: none")
            return "\n".join(lines)
        lines.append("actions:")
        for action in self.actions:
            catalog_status = "cataloged" if action.cataloged else "uncataloged"
            lines.append(f"- {action.action_ref}: {catalog_status}")
            if action.live_modes:
                lines.append(f"  live modes: {', '.join(action.live_modes)}")
            if action.configured_live_modes:
                lines.append(f"  configured: {', '.join(action.configured_live_modes)}")
            if action.required_credential_env:
                lines.append(f"  credential env: {'; '.join(action.required_credential_env)}")
            if action.required_transport_env:
                lines.append(f"  transport env: {'; '.join(action.required_transport_env)}")
            for issue in action.issues:
                lines.append(f"  issue: {issue}")
        return "\n".join(lines)


def inspect_workflow_connector_registry(
    package_path: str | Path,
    *,
    env: Mapping[str, str],
) -> WorkflowConnectorRegistryReport:
    """Return declared workflow connector actions joined with builtin execution metadata."""

    documents = load_package_documents(package_path)
    catalog = builtin_connector_catalog()
    configured = _configured_modes_by_action(env)
    permissions = _permission_scopes(documents.permissions)
    actions: list[WorkflowConnectorRegistryAction] = []
    issues: list[str] = []

    for declared in _declared_actions(documents.connectors):
        action_ref = declared.action_ref
        metadata = catalog.actions.get(action_ref)
        cataloged = metadata is not None
        action_issues: tuple[str, ...] = ()
        if metadata is None:
            issue = f"action is not cataloged: {action_ref}"
            issues.append(issue)
            action_issues = (issue,)

        required_scopes = declared.required_scopes or permissions.get(action_ref, ())
        if not required_scopes and metadata is not None:
            required_scopes = metadata.required_scopes
        allowed_outputs = declared.allowed_outputs or (metadata.allowed_outputs if metadata is not None else ())
        live_modes = _live_modes(metadata) if metadata is not None else ()
        actions.append(
            WorkflowConnectorRegistryAction(
                action_ref=action_ref,
                connector_id=declared.connector_id,
                action_id=declared.action_id,
                cataloged=cataloged,
                side_effect=metadata.side_effect if metadata is not None else declared.side_effect,
                side_effect_class=metadata.side_effect_class if metadata is not None else declared.side_effect_class,
                required_scopes=required_scopes,
                allowed_outputs=allowed_outputs,
                live_modes=live_modes,
                configured_live_modes=tuple(mode for mode in live_modes if action_ref in configured.get(mode, ())),
                required_credential_env=_credential_env_hints(declared.connector_id, required_scopes),
                required_transport_env=_transport_env_hints(action_ref, live_modes),
                issues=action_issues,
            )
        )

    return WorkflowConnectorRegistryReport(
        workflow_name=str(documents.workflow.get("name", "")),
        package_path=documents.root,
        actions=tuple(sorted(actions, key=lambda action: action.action_ref)),
        issues=tuple(issues),
    )


@dataclass(frozen=True)
class _DeclaredAction:
    connector_id: str
    action_id: str
    action_ref: str
    side_effect: bool
    side_effect_class: str | None
    required_scopes: tuple[str, ...]
    allowed_outputs: tuple[str, ...]


def _declared_actions(connectors: Mapping[str, Any]) -> tuple[_DeclaredAction, ...]:
    declared: list[_DeclaredAction] = []
    for connector in connectors.get("connectors", []):
        if not isinstance(connector, Mapping) or not isinstance(connector.get("id"), str):
            continue
        connector_id = str(connector["id"])
        for action in connector.get("actions", []):
            if not isinstance(action, Mapping) or not isinstance(action.get("id"), str):
                continue
            action_id = str(action["id"])
            outputs = action.get("outputs")
            allowed = outputs.get("allowed", ()) if isinstance(outputs, Mapping) else ()
            declared.append(
                _DeclaredAction(
                    connector_id=connector_id,
                    action_id=action_id,
                    action_ref=f"{connector_id}.{action_id}",
                    side_effect=bool(action.get("side_effect", False)),
                    side_effect_class=str(action["side_effect_class"]) if isinstance(action.get("side_effect_class"), str) else None,
                    required_scopes=_string_tuple(action.get("required_scopes", ())),
                    allowed_outputs=_string_tuple(allowed),
                )
            )
    return tuple(declared)


def _permission_scopes(permissions: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    scopes_by_action: dict[str, tuple[str, ...]] = {}
    for permission in permissions.get("permissions", []):
        if not isinstance(permission, Mapping) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        scopes = _string_tuple(permission.get("scopes", ()))
        for action in permission.get("actions", []):
            if isinstance(action, str):
                scopes_by_action[f"{connector_id}.{action}"] = scopes
    return scopes_by_action


def _configured_modes_by_action(env: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    return {
        "live-read": tuple(build_live_read_transports_from_env(env).keys()),
        "live-draft": tuple(build_live_draft_transports_from_env(env).keys()),
        "live-production": tuple(build_live_production_transports_from_env(env).keys()),
    }


def _live_modes(metadata) -> tuple[str, ...]:
    modes: list[str] = []
    if metadata.live_read_eligible:
        modes.append(WorkflowRunMode.LIVE_READ.value)
    if metadata.live_draft_eligible:
        modes.append(WorkflowRunMode.LIVE_DRAFT.value)
    if metadata.live_production_enabled:
        modes.append(WorkflowRunMode.LIVE_PRODUCTION.value)
    return tuple(modes)


def _credential_env_hints(connector_id: str, scopes: tuple[str, ...]) -> tuple[str, ...]:
    env_name = credential_env_name(connector_id)
    parts = [f"APPV24_{env_name}_CREDENTIAL_REF"]
    if scopes:
        parts.append(f"APPV24_{env_name}_SCOPES={','.join(scopes)}")
    return (", ".join(parts),)


def _transport_env_hints(action_ref: str, live_modes: tuple[str, ...]) -> tuple[str, ...]:
    hints = []
    for mode in live_modes:
        hint = _transport_env_hint(mode, action_ref)
        if hint is not None:
            hints.append(f"{mode}: {hint}")
    return tuple(hints)


def _transport_env_hint(mode: str, action_ref: str) -> str | None:
    hints = {
        ("live-read", "brave.search"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_BRAVE_SEARCH_API_KEY",
        ("live-read", "github.list_dependabot_alerts"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
        ("live-read", "source.fetch_document"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        ("live-read", "source.fetch_items"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        (
            "live-draft",
            "gmail.create_draft",
        ): (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or "
            "APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
        ),
        ("live-production", "telegram.send_message"): (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
    }
    return hints.get((mode, action_ref))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))
