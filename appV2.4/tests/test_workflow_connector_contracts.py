from __future__ import annotations

from pathlib import Path

from appv24.workflows.catalog import built_in_capability_catalog, is_builtin_workflow_action
from appv24.workflows.connector_contracts import (
    ApprovalClass,
    SideEffectClass,
    WorkflowLiveMode,
    builtin_connector_contract_catalog,
    workflow_operator_env_from_env,
    workflow_operator_env_names,
)
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.prompt_fragments import render_capability_catalog_fragment
from appv24.workflows.semantic import validate_semantics


def test_builtin_contract_catalog_is_default_deny() -> None:
    catalog = builtin_connector_contract_catalog()

    assert "brave.search" in catalog
    assert "source.fetch_items" in catalog
    assert "http.fetch" in catalog
    assert "gmail.create_draft" in catalog
    assert "telegram.send_message" in catalog
    assert "slack.send_message" not in catalog


def test_gmail_create_draft_contract_is_draft_only() -> None:
    contract = builtin_connector_contract_catalog()["gmail.create_draft"]

    assert contract.approval_class is ApprovalClass.DRAFT
    assert contract.side_effect_class is SideEffectClass.CREATE
    assert WorkflowLiveMode.LIVE_DRAFT in contract.live_modes
    assert WorkflowLiveMode.LIVE_PRODUCTION not in contract.live_modes
    assert contract.idempotency_key_fields == ("to", "subject")
    assert contract.required_scopes == ("gmail.compose",)


def test_telegram_send_message_contract_requires_target_policy() -> None:
    contract = builtin_connector_contract_catalog()["telegram.send_message"]

    assert contract.approval_class is ApprovalClass.EXTERNAL_SEND
    assert contract.side_effect_class is SideEffectClass.SEND
    assert contract.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert contract.idempotency_key_fields == ("chat_id", "text")
    assert WorkflowLiveMode.LIVE_PRODUCTION in contract.live_modes


def test_telegram_subscriber_and_broadcast_contracts_are_connector_interfaces() -> None:
    catalog = builtin_connector_contract_catalog()

    subscribers = catalog["telegram.list_subscribers"]
    assert subscribers.approval_class is ApprovalClass.NONE
    assert subscribers.side_effect_class is SideEffectClass.READ
    assert subscribers.required_scopes == ("telegram:subscribers:read",)
    assert subscribers.allowed_outputs == ("recipients", "recipient_count", "audience_hash", "artifact_path")
    assert subscribers.secrets[0].env_name == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert WorkflowLiveMode.LIVE_READ in subscribers.live_modes
    assert subscribers.prompt_summary == (
        "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash"
    )

    broadcast = catalog["telegram.broadcast_message"]
    assert broadcast.approval_class is ApprovalClass.EXTERNAL_SEND
    assert broadcast.side_effect_class is SideEffectClass.SEND
    assert broadcast.required_scopes == ("telegram:send",)
    assert broadcast.idempotency_key_fields == ("audience_hash", "text")
    assert broadcast.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert broadcast.allowed_outputs == ("sent_count", "message_ids", "chat_ids", "artifact_path")
    assert WorkflowLiveMode.LIVE_PRODUCTION in broadcast.live_modes
    assert broadcast.prompt_summary == (
        "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids"
    )


def test_brave_search_contract_is_read_only_live_read() -> None:
    contract = builtin_connector_contract_catalog()["brave.search"]

    assert contract.approval_class is ApprovalClass.NONE
    assert contract.side_effect_class is SideEffectClass.READ
    assert contract.required_scopes == ("brave:search",)
    assert contract.secrets[0].env_name == "APPV24_BRAVE_SEARCH_API_KEY"
    assert WorkflowLiveMode.LIVE_READ in contract.live_modes
    assert contract.prompt_summary == "brave.search(input: query, freshness, count) -> items, source_count"


