from __future__ import annotations

from pathlib import Path

from appv24.workflows.catalog import built_in_capability_catalog, is_builtin_workflow_action
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.capabilities import build_connector_capabilities
from appv24.workflows.connector_contracts import (
    ApprovalClass,
    ConnectorLiveMode,
    SideEffectClass,
    builtin_connector_contract_catalog,
    external_connector_env_requirements,
    workflow_operator_env_from_env,
    workflow_operator_env_names,
)
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.policy import evaluate_policy
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
    assert ConnectorLiveMode.LIVE_DRAFT in contract.live_modes
    assert ConnectorLiveMode.LIVE_PRODUCTION not in contract.live_modes
    assert contract.idempotency_key_fields == ("to", "subject")
    assert contract.required_scopes == ("gmail.compose",)


def test_telegram_send_message_contract_requires_target_policy() -> None:
    contract = builtin_connector_contract_catalog()["telegram.send_message"]

    assert contract.approval_class is ApprovalClass.EXTERNAL_SEND
    assert contract.side_effect_class is SideEffectClass.SEND
    assert contract.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert contract.idempotency_key_fields == ("chat_id", "text")
    assert ConnectorLiveMode.LIVE_PRODUCTION in contract.live_modes


def test_telegram_subscriber_and_broadcast_contracts_are_connector_interfaces() -> None:
    catalog = builtin_connector_contract_catalog()

    subscribers = catalog["telegram.list_subscribers"]
    assert subscribers.approval_class is ApprovalClass.NONE
    assert subscribers.side_effect_class is SideEffectClass.READ
    assert subscribers.required_scopes == ("telegram:subscribers:read",)
    assert subscribers.allowed_outputs == ("recipients", "recipient_count", "audience_hash", "artifact_path")
    assert subscribers.secrets[0].env_name == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert ConnectorLiveMode.LIVE_READ in subscribers.live_modes
    assert subscribers.prompt_summary == (
        "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash"
    )

    broadcast = catalog["telegram.broadcast_message"]
    assert broadcast.approval_class is ApprovalClass.EXTERNAL_SEND
    assert broadcast.side_effect_class is SideEffectClass.SEND
    assert broadcast.required_scopes == ("telegram:send",)
    assert broadcast.required_input_fields == ("recipients", "audience_hash", "text")
    assert broadcast.idempotency_key_fields == ("audience_hash", "text")
    assert broadcast.target_policy.allowed_chat_ids_env == "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
    assert broadcast.allowed_outputs == ("sent_count", "message_ids", "chat_ids", "artifact_path")
    assert ConnectorLiveMode.LIVE_PRODUCTION in broadcast.live_modes
    assert broadcast.prompt_summary == (
        "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids"
    )


def test_compile_uses_builtin_connector_outputs_when_package_omits_them() -> None:
    documents = _telegram_broadcast_documents(idempotency_key_fields=["audience_hash", "text"])
    telegram = documents.connectors["connectors"][0]
    list_subscribers = telegram["actions"][0]
    list_subscribers.pop("outputs")
    documents.permissions["approvals"][0]["mode"] = "explicit"
    documents.permissions["permissions"].append({"connector": "llm", "actions": ["summarize"], "scopes": []})

    compiled = compile_workflow_documents(documents)

    assert compiled.ok
    assert compiled.ir is not None
    action = compiled.ir.connectors["telegram"].actions["list_subscribers"]
    assert "recipients" in action.allowed_outputs
    assert "audience_hash" in action.allowed_outputs


def test_compile_preserves_explicit_package_output_allowlist() -> None:
    capabilities = build_connector_capabilities(
        {
            "connectors": [
                {
                    "id": "ticket",
                    "adapter": "manual",
                    "actions": [
                        {
                            "id": "create_draft",
                            "side_effect": True,
                            "outputs": {"allowed": ["ticket_id"]},
                        }
                    ],
                }
            ]
        },
        {"permissions": []},
    )

    assert capabilities["ticket"].actions["create_draft"].allowed_outputs == ("ticket_id",)


