"""No-side-effect operator readiness reports for live workflow runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from appv24.workflows.bundle_guard import inspect_default_workflow_bundle
from appv24.workflows.connector_contracts import external_connector_env_requirements
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import (
    credential_env_name,
    credential_resolver_for_package_connector_config,
    preflight_credential_refs,
)
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.live_preflight import (
    LiveDraftPreflightReport,
    preflight_live_draft_workflow,
    preflight_live_production_workflow,
    preflight_live_read_workflow,
)
from appv24.workflows.live_transports import (
    build_live_draft_transports_from_env,
    build_live_production_transports_from_connector_config,
    build_live_production_transports_from_env,
    build_live_read_transports_from_connector_config,
    build_live_read_transports_from_env,
)
from appv24.workflows.loader import load_package_documents
from appv24.workflows.middleware_contracts import middleware_env_refs, middleware_env_requirements


@dataclass(frozen=True)
class WorkflowLiveReadinessReport:
    mode: WorkflowRunMode
    status: str
    preflight: LiveDraftPreflightReport
    configured_transports: tuple[str, ...]
    required_credential_env: tuple[str, ...]
    required_transport_env: tuple[str, ...]
    required_connector_env: tuple[str, ...]
    required_middleware_env: tuple[str, ...]
    bundle_status: str
    bundle_path: Path
    bundle_issues: tuple[str, ...]
    issues: tuple[str, ...]
    next_steps: tuple[str, ...]


def inspect_workflow_live_readiness(
    request: WorkflowRunRequest,
    *,
    env: Mapping[str, str],
    connector_config: ConnectorConfigResolver | None = None,
) -> WorkflowLiveReadinessReport:
    """Inspect readiness for a live workflow run without invoking connectors."""

    connector_config = connector_config or ConnectorConfigResolver(env=env)
    available_transports = _configured_transports(request.mode, env, connector_config)
    preflight = _preflight(request, connector_config=connector_config, available_transports=available_transports)
    required_credential_env = _required_credential_env(request)
    required_transport_env = _required_transport_env(request)
    required_connector_env = _required_connector_env(request)
    required_middleware_env = _required_middleware_env(request)
    setup_issues = _setup_issues(request, env=env, connector_config=connector_config)
    bundle = inspect_default_workflow_bundle(request.package_path)
    issues = _dedupe((*setup_issues, *preflight.issues, *bundle.issues))
    status = "ready" if preflight.status == "passed" and not issues else "blocked"
    return WorkflowLiveReadinessReport(
        mode=request.mode,
        status=status,
        preflight=preflight,
        configured_transports=tuple(sorted(available_transports)),
        required_credential_env=required_credential_env,
        required_transport_env=required_transport_env,
        required_connector_env=required_connector_env,
        required_middleware_env=required_middleware_env,
        bundle_status=bundle.status,
        bundle_path=bundle.bundle_path,
        bundle_issues=bundle.issues,
        issues=issues,
        next_steps=_next_steps(request, status, bundle_status=bundle.status),
    )


def format_workflow_live_readiness_report(report: WorkflowLiveReadinessReport) -> str:
    lines = [
        f"Workflow live readiness: {report.status}",
        f"mode: {report.mode.value}",
        f"workflow: {report.preflight.workflow_name or '<unknown>'}",
        f"fixture: {report.preflight.fixture_name}",
    ]
    if report.preflight.approval_run_id:
        lines.append(f"approval run: {report.preflight.approval_run_id}")
    lines.append(f"bundle check: {report.bundle_status}")
    lines.append(f"bundle: {report.bundle_path}")
    if report.issues:
        lines.append("issues:")
        lines.extend(f"- {issue}" for issue in report.issues)
    if report.preflight.checked_actions:
        lines.append("checked actions:")
        lines.extend(f"- {action}" for action in report.preflight.checked_actions)
    lines.append("configured transports:")
    if report.configured_transports:
        lines.extend(f"- {action}" for action in report.configured_transports)
    else:
        lines.append("- none")
    if report.required_credential_env:
        lines.append("required credential env:")
        lines.extend(f"- {hint}" for hint in report.required_credential_env)
    if report.required_transport_env:
        lines.append("required transport env:")
        lines.extend(f"- {hint}" for hint in report.required_transport_env)
    if report.required_connector_env:
        lines.append("required connector env:")
        lines.extend(f"- {hint}" for hint in report.required_connector_env)
    if report.required_middleware_env:
        lines.append("required middleware env:")
        lines.extend(f"- {hint}" for hint in report.required_middleware_env)
    if report.next_steps:
        lines.append("next steps:")
        lines.extend(f"- {step}" for step in report.next_steps)
    return "\n".join(lines)


def _preflight(
    request: WorkflowRunRequest,
    *,
    connector_config: ConnectorConfigResolver,
    available_transports: tuple[str, ...],
) -> LiveDraftPreflightReport:
    credential_resolver = credential_resolver_for_package_connector_config(request.package_path, connector_config)
    if request.mode is WorkflowRunMode.LIVE_READ:
        return preflight_live_read_workflow(
            request,
            credential_resolver=credential_resolver,
            available_transports=available_transports,
        )
    if request.mode is WorkflowRunMode.LIVE_DRAFT:
        return preflight_live_draft_workflow(
            request,
            credential_resolver=credential_resolver,
            available_transports=available_transports,
        )
    if request.mode is WorkflowRunMode.LIVE_PRODUCTION:
        return preflight_live_production_workflow(
            request,
            credential_resolver=credential_resolver,
            available_transports=available_transports,
        )
    return preflight_live_read_workflow(
        request,
        credential_resolver=credential_resolver,
        available_transports=available_transports,
    )


def _configured_transports(
    mode: WorkflowRunMode,
    env: Mapping[str, str],
    connector_config: ConnectorConfigResolver,
) -> tuple[str, ...]:
    if mode is WorkflowRunMode.LIVE_READ:
        return tuple(
            sorted(
                {
                    *build_live_read_transports_from_env(env).keys(),
                    *build_live_read_transports_from_connector_config(connector_config).keys(),
                }
            )
        )
    if mode is WorkflowRunMode.LIVE_DRAFT:
        return tuple(_legacy_configured_transports(mode, env))
    if mode is WorkflowRunMode.LIVE_PRODUCTION:
        return tuple(
            sorted(
                {
                    *build_live_production_transports_from_env(env).keys(),
                    *build_live_production_transports_from_connector_config(connector_config).keys(),
                }
            )
        )
    return ()


def _legacy_configured_transports(mode: WorkflowRunMode, env: Mapping[str, str]) -> tuple[str, ...]:
    if mode is WorkflowRunMode.LIVE_READ:
        return tuple(sorted(build_live_read_transports_from_env(env).keys()))
    if mode is WorkflowRunMode.LIVE_DRAFT:
        return tuple(sorted(build_live_draft_transports_from_env(env).keys()))
    if mode is WorkflowRunMode.LIVE_PRODUCTION:
        return tuple(sorted(build_live_production_transports_from_env(env).keys()))
    return ()


def _required_credential_env(request: WorkflowRunRequest) -> tuple[str, ...]:
    documents = load_package_documents(request.package_path)
    hints: list[str] = []
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        env_name = credential_env_name(connector_id)
        parts = [f"APPV24_{env_name}_CREDENTIAL_REF"]
        scopes = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        if scopes:
            parts.append(f"APPV24_{env_name}_SCOPES={','.join(scopes)}")
        hints.append(", ".join(parts))
    return _dedupe(hints)


def _required_transport_env(request: WorkflowRunRequest) -> tuple[str, ...]:
    return _dedupe(
        hint
        for action in request.connector_allowlist
        for hint in [_transport_env_hint(request.mode, action)]
        if hint is not None
    )


def _required_connector_env(request: WorkflowRunRequest) -> tuple[str, ...]:
    return external_connector_env_requirements(load_package_documents(request.package_path).connectors)


def _required_middleware_env(request: WorkflowRunRequest) -> tuple[str, ...]:
    return middleware_env_requirements(load_package_documents(request.package_path))


def _transport_env_hint(mode: WorkflowRunMode, action_ref: str) -> str | None:
    hints = {
        (WorkflowRunMode.LIVE_READ, "brave.search"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_BRAVE_SEARCH_API_KEY",
        (WorkflowRunMode.LIVE_READ, "github.list_dependabot_alerts"): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_GITHUB_TOKEN",
        (
            WorkflowRunMode.LIVE_READ,
            "source.fetch_document",
        ): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        (
            WorkflowRunMode.LIVE_READ,
            "source.fetch_items",
        ): "APPV24_WORKFLOW_LIVE_READ_ENABLED=true, APPV24_SOURCE_BASE_URL",
        (
            WorkflowRunMode.LIVE_DRAFT,
            "gmail.create_draft",
        ): (
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED=true plus APPV24_GMAIL_ACCESS_TOKEN or "
            "APPV24_GMAIL_REFRESH_TOKEN, APPV24_GMAIL_CLIENT_ID, APPV24_GMAIL_CLIENT_SECRET"
        ),
        (
            WorkflowRunMode.LIVE_PRODUCTION,
            "telegram.send_message",
        ): (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
        (
            WorkflowRunMode.LIVE_PRODUCTION,
            "telegram.broadcast_message",
        ): (
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED=true, "
            "APPV24_TELEGRAM_BOT_TOKEN, APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
        ),
    }
    return hints.get((mode, action_ref))


def _setup_issues(
    request: WorkflowRunRequest,
    *,
    env: Mapping[str, str],
    connector_config: ConnectorConfigResolver,
) -> tuple[str, ...]:
    issues: list[str] = []
    if request.mode is WorkflowRunMode.LIVE_DRAFT and not request.approval_run_id:
        issues.append("live-draft requires approval_run_id")
    elif request.mode is WorkflowRunMode.LIVE_PRODUCTION and not request.approval_run_id:
        issues.append("live-production requires approval_run_id")
    documents = load_package_documents(request.package_path)
    credential_resolver = credential_resolver_for_package_connector_config(request.package_path, connector_config)
    credential_report = preflight_credential_refs(documents, credential_resolver)
    issues.extend(credential_report.issues)
    issues.extend(_connector_config_hints(documents.permissions, connector_config))
    for middleware_id, env_ref in middleware_env_refs(documents):
        if env_ref.required and env_ref.env_name not in env:
            issues.append(f"missing middleware env for {middleware_id}; required env: {env_ref.env_name}")
    for env_name in external_connector_env_requirements(documents.connectors):
        if env_name not in env:
            issues.append(f"missing connector env; required env: {env_name}")
    return tuple(issues)


def _connector_config_hints(
    permissions: Mapping[str, object],
    connector_config: ConnectorConfigResolver,
) -> tuple[str, ...]:
    hints: list[str] = []
    for permission in permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        resolved = connector_config.resolve(connector_id)
        if resolved is not None and resolved.available:
            continue
        scopes = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        hints.append(_connector_config_hint(connector_id, scopes))
    return _dedupe(hints)


def _connector_config_hint(connector_id: str, requested_scopes: tuple[str, ...]) -> str:
    env_name = credential_env_name(connector_id)
    pieces = [f"missing connector config for {connector_id}; run /workflow-connector-configure {connector_id}"]
    env_hint = [f"APPV24_{env_name}_CREDENTIAL_REF"]
    if requested_scopes:
        env_hint.append(f"APPV24_{env_name}_SCOPES={','.join(requested_scopes)}")
    pieces.append(f"env fallback: {', '.join(env_hint)}")
    return "; ".join(pieces)


def _next_steps(request: WorkflowRunRequest, status: str, *, bundle_status: str) -> tuple[str, ...]:
    steps: list[str] = []
    if bundle_status in {"missing", "failed"}:
        steps.append(f"/workflow-export-bundle {request.package_path}")
    if status == "ready":
        if request.mode is WorkflowRunMode.LIVE_READ:
            steps.append(_live_read_run_command(request))
            return tuple(steps)
        if request.mode is WorkflowRunMode.LIVE_DRAFT:
            steps.append(_live_draft_run_command(request))
            return tuple(steps)
        if request.mode is WorkflowRunMode.LIVE_PRODUCTION:
            steps.append(_live_production_run_command(request))
            return tuple(steps)
        return tuple(steps)
    if request.mode is WorkflowRunMode.LIVE_READ:
        steps.append("fix the listed env/allowlist issues, then rerun /workflow-live-ready live-read")
        return tuple(steps)
    if request.mode is WorkflowRunMode.LIVE_DRAFT:
        steps.append("run a local workflow approval pass, approve the request, fix env issues, then rerun readiness")
        return tuple(steps)
    if request.mode is WorkflowRunMode.LIVE_PRODUCTION:
        steps.append("run a local workflow approval pass, approve the request, fix env issues, then rerun readiness")
        return tuple(steps)
    return tuple(steps)


def _live_read_run_command(request: WorkflowRunRequest) -> str:
    return (
        f"/workflow-run-live-read {request.package_path} {request.fixture_name or 'happy_path.yaml'} "
        f"--allow {','.join(request.connector_allowlist)}"
    )


def _live_draft_run_command(request: WorkflowRunRequest) -> str:
    return (
        f"/workflow-run-live-draft {request.package_path} {request.fixture_name or 'happy_path.yaml'} "
        f"{request.approval_run_id or '<approval-run-id>'} --allow {','.join(request.connector_allowlist)}"
    )


def _live_production_run_command(request: WorkflowRunRequest) -> str:
    return (
        f"/workflow-run-live-production {request.package_path} {request.fixture_name or 'happy_path.yaml'} "
        f"{request.approval_run_id or '<approval-run-id>'} --allow {','.join(request.connector_allowlist)}"
    )


def _dedupe(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))