def test_workflow_operator_env_uses_only_target_policy_env_names() -> None:
    source_env = {
        "APPV24_HTTP_FETCH_ALLOWED_HOSTS": "example.com",
        "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat",
        "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-prod",
        "APPV24_TELEGRAM_SCOPES": "telegram:send",
        "OPENROUTER_API_KEY": "provider-secret",
    }

    assert workflow_operator_env_names() == (
        "APPV24_HTTP_FETCH_ALLOWED_HOSTS",
        "APPV24_TELEGRAM_ALLOWED_CHAT_IDS",
    )
    assert workflow_operator_env_from_env(source_env) == {
        "APPV24_HTTP_FETCH_ALLOWED_HOSTS": "example.com",
        "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "admin-chat",
    }


def test_connector_metadata_and_capabilities_are_derived_from_contracts() -> None:
    connector_catalog = builtin_connector_catalog()
    capability_catalog = built_in_capability_catalog()

    http_metadata = connector_catalog.action("http.fetch")
    assert http_metadata.side_effect is False
    assert http_metadata.required_scopes == ("http:read",)
    assert http_metadata.allowed_outputs == ("status_code", "url", "text", "json", "artifact_path")
    assert http_metadata.live_read_eligible is True

    http_capability = capability_catalog.require("http.fetch")
    assert http_capability.side_effect is False
    assert http_capability.required_scopes == ("http:read",)
    assert http_capability.idempotency_key_fields == ()
    assert is_builtin_workflow_action("http.fetch")


def test_capability_prompt_uses_slim_contract_summaries() -> None:
    fragment = render_capability_catalog_fragment()

    assert "brave.search(input: query, freshness, count) -> items, source_count" in fragment
    assert "For public web/news search, prefer brave.search over placeholder source.fetch_items URLs." in fragment
    assert "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash" in fragment
    assert "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids" in fragment
    assert "For Telegram registered-user workflows, use telegram.list_subscribers before telegram.broadcast_message." in fragment
    assert "fetch_user_list" not in fragment
    assert "format_news_digest" not in fragment
    assert "http.fetch(input: url, query) -> status_code, url, text/json" in fragment
    assert "gmail.create_draft(input: to, subject, body) -> draft_id, thread_id" in fragment
    assert "ConnectorContract(" not in fragment
    assert "input_schema" not in fragment


def test_semantics_reject_contract_scope_mismatch() -> None:
    issues = validate_semantics(_http_fetch_documents(required_scopes=[], permission_scopes=[]))

    messages = "\n".join(issue.message for issue in issues)
    assert "http.fetch required_scopes must include http:read" in messages
    assert "permissions for http do not grant http:read required by fetch" in messages


def test_semantics_reject_permission_wildcard_scope() -> None:
    issues = validate_semantics(_http_fetch_documents(required_scopes=["http:read"], permission_scopes=["*"]))

    messages = "\n".join(issue.message for issue in issues)
    assert "permissions for http must not use wildcard scope; grant explicit required scopes" in messages
    assert "permissions for http do not grant http:read required by fetch" in messages


def _http_fetch_documents(*, required_scopes: list[str], permission_scopes: list[str]) -> PackageDocuments:
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "http_fetch_news",
            "description": "Fetch approved public news data.",
            "design": {
                "intent": "Fetch approved public news data.",
                "trigger": "Manual run.",
                "inputs": ["Approved URL."],
                "outputs": ["Fetched response."],
                "assumptions": ["The URL host is operator-approved."],
                "side_effects": ["Read-only HTTP GET."],
                "approval_gates": ["None."],
                "failure_modes": ["HTTP request fails."],
                "rollback": ["No external mutation to roll back."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "fetch_news",
            "steps": [
                {
                    "id": "fetch_news",
                    "uses": "http.fetch",
                    "with": {"url": "https://example.com/news.json"},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "http",
                    "adapter": "direct_api",
                    "actions": [
                        {
                            "id": "fetch",
                            "side_effect": False,
                            "required_scopes": required_scopes,
                            "outputs": {"allowed": ["status_code", "url", "text"]},
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "http", "actions": ["fetch"], "scopes": permission_scopes}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"url": "https://example.com/news.json"}}},
        rollback_text=None,
    )