def test_brave_search_contract_is_read_only_live_read() -> None:
    contract = builtin_connector_contract_catalog()["brave.search"]

    assert contract.approval_class is ApprovalClass.NONE
    assert contract.side_effect_class is SideEffectClass.READ
    assert contract.required_scopes == ("brave:search",)
    assert contract.required_input_fields == ("query",)
    assert contract.secrets[0].env_name == "APPV24_BRAVE_SEARCH_API_KEY"
    assert ConnectorLiveMode.LIVE_READ in contract.live_modes
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
    assert (
        "workflow.yaml steps[].uses values must match one built-in action or a strict external action declared in connectors.yaml"
        in fragment
    )
    assert "Non-built-in actions require a connector-owned interface: custom: true, source@version" in fragment
    assert "live still needs transport/preflight" in fragment
    assert "For public web/news search, prefer brave.search over placeholder source.fetch_items URLs." in fragment
    assert (
        "for telegram.broadcast_message set with.recipients, with.audience_hash, and with.text"
        in fragment
    )
    assert (
        "Built-in connector idempotency.key_fields must match the connector contract; "
        "telegram.broadcast_message uses audience_hash, text."
    ) in fragment
    assert "Use draft actions only when the user asks for a draft/review-only workflow:" in fragment
    assert "Prefer draft actions for user-visible messages" not in fragment
    assert "telegram.list_subscribers(input: audience, limit) -> recipients, recipient_count, audience_hash" in fragment
    assert "telegram.broadcast_message(input: recipients, text) -> sent_count, message_ids, chat_ids" in fragment
    assert "For telegram registered-user workflows, use telegram.list_subscribers before telegram.broadcast_message." in fragment
    assert (
        "Registered-user delivery must be modeled as connector interfaces: "
        "telegram.list_subscribers then telegram.broadcast_message."
    ) in fragment
    assert (
        "Approval for registered-user telegram delivery belongs on telegram.broadcast_message, "
        "not on a separate manual approval step."
    ) in fragment
    assert (
        "Do not insert telegram.create_message_draft, policy.check, manual_approval, decision, "
        "or other connector/action gates between "
        "telegram.list_subscribers and telegram.broadcast_message."
    ) in fragment
    assert "Only pure LLM message transforms may appear between telegram.list_subscribers and telegram.broadcast_message." in fragment
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


