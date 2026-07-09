"""Workflow connector execution contracts.

This module is the source of truth for built-in workflow action vocabulary.
Adapters such as connector_packs.py and catalog_builtins.py expose legacy views
derived from these contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from dataclasses import dataclass
from enum import StrEnum

from appv24.workflows.errors import WorkflowIssue


class ConnectorLiveMode(StrEnum):
    LIVE_READ = "live-read"
    LIVE_DRAFT = "live-draft"
    LIVE_PRODUCTION = "live-production"


class SideEffectClass(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SEND = "send"
    PUBLISH = "publish"
    PROVISION = "provision"


class ApprovalClass(StrEnum):
    NONE = "none"
    DRAFT = "draft"
    EXTERNAL_SEND = "external_send"
    PRODUCTION_MUTATION = "production_mutation"
    INFRA_PROVISION = "infra_provision"


class RetryClass(StrEnum):
    NONE = "none"
    READ_SAFE = "read_safe"
    IDEMPOTENT_WRITE = "idempotent_write"


@dataclass(frozen=True)
class ConnectorSecretRef:
    env_name: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class ExternalConnectorEnvRef:
    env_name: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class ConnectorTargetPolicy:
    allow_env: str | None = None
    allowed_schemes: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_chat_ids_env: str | None = None
    required_target_fields: tuple[str, ...] = ()
    max_response_bytes: int | None = None
    follow_redirects: bool = False


@dataclass(frozen=True)
class ConnectorContract:
    action_ref: str
    connector_id: str
    action_id: str
    input_schema: dict[str, object]
    required_input_fields: tuple[str, ...]
    output_schema: dict[str, object]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    secrets: tuple[ConnectorSecretRef, ...]
    side_effect_class: SideEffectClass
    approval_class: ApprovalClass
    live_modes: tuple[ConnectorLiveMode, ...]
    idempotency_key_fields: tuple[str, ...]
    retry_class: RetryClass
    target_policy: ConnectorTargetPolicy
    prompt_summary: str
    adapter: str = "direct_api"
    execution_mode: str = "brokered"
    dry_run_supported: bool = True
    design_only: bool = False

    @property
    def side_effect(self) -> bool:
        return self.side_effect_class is not SideEffectClass.READ

    @property
    def approval_required(self) -> bool:
        return self.approval_class is not ApprovalClass.NONE


EXTERNAL_CONNECTOR_EXECUTION_MODES = frozenset({"brokered", "manual", "design_only"})
EXTERNAL_CONNECTOR_SIDE_EFFECT_CLASSES = frozenset({"read", "create", "update", "delete", "send", "publish", "provision"})
EXTERNAL_CONNECTOR_IDEMPOTENCY_CLASSES = frozenset({"create", "send", "publish", "provision"})
EXTERNAL_CONNECTOR_SCOPELESS_ADAPTERS = frozenset({"llm", "manual", "platform_export"})


def builtin_connector_contract_catalog() -> dict[str, ConnectorContract]:
    contracts = (
        _contract(
            "brave.search",
            input_fields=("query", "q", "freshness", "count", "country", "search_lang", "extra_snippets"),
            required_input_fields=("query",),
            allowed_outputs=("items", "source_count", "query", "artifact_path"),
            required_scopes=("brave:search",),
            secrets=(ConnectorSecretRef("APPV24_BRAVE_SEARCH_API_KEY", "Brave Search API key"),),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            adapter="direct_api",
            prompt_summary="brave.search(input: query, freshness, count) -> items, source_count",
        ),
        _contract(
            "source.fetch_document",
            allowed_outputs=("document", "title", "url", "metadata", "artifact_path"),
            required_scopes=("source:read",),
            secrets=(ConnectorSecretRef("APPV24_SOURCE_BASE_URL", "operator source base URL"),),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_schemes=("http", "https"), follow_redirects=False),
            prompt_summary="source.fetch_document(input: source_url/path/query) -> document",
        ),
        _contract(
            "source.fetch_items",
            allowed_outputs=("items", "source_count", "metadata", "artifact_path"),
            required_scopes=("source:read",),
            secrets=(ConnectorSecretRef("APPV24_SOURCE_BASE_URL", "operator source base URL"),),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_schemes=("http", "https"), follow_redirects=False),
            prompt_summary="source.fetch_items(input: source_url/path/query) -> items, source_count",
        ),
        _contract(
            "http.fetch",
            allowed_outputs=("status_code", "url", "text", "json", "artifact_path"),
            input_fields=("url", "source_url", "query"),
            required_input_fields=("url",),
            required_scopes=("http:read",),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(
                allow_env="APPV24_HTTP_FETCH_ALLOWED_HOSTS",
                allowed_schemes=("http", "https"),
                max_response_bytes=65536,
                follow_redirects=False,
            ),
            prompt_summary="http.fetch(input: url, query) -> status_code, url, text/json",
        ),
        _contract(
            "github.list_dependabot_alerts",
            allowed_outputs=("alerts", "count", "artifact_path"),
            required_scopes=("dependabot:read",),
            secrets=(ConnectorSecretRef("APPV24_GITHUB_TOKEN", "GitHub API token"),),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            adapter="openapi",
            prompt_summary="github.list_dependabot_alerts(input: repository) -> alerts, count",
        ),
        _contract(
            "llm.classify",
            allowed_outputs=("label", "classification", "confidence", "confidence_score", "reason", "raw_response", "artifact_path"),
            adapter="llm",
            prompt_summary="llm.classify(input: text/schema) -> classification, confidence",
        ),
        _contract(
            "llm.extract_structured",
            allowed_outputs=("alerts", "items", "count", "fields", "confidence", "confidence_score", "issues", "raw_response", "artifact_path"),
            adapter="llm",
            prompt_summary="llm.extract_structured(input: text/items/schema) -> fields, confidence, issues",
        ),
        _contract(
            "llm.summarize",
            input_fields=("text", "items", "input_ref"),
            allowed_outputs=("summary", "citations", "confidence", "raw_response", "artifact_path"),
            adapter="llm",
            prompt_summary="llm.summarize(input: text/items) -> summary, citations",
        ),
        _contract(
            "policy.check",
            allowed_outputs=("approved", "allowed", "reason", "matched_rule", "artifact_path"),
            adapter="manual",
            prompt_summary="policy.check(input: fields/rules) -> allowed, reason, matched_rule",
        ),
        _contract(
            "ticket.create_draft",
            allowed_outputs=("ticket_id", "draft_id", "url", "title", "artifact_path"),
            required_scopes=("ticket:draft",),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            live_modes=(ConnectorLiveMode.LIVE_DRAFT,),
            idempotency_key_fields=("source_id", "summary"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            adapter="manual",
            prompt_summary="ticket.create_draft(input: title, body) -> ticket_id",
        ),
        _contract(
            "notification.create_draft",
            allowed_outputs=("draft_id", "channel", "recipient", "artifact_path"),
            required_scopes=("notification:draft",),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            live_modes=(ConnectorLiveMode.LIVE_DRAFT,),
            idempotency_key_fields=("alert_key", "digest_key"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            adapter="manual",
            prompt_summary="notification.create_draft(input: channel, recipient, body) -> draft_id",
        ),
        _contract(
            "gmail.create_draft",
            input_fields=("to", "recipient", "subject", "body", "from", "cc", "bcc", "thread_id"),
            allowed_outputs=("draft_id", "thread_id", "artifact_path"),
            required_scopes=("gmail.compose",),
            secrets=(
                ConnectorSecretRef("APPV24_GMAIL_ACCESS_TOKEN", "Gmail OAuth access token", required=False),
                ConnectorSecretRef("APPV24_GMAIL_REFRESH_TOKEN", "Gmail OAuth refresh token", required=False),
            ),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            live_modes=(ConnectorLiveMode.LIVE_DRAFT,),
            idempotency_key_fields=("to", "subject"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            prompt_summary="gmail.create_draft(input: to, subject, body) -> draft_id, thread_id",
        ),
        _contract(
            "telegram.create_message_draft",
            allowed_outputs=("draft_id", "chat_id", "text", "artifact_path"),
            required_scopes=("telegram:draft",),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            live_modes=(ConnectorLiveMode.LIVE_DRAFT,),
            idempotency_key_fields=("chat_id", "message_hash"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            prompt_summary="telegram.create_message_draft(input: chat_id, text) -> draft_id",
        ),
        _contract(
            "telegram.send_message_draft",
            allowed_outputs=("draft_id", "artifact_path"),
            required_scopes=("telegram:draft",),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.DRAFT,
            idempotency_key_fields=("chat_id", "message_hash"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            prompt_summary="telegram.send_message_draft(input: chat_id, text) -> draft_id",
        ),
        _contract(
            "telegram.list_subscribers",
            input_fields=("audience", "limit"),
            allowed_outputs=("recipients", "recipient_count", "audience_hash", "artifact_path"),
            required_scopes=("telegram:subscribers:read",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_ALLOWED_CHAT_IDS", "Telegram subscriber allowlist"),),
            live_modes=(ConnectorLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary=(
                "telegram.list_subscribers(input: audience, limit) -> "
                "recipients, recipient_count, audience_hash"
            ),
        ),
        _contract(
            "telegram.broadcast_message",
            input_fields=(
                "recipients",
                "audience_hash",
                "text",
                "parse_mode",
                "disable_notification",
                "protect_content",
            ),
            required_input_fields=("recipients", "audience_hash", "text"),
            allowed_outputs=("sent_count", "message_ids", "chat_ids", "artifact_path"),
            required_scopes=("telegram:send",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_BOT_TOKEN", "Telegram bot token"),),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.EXTERNAL_SEND,
            live_modes=(ConnectorLiveMode.LIVE_PRODUCTION,),
            idempotency_key_fields=("audience_hash", "text"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary="telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids",
        ),
        _contract(
            "telegram.send_message",
            input_fields=(
                "chat_id",
                "admin_chat_id",
                "recipient",
                "text",
                "message_text",
                "body",
                "parse_mode",
                "disable_notification",
                "protect_content",
            ),
            required_input_fields=("chat_id", "text"),
            allowed_outputs=("message_id", "chat_id", "artifact_path"),
            required_scopes=("telegram:send",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_BOT_TOKEN", "Telegram bot token"),),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.EXTERNAL_SEND,
            live_modes=(ConnectorLiveMode.LIVE_PRODUCTION,),
            idempotency_key_fields=("chat_id", "text"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            target_policy=ConnectorTargetPolicy(allowed_chat_ids_env="APPV24_TELEGRAM_ALLOWED_CHAT_IDS"),
            prompt_summary="telegram.send_message(input: chat_id, text) -> message_id, chat_id",
        ),
        _contract(
            "github.create_issue",
            allowed_outputs=("issue_number", "issue_url", "artifact_path"),
            required_scopes=("issues:write",),
            secrets=(ConnectorSecretRef("APPV24_GITHUB_TOKEN", "GitHub API token"),),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.PRODUCTION_MUTATION,
            idempotency_key_fields=("repo", "alert_id"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            adapter="openapi",
            prompt_summary="github.create_issue(input: repo, title, body) -> issue_number, issue_url",
        ),
        _contract(
            "github.create_issue_draft",
            allowed_outputs=("draft_id", "artifact_path"),
            required_scopes=("issues:draft",),
            secrets=(ConnectorSecretRef("APPV24_GITHUB_TOKEN", "GitHub API token", required=False),),
            side_effect_class=SideEffectClass.CREATE,
            approval_class=ApprovalClass.DRAFT,
            idempotency_key_fields=("repository", "title", "body_hash"),
            retry_class=RetryClass.IDEMPOTENT_WRITE,
            prompt_summary="github.create_issue_draft(input: repository, title, body) -> draft_id",
        ),
    )
    return {contract.action_ref: contract for contract in contracts}


def live_mode_values(contract: ConnectorContract) -> tuple[str, ...]:
    return tuple(mode.value for mode in contract.live_modes)


def workflow_operator_env_names(
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> tuple[str, ...]:
    catalog = builtin_connector_contract_catalog() if contract_catalog is None else contract_catalog
    env_names = set()
    for contract in catalog.values():
        if contract.target_policy.allow_env:
            env_names.add(contract.target_policy.allow_env)
        if contract.target_policy.allowed_chat_ids_env:
            env_names.add(contract.target_policy.allowed_chat_ids_env)
    return tuple(sorted(env_names))


def workflow_operator_env_from_env(
    env: Mapping[str, object],
    contract_catalog: Mapping[str, ConnectorContract] | None = None,
) -> dict[str, str]:
    return {
        name: str(env[name])
        for name in workflow_operator_env_names(contract_catalog)
        if name in env
    }


def external_connector_action_refs(connectors_document: Mapping[str, Any]) -> tuple[str, ...]:
    """Return non-built-in connector actions that declare an external interface."""

    builtins = builtin_connector_contract_catalog()
    refs: list[str] = []
    for connector, action in _iter_connector_actions(connectors_document):
        action_ref = _action_ref(connector, action)
        if action_ref is None or action_ref in builtins:
            continue
        if _is_design_only_action(action):
            continue
        if _is_external_connector_candidate(connector, action):
            refs.append(action_ref)
    return tuple(sorted(dict.fromkeys(refs)))


def validate_external_connector_contracts(connectors_document: Mapping[str, Any]) -> tuple[WorkflowIssue, ...]:
    """Validate connector-owned interfaces for non-built-in executable actions."""

    builtins = builtin_connector_contract_catalog()
    issues: list[WorkflowIssue] = []
    for connector, action in _iter_connector_actions(connectors_document):
        action_ref = _action_ref(connector, action)
        if action_ref is None or action_ref in builtins:
            continue
        if _is_design_only_action(action):
            continue
        if not _is_external_connector_candidate(connector, action):
            continue
        issues.extend(_external_connector_action_issues(connector, action, action_ref))
    return tuple(issues)


def is_external_connector_action_payload(action_ref: str, action: Mapping[str, Any]) -> bool:
    """Return whether an IR action payload is an explicitly external action."""

    if action_ref in builtin_connector_contract_catalog():
        return False
    if action.get("custom") is not True:
        return False
    source = action.get("source")
    if not isinstance(source, str) or not _is_versioned_source(source):
        return False
    execution_mode = action.get("execution_mode")
    if execution_mode not in {"brokered", "manual"}:
        return False
    return True


def external_connector_env_refs(connectors_document: Mapping[str, Any]) -> tuple[ExternalConnectorEnvRef, ...]:
    refs: list[ExternalConnectorEnvRef] = []
    for connector, action in _iter_connector_actions(connectors_document):
        action_ref = _action_ref(connector, action)
        if action_ref is None or action_ref in builtin_connector_contract_catalog():
            continue
        if not _is_external_connector_candidate(connector, action):
            continue
        refs.extend(_env_refs(connector.get("env")))
        refs.extend(_env_refs(action.get("env")))
    return tuple(refs)


def external_connector_env_requirements(connectors_document: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(ref.env_name for ref in external_connector_env_refs(connectors_document) if ref.required)))


def _external_connector_action_issues(
    connector: Mapping[str, Any],
    action: Mapping[str, Any],
    action_ref: str,
) -> list[WorkflowIssue]:
    connector_id = str(connector.get("id", ""))
    action_id = str(action.get("id", ""))
    path = ("connectors", connector_id, "actions", action_id)
    issues: list[WorkflowIssue] = []

    if action.get("custom") is not True and connector.get("custom") is not True:
        issues.append(
            WorkflowIssue(
                "external_connector_contract_missing",
                "connectors.yaml",
                (*path, "custom"),
                f"{action_ref} external connector actions require custom: true",
                expected="custom: true",
            )
        )

    source = _string(action.get("source")) or _string(connector.get("source"))
    if source is None:
        issues.append(
            WorkflowIssue(
                "external_connector_contract_missing",
                "connectors.yaml",
                (*path, "source"),
                f"{action_ref} external connector actions require a versioned source URI",
                expected="source URI with @version",
            )
        )
    elif not _is_versioned_source(source):
        issues.append(
            WorkflowIssue(
                "external_connector_contract_invalid",
                "connectors.yaml",
                (*path, "source"),
                f"{action_ref} source must be a versioned connector URI",
                expected="scheme://connector-or-action@version",
            )
        )

    side_effect = action.get("side_effect")
    if not isinstance(side_effect, bool):
        issues.append(
            WorkflowIssue(
                "external_connector_contract_missing",
                "connectors.yaml",
                (*path, "side_effect"),
                f"{action_ref} external connector actions require side_effect",
                expected="true or false",
            )
        )

    side_effect_class = action.get("side_effect_class")
    if side_effect_class not in EXTERNAL_CONNECTOR_SIDE_EFFECT_CLASSES:
        issues.append(
            WorkflowIssue(
                "external_connector_contract_missing",
                "connectors.yaml",
                (*path, "side_effect_class"),
                f"{action_ref} external connector actions require side_effect_class",
                expected=", ".join(sorted(EXTERNAL_CONNECTOR_SIDE_EFFECT_CLASSES)),
            )
        )
    elif side_effect is False and side_effect_class != "read":
        issues.append(
            WorkflowIssue(
                "external_connector_contract_invalid",
                "connectors.yaml",
                (*path, "side_effect_class"),
                f"{action_ref} read-only actions require side_effect_class: read",
                expected="read",
            )
        )
    elif side_effect is True and side_effect_class == "read":
        issues.append(
            WorkflowIssue(
                "external_connector_contract_invalid",
                "connectors.yaml",
                (*path, "side_effect_class"),
                f"{action_ref} mutating actions cannot use side_effect_class: read",
                expected="create, update, delete, send, publish, or provision",
            )
        )

    execution_mode = action.get("execution_mode")
    if execution_mode not in EXTERNAL_CONNECTOR_EXECUTION_MODES:
        issues.append(
            WorkflowIssue(
                "external_connector_contract_missing",
                "connectors.yaml",
                (*path, "execution_mode"),
                f"{action_ref} external connector actions require execution_mode",
                expected="brokered, manual, or design_only",
            )
        )

    if execution_mode in {"brokered", "manual"}:
        if not isinstance(action.get("dry_run_supported"), bool):
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (*path, "dry_run_supported"),
                    f"{action_ref} executable external connector actions require dry_run_supported",
                    expected="true or false",
                )
            )

        allowed_outputs = _allowed_outputs(action)
        if not allowed_outputs:
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (*path, "outputs", "allowed"),
                    f"{action_ref} executable external connector actions require outputs.allowed",
                    expected="non-empty explicit output list",
                )
            )
        elif "*" in allowed_outputs:
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_invalid",
                    "connectors.yaml",
                    (*path, "outputs", "allowed"),
                    f"{action_ref} outputs.allowed must not include wildcard",
                    expected="explicit output fields",
                )
            )

        required_scopes = _string_tuple(action.get("required_scopes"))
        adapter = _string(connector.get("adapter")) or ""
        if adapter not in EXTERNAL_CONNECTOR_SCOPELESS_ADAPTERS and not required_scopes:
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (*path, "required_scopes"),
                    f"{action_ref} external connector actions require explicit required_scopes",
                    expected="non-empty required_scopes list",
                )
            )

        if side_effect is True and execution_mode == "brokered" and action.get("approval_required") is not True:
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_invalid",
                    "connectors.yaml",
                    (*path, "approval_required"),
                    f"{action_ref} brokered mutating external actions require approval_required: true",
                    expected="approval_required: true",
                )
            )

        if (
            side_effect is True
            and execution_mode == "brokered"
            and side_effect_class in EXTERNAL_CONNECTOR_IDEMPOTENCY_CLASSES
            and not _idempotency_key_fields(action)
        ):
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (*path, "idempotency", "key_fields"),
                    f"{action_ref} brokered mutating external actions require idempotency.key_fields",
                    expected="idempotency.key_fields",
                )
            )

    for env_ref in _invalid_env_refs(action_ref, connector.get("env")):
        issues.append(env_ref)
    for env_ref in _invalid_env_refs(action_ref, action.get("env")):
        issues.append(env_ref)
    return issues


def _iter_connector_actions(connectors_document: Mapping[str, Any]):
    for connector in connectors_document.get("connectors", []):
        if not isinstance(connector, Mapping):
            continue
        for action in connector.get("actions", []):
            if isinstance(action, Mapping):
                yield connector, action


def _action_ref(connector: Mapping[str, Any], action: Mapping[str, Any]) -> str | None:
    connector_id = connector.get("id")
    action_id = action.get("id")
    if not isinstance(connector_id, str) or not isinstance(action_id, str):
        return None
    return f"{connector_id}.{action_id}"


def _is_external_connector_candidate(connector: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    return (
        action.get("custom") is True
        or connector.get("custom") is True
        or isinstance(action.get("source"), str)
        or isinstance(connector.get("source"), str)
    )


def _is_design_only_action(action: Mapping[str, Any]) -> bool:
    return action.get("custom") is True and action.get("execution_mode") == "design_only"


def _is_versioned_source(value: str) -> bool:
    stripped = value.strip()
    if "://" not in stripped or "@" not in stripped:
        return False
    version = stripped.rsplit("@", 1)[1]
    return bool(version.strip())


def _env_refs(value: object) -> list[ExternalConnectorEnvRef]:
    if not isinstance(value, list):
        return []
    refs: list[ExternalConnectorEnvRef] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        purpose = entry.get("purpose")
        if not isinstance(name, str) or not name.startswith("APPV24_"):
            continue
        if not isinstance(purpose, str) or not purpose.strip():
            continue
        refs.append(ExternalConnectorEnvRef(env_name=name, purpose=purpose, required=entry.get("required") is not False))
    return refs


def _invalid_env_refs(action_ref: str, value: object) -> list[WorkflowIssue]:
    if value is None:
        return []
    issues: list[WorkflowIssue] = []
    if not isinstance(value, list):
        return [
            WorkflowIssue(
                "external_connector_contract_invalid",
                "connectors.yaml",
                (),
                f"{action_ref} env must be a list of env references",
                expected="env: [{name, purpose, required}]",
            )
        ]
    for entry in value:
        if not isinstance(entry, Mapping):
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_invalid",
                    "connectors.yaml",
                    (),
                    f"{action_ref} env entries must be objects",
                    expected="env entry object",
                )
            )
            continue
        name = entry.get("name")
        purpose = entry.get("purpose")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (),
                    f"{action_ref} env entries require name",
                    expected="APPV24_ env name",
                )
            )
        elif not name.startswith("APPV24_"):
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_invalid",
                    "connectors.yaml",
                    (),
                    f"{action_ref} env name must use APPV24_ namespace: {name}",
                    expected="APPV24_...",
                )
            )
        if not isinstance(purpose, str) or not purpose.strip():
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_missing",
                    "connectors.yaml",
                    (),
                    f"{action_ref} env entries require purpose",
                    expected="non-empty purpose",
                )
            )
        if "required" in entry and not isinstance(entry.get("required"), bool):
            issues.append(
                WorkflowIssue(
                    "external_connector_contract_invalid",
                    "connectors.yaml",
                    (),
                    f"{action_ref} env required must be boolean",
                    expected="true or false",
                )
            )
    return issues


def _allowed_outputs(action: Mapping[str, Any]) -> tuple[str, ...]:
    outputs = action.get("outputs")
    if not isinstance(outputs, Mapping):
        return ()
    allowed = outputs.get("allowed")
    return _string_tuple(allowed)


def _idempotency_key_fields(action: Mapping[str, Any]) -> tuple[str, ...]:
    idempotency = action.get("idempotency")
    if not isinstance(idempotency, Mapping):
        return ()
    return _string_tuple(idempotency.get("key_fields"))


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _contract(
    action_ref: str,
    *,
    input_fields: tuple[str, ...] = (),
    required_input_fields: tuple[str, ...] = (),
    allowed_outputs: tuple[str, ...],
    required_scopes: tuple[str, ...] = (),
    secrets: tuple[ConnectorSecretRef, ...] = (),
    side_effect_class: SideEffectClass = SideEffectClass.READ,
    approval_class: ApprovalClass = ApprovalClass.NONE,
    live_modes: tuple[ConnectorLiveMode, ...] = (),
    idempotency_key_fields: tuple[str, ...] = (),
    retry_class: RetryClass = RetryClass.NONE,
    target_policy: ConnectorTargetPolicy | None = None,
    prompt_summary: str,
    adapter: str = "direct_api",
    execution_mode: str = "brokered",
    dry_run_supported: bool = True,
    design_only: bool = False,
) -> ConnectorContract:
    connector_id, _, action_id = action_ref.partition(".")
    all_input_fields = tuple(dict.fromkeys((*required_input_fields, *input_fields)))
    return ConnectorContract(
        action_ref=action_ref,
        connector_id=connector_id,
        action_id=action_id,
        input_schema=_input_schema(all_input_fields, required_input_fields),
        required_input_fields=required_input_fields,
        output_schema={"type": "object", "additionalProperties": True},
        allowed_outputs=allowed_outputs,
        required_scopes=required_scopes,
        secrets=secrets,
        side_effect_class=side_effect_class,
        approval_class=approval_class,
        live_modes=live_modes,
        idempotency_key_fields=idempotency_key_fields,
        retry_class=retry_class,
        target_policy=target_policy or ConnectorTargetPolicy(),
        prompt_summary=prompt_summary,
        adapter=adapter,
        execution_mode=execution_mode,
        dry_run_supported=dry_run_supported,
        design_only=design_only,
    )


def _input_schema(input_fields: tuple[str, ...], required_input_fields: tuple[str, ...] = ()) -> dict[str, object]:
    if not input_fields:
        return {"type": "object", "additionalProperties": True}
    schema: dict[str, object] = {
        "type": "object",
        "properties": {field: {} for field in input_fields},
        "additionalProperties": False,
    }
    if required_input_fields:
        schema["required"] = list(required_input_fields)
    return schema
