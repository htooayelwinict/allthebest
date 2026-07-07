"""Workflow connector execution contracts.

This module is the source of truth for built-in workflow action vocabulary.
Adapters such as connector_packs.py and catalog_builtins.py expose legacy views
derived from these contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class WorkflowLiveMode(StrEnum):
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
class ConnectorTargetPolicy:
    allow_env: str | None = None
    allowed_schemes: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_chat_ids_env: str | None = None
    max_response_bytes: int | None = None
    follow_redirects: bool = False


@dataclass(frozen=True)
class ConnectorContract:
    action_ref: str
    connector_id: str
    action_id: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    allowed_outputs: tuple[str, ...]
    required_scopes: tuple[str, ...]
    secrets: tuple[ConnectorSecretRef, ...]
    side_effect_class: SideEffectClass
    approval_class: ApprovalClass
    live_modes: tuple[WorkflowLiveMode, ...]
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


def builtin_connector_contract_catalog() -> dict[str, ConnectorContract]:
    contracts = (
        _contract(
            "brave.search",
            input_fields=("query", "q", "freshness", "count", "country", "search_lang", "extra_snippets"),
            allowed_outputs=("items", "source_count", "query", "artifact_path"),
            required_scopes=("brave:search",),
            secrets=(ConnectorSecretRef("APPV24_BRAVE_SEARCH_API_KEY", "Brave Search API key"),),
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            adapter="direct_api",
            prompt_summary="brave.search(input: query, freshness, count) -> items, source_count",
        ),
        _contract(
            "source.fetch_document",
            allowed_outputs=("document", "title", "url", "metadata", "artifact_path"),
            required_scopes=("source:read",),
            secrets=(ConnectorSecretRef("APPV24_SOURCE_BASE_URL", "operator source base URL"),),
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_schemes=("http", "https"), follow_redirects=False),
            prompt_summary="source.fetch_document(input: source_url/path/query) -> document",
        ),
        _contract(
            "source.fetch_items",
            allowed_outputs=("items", "source_count", "metadata", "artifact_path"),
            required_scopes=("source:read",),
            secrets=(ConnectorSecretRef("APPV24_SOURCE_BASE_URL", "operator source base URL"),),
            live_modes=(WorkflowLiveMode.LIVE_READ,),
            retry_class=RetryClass.READ_SAFE,
            target_policy=ConnectorTargetPolicy(allowed_schemes=("http", "https"), follow_redirects=False),
            prompt_summary="source.fetch_items(input: source_url/path/query) -> items, source_count",
        ),
        _contract(
            "http.fetch",
            allowed_outputs=("status_code", "url", "text", "json", "artifact_path"),
            required_scopes=("http:read",),
            live_modes=(WorkflowLiveMode.LIVE_READ,),
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
            live_modes=(WorkflowLiveMode.LIVE_READ,),
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
            live_modes=(WorkflowLiveMode.LIVE_DRAFT,),
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
            live_modes=(WorkflowLiveMode.LIVE_DRAFT,),
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
            live_modes=(WorkflowLiveMode.LIVE_DRAFT,),
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
            live_modes=(WorkflowLiveMode.LIVE_DRAFT,),
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
            allowed_outputs=("message_id", "chat_id", "artifact_path"),
            required_scopes=("telegram:send",),
            secrets=(ConnectorSecretRef("APPV24_TELEGRAM_BOT_TOKEN", "Telegram bot token"),),
            side_effect_class=SideEffectClass.SEND,
            approval_class=ApprovalClass.EXTERNAL_SEND,
            live_modes=(WorkflowLiveMode.LIVE_PRODUCTION,),
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


def _contract(
    action_ref: str,
    *,
    input_fields: tuple[str, ...] = (),
    allowed_outputs: tuple[str, ...],
    required_scopes: tuple[str, ...] = (),
    secrets: tuple[ConnectorSecretRef, ...] = (),
    side_effect_class: SideEffectClass = SideEffectClass.READ,
    approval_class: ApprovalClass = ApprovalClass.NONE,
    live_modes: tuple[WorkflowLiveMode, ...] = (),
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
    return ConnectorContract(
        action_ref=action_ref,
        connector_id=connector_id,
        action_id=action_id,
        input_schema=_input_schema(input_fields),
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


def _input_schema(input_fields: tuple[str, ...]) -> dict[str, object]:
    if not input_fields:
        return {"type": "object", "additionalProperties": True}
    return {
        "type": "object",
        "properties": {field: {} for field in input_fields},
        "additionalProperties": False,
    }
