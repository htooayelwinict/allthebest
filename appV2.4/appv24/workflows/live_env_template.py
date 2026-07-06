"""Safe env template rendering for live workflow setup."""

from __future__ import annotations

from dataclasses import dataclass

from appv24.workflows.credential_refs import credential_env_name
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.loader import load_package_documents


@dataclass(frozen=True)
class WorkflowLiveEnvTemplate:
    mode: WorkflowRunMode
    workflow_name: str
    credential_lines: tuple[str, ...]
    transport_lines: tuple[str, ...]
    notes: tuple[str, ...]


def build_workflow_live_env_template(request: WorkflowRunRequest) -> WorkflowLiveEnvTemplate:
    documents = load_package_documents(request.package_path)
    return WorkflowLiveEnvTemplate(
        mode=request.mode,
        workflow_name=str(documents.workflow.get("name", "")),
        credential_lines=_credential_lines(documents.permissions),
        transport_lines=_transport_lines(request.mode, request.connector_allowlist),
        notes=_transport_notes(request.mode, request.connector_allowlist),
    )


def format_workflow_live_env_template(template: WorkflowLiveEnvTemplate) -> str:
    lines = [
        f"Workflow live env template: {template.mode.value}",
        f"workflow: {template.workflow_name or '<unknown>'}",
        "credential env:",
    ]
    lines.extend(template.credential_lines or ("# none",))
    lines.append("transport env:")
    lines.extend(template.transport_lines or ("# none",))
    if template.notes:
        lines.append("notes:")
        lines.extend(f"- {note}" for note in template.notes)
    return "\n".join(lines)


def _credential_lines(permissions: dict) -> tuple[str, ...]:
    lines: list[str] = []
    for permission in permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        env_name = credential_env_name(str(permission["connector"]))
        lines.append(f"APPV24_{env_name}_CREDENTIAL_REF=<credential-ref>")
        scopes = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        if scopes:
            lines.append(f"APPV24_{env_name}_SCOPES={','.join(scopes)}")
    return _dedupe(lines)


def _transport_lines(mode: WorkflowRunMode, allowlist: tuple[str, ...]) -> tuple[str, ...]:
    lines: list[str] = []
    for action_ref in allowlist:
        lines.extend(_known_transport_lines(mode, action_ref))
    return _dedupe(lines)


def _known_transport_lines(mode: WorkflowRunMode, action_ref: str) -> tuple[str, ...]:
    if mode is WorkflowRunMode.LIVE_READ and action_ref == "github.list_dependabot_alerts":
        return ("APPV24_WORKFLOW_LIVE_READ_ENABLED=true", "APPV24_GITHUB_TOKEN=<github-token>")
    if mode is WorkflowRunMode.LIVE_READ and action_ref in {"source.fetch_document", "source.fetch_items"}:
        return ("APPV24_WORKFLOW_LIVE_READ_ENABLED=true", "APPV24_SOURCE_BASE_URL=<https-source-base-url>")
    if mode is WorkflowRunMode.LIVE_DRAFT and action_ref == "gmail.create_draft":
        return (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true",
            "# access-token mode",
            "APPV24_GMAIL_ACCESS_TOKEN=<oauth-access-token>",
            "# refresh-token mode",
            "APPV24_GMAIL_REFRESH_TOKEN=<oauth-refresh-token>",
            "APPV24_GMAIL_CLIENT_ID=<oauth-client-id>",
            "APPV24_GMAIL_CLIENT_SECRET=<oauth-client-secret>",
        )
    if mode is WorkflowRunMode.LIVE_PRODUCTION and action_ref == "telegram.send_message":
        return ("APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true", "APPV24_TELEGRAM_BOT_TOKEN=<telegram-bot-token>")
    return ()


def _transport_notes(mode: WorkflowRunMode, allowlist: tuple[str, ...]) -> tuple[str, ...]:
    notes = [
        f"no built-in {mode.value} transport env template for {action_ref}"
        for action_ref in allowlist
        if not _known_transport_lines(mode, action_ref)
    ]
    return tuple(notes)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