def test_semantics_reject_builtin_connector_idempotency_mismatch() -> None:
    issues = validate_semantics(_telegram_broadcast_documents(idempotency_key_fields=["message_content_checksum", "recipient_list_checksum"]))

    assert any(issue.code == "connector_contract_idempotency_mismatch" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "telegram.broadcast_message idempotency.key_fields must match connector contract: audience_hash, text" in messages


def test_semantics_requires_connector_owned_idempotency_inputs_on_workflow_step() -> None:
    documents = _telegram_broadcast_documents(
        idempotency_key_fields=["audience_hash", "text"],
        broadcast_with={"recipients": "{{ get_subscribers.recipients }}", "text": "{{ summarize.summary }}"},
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "missing_connector_required_input" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "telegram.broadcast_message requires workflow.yaml step send_broadcast to bind audience_hash" in messages


def test_semantics_requires_llm_summarize_input_binding() -> None:
    documents = _telegram_broadcast_documents(
        idempotency_key_fields=["audience_hash", "text"],
        summarize_with={"output_contract": "summary_v1"},
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "missing_llm_input" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "LLM step summarize using llm.summarize requires one of with.text, with.items, or with.input_ref" in messages


def test_semantics_accepts_strict_external_connector_interface() -> None:
    documents = _external_crm_documents()

    issues = validate_semantics(documents)
    compiled = compile_workflow_documents(documents)
    policy = evaluate_policy(compiled.ir) if compiled.ir is not None else None

    assert not any(issue.code in {"unknown_catalog_action", "unknown_connector_contract"} for issue in issues)
    assert not any(issue.code.startswith("external_connector_contract") for issue in issues)
    assert external_connector_env_requirements(documents.connectors) == ("APPV24_CRM_MCP_SERVER",)
    assert compiled.ok, [issue.message for issue in compiled.issues]
    assert policy is not None and policy.ok, [issue.message for issue in policy.issues]


def test_semantics_rejects_external_connector_without_versioned_source() -> None:
    documents = _external_crm_documents()
    connectors = {
        **documents.connectors,
        "connectors": [
            {
                **documents.connectors["connectors"][0],
                "source": "mcp://crm",
                "actions": [
                    {
                        **documents.connectors["connectors"][0]["actions"][0],
                        "source": "mcp://crm.lookup_contact",
                    }
                ],
            }
        ],
    }
    documents = PackageDocuments(
        root=documents.root,
        workflow=documents.workflow,
        connectors=connectors,
        permissions=documents.permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )

    issues = validate_semantics(documents)

    codes = {issue.code for issue in issues}
    messages = "\n".join(issue.message for issue in issues)
    assert "external_connector_contract_invalid" in codes
    assert "crm.lookup_contact source must be a versioned connector URI" in messages
    assert "unknown_catalog_action" not in codes
    assert "unknown_connector_contract" not in codes


def test_semantics_rejects_external_connector_env_outside_appv24_namespace() -> None:
    documents = _external_crm_documents()
    connectors = {
        **documents.connectors,
        "connectors": [
            {
                **documents.connectors["connectors"][0],
                "env": [{"name": "CRM_MCP_SERVER", "purpose": "CRM MCP server URL", "required": True}],
            }
        ],
    }
    documents = PackageDocuments(
        root=documents.root,
        workflow=documents.workflow,
        connectors=connectors,
        permissions=documents.permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "external_connector_contract_invalid" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact env name must use APPV24_ namespace: CRM_MCP_SERVER" in messages


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


def _telegram_broadcast_documents(
    *,
    idempotency_key_fields: list[str],
    broadcast_with: dict[str, object] | None = None,
    summarize_with: dict[str, object] | None = None,
) -> PackageDocuments:
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "telegram_registered_news",
            "description": "Broadcast a summarized news digest to registered Telegram users.",
            "design": {
                "intent": "Broadcast a summarized news digest to registered Telegram users.",
                "trigger": "Manual run.",
                "inputs": ["News digest text."],
                "outputs": ["Telegram broadcast result."],
                "assumptions": ["Registered recipients are resolved by the Telegram subscriber connector."],
                "side_effects": ["Send a Telegram message to registered users."],
                "approval_gates": ["approve_broadcast"],
                "failure_modes": ["Telegram send fails."],
                "rollback": ["Notify operator; Telegram sends cannot be undone."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "get_subscribers",
            "steps": [
                {
                    "id": "get_subscribers",
                    "uses": "telegram.list_subscribers",
                    "with": {"audience": "registered_users"},
                    "next": "summarize",
                },
                {
                    "id": "summarize",
                    "uses": "llm.summarize",
                    "with": summarize_with or {"text": "Digest", "output_contract": "summary_v1"},
                    "next": "send_broadcast",
                },
                {
                    "id": "send_broadcast",
                    "uses": "telegram.broadcast_message",
                    "side_effect_class": "send",
                    "approval": "approve_broadcast",
                    "with": broadcast_with
                    or {
                        "recipients": "{{ get_subscribers.recipients }}",
                        "audience_hash": "{{ get_subscribers.audience_hash }}",
                        "text": "{{ summarize.summary }}",
                    },
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "telegram",
                    "adapter": "direct_api",
                    "actions": [
                        {
                            "id": "list_subscribers",
                            "side_effect": False,
                            "required_scopes": ["telegram:subscribers:read"],
                            "outputs": {"allowed": ["recipients", "recipient_count", "audience_hash"]},
                        },
                        {
                            "id": "broadcast_message",
                            "execution_mode": "brokered",
                            "side_effect": True,
                            "side_effect_class": "send",
                            "approval_required": True,
                            "dry_run_supported": True,
                            "required_scopes": ["telegram:send"],
                            "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids"]},
                            "idempotency": {"key_fields": idempotency_key_fields},
                        },
                    ],
                },
                {
                    "id": "llm",
                    "adapter": "llm",
                    "actions": [
                        {
                            "id": "summarize",
                            "side_effect": False,
                            "outputs": {"allowed": ["summary", "raw_response"]},
                        }
                    ],
                },
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [
                {
                    "connector": "telegram",
                    "actions": ["list_subscribers", "broadcast_message"],
                    "scopes": ["telegram:subscribers:read", "telegram:send"],
                }
            ],
            "approvals": [{"id": "approve_broadcast", "required_for": ["send_broadcast"]}],
        },
        dry_run_fixtures={
            "happy_path.yaml": {"expected_path": ["get_subscribers", "summarize", "send_broadcast", "done"]},
            "rejection_path.yaml": {"expected_path": ["get_subscribers", "summarize", "send_broadcast", "done"]},
        },
        rollback_text="Telegram sends cannot be undone; notify the operator if a wrong message is sent.",
    )


def _external_crm_documents() -> PackageDocuments:
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "crm_lookup",
            "description": "Look up a CRM contact through an external connector interface.",
            "design": {
                "intent": "Look up a CRM contact by email.",
                "trigger": "Manual support run.",
                "inputs": ["Customer email address."],
                "outputs": ["CRM contact summary."],
                "assumptions": ["The CRM connector interface is versioned and operator configured."],
                "side_effects": ["Read-only CRM lookup."],
                "approval_gates": ["None."],
                "failure_modes": ["CRM connector unavailable."],
                "rollback": ["No external mutation occurs."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "lookup_contact",
            "steps": [
                {
                    "id": "lookup_contact",
                    "uses": "crm.lookup_contact",
                    "with": {"email": {"inputRef": "email"}},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "crm",
                    "adapter": "mcp",
                    "source": "mcp://crm@v1",
                    "description": "External CRM MCP connector interface.",
                    "env": [
                        {
                            "name": "APPV24_CRM_MCP_SERVER",
                            "purpose": "CRM MCP server endpoint",
                            "required": True,
                        }
                    ],
                    "actions": [
                        {
                            "id": "lookup_contact",
                            "custom": True,
                            "source": "mcp://crm.lookup_contact@v1",
                            "tool": "crm.lookup_contact",
                            "side_effect": False,
                            "side_effect_class": "read",
                            "execution_mode": "brokered",
                            "dry_run_supported": True,
                            "required_scopes": ["crm:read"],
                            "outputs": {"allowed": ["contact", "artifact_path"]},
                        }
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"email": "customer@example.com"}}},
        rollback_text=None,
    )
