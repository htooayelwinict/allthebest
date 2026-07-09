from __future__ import annotations

from copy import deepcopy
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
from appv24.workflows.workflow_extensions import extension_connector_contract_catalog


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
    assert (
        'appv24 workflow references must be quoted double-brace strings without a dollar sign: '
        '"{{ steps.<step_id>.outputs.<field> }}"; never use GitHub-style "${{ ... }}".'
    ) in fragment
    assert (
        "For AI-news registered telegram workflows, bind exactly: "
        'items: "{{ steps.search_ai_news.outputs.items }}", '
        'recipients: "{{ steps.list_subscribers.outputs.recipients }}", '
        'audience_hash: "{{ steps.list_subscribers.outputs.audience_hash }}", '
        'text: "{{ steps.summarize_news.outputs.summary }}".'
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


def test_semantics_reject_permission_scopes_not_declared_by_connector_actions() -> None:
    issues = validate_semantics(
        _telegram_broadcast_documents(
            idempotency_key_fields=["audience_hash", "text"],
            permission_scopes=[
                "bot_access",
                "send_messages",
                "telegram:send",
                "telegram:subscribers:read",
            ],
        )
    )

    messages = "\n".join(issue.message for issue in issues)
    assert "permissions for telegram grant unsupported scope bot_access" in messages
    assert "permissions for telegram grant unsupported scope send_messages" in messages


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


def test_semantics_uses_extension_required_input_hints_for_custom_connectors() -> None:
    documents = _external_crm_documents(
        lookup_with={},
        workflow_hints={"required_input_fields": ["email"], "input_fields": ["email"]},
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "missing_connector_required_input" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact requires workflow.yaml step lookup_contact to bind email" in messages


def test_semantics_uses_extension_required_input_any_hints_for_custom_connectors() -> None:
    documents = _external_crm_documents(
        lookup_with={},
        workflow_hints={
            "input_fields": ["email", "customer_id"],
            "required_input_any": [["email", "customer_id"]],
        },
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "missing_connector_required_input_any" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact requires workflow.yaml step lookup_contact to bind one of email, customer_id" in messages


def test_semantics_accepts_extension_required_input_any_when_one_field_is_bound() -> None:
    documents = _external_crm_documents(
        lookup_with={"customer_id": {"inputRef": "customer_id"}},
        workflow_hints={
            "input_fields": ["email", "customer_id"],
            "required_input_any": [["email", "customer_id"]],
        },
    )

    issues = validate_semantics(documents)

    assert not any(issue.code == "missing_connector_required_input_any" for issue in issues)


def test_semantics_uses_extension_scalar_input_hints_for_custom_connectors() -> None:
    documents = _external_crm_documents(
        lookup_with={"email": "{{ inputs.email }}"},
        workflow_hints={"required_input_fields": ["email"], "scalar_input_fields": ["email"]},
        fixture_input={"email": ["primary@example.com", "secondary@example.com"]},
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "connector_scalar_input_collection" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact email must resolve to a scalar value" in messages


def test_semantics_requires_qa_for_coder_generated_custom_connector() -> None:
    documents = _external_crm_documents(
        workflow_hints={"generated_by": "coding_profile", "qa_required": True},
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "extension_qa_missing" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact requires passed extension QA evidence" in messages


def test_semantics_requires_coder_generated_connector_to_be_used_by_workflow() -> None:
    base = _external_crm_documents(
        workflow_hints={"generated_by": "coding_profile", "qa_required": True},
    )
    workflow = deepcopy(base.workflow)
    workflow["entrypoint"] = "done"
    workflow["steps"] = [{"id": "done", "type": "terminal"}]
    documents = PackageDocuments(
        root=base.root,
        workflow=workflow,
        connectors=base.connectors,
        permissions=base.permissions,
        dry_run_fixtures=base.dry_run_fixtures,
        rollback_text=base.rollback_text,
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "orphan_generated_connector_action" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "crm.lookup_contact is generated by the coding profile but no workflow step calls it" in messages


def test_semantics_requires_design_declared_crm_to_be_modeled_as_connector_step() -> None:
    base = _external_crm_documents()
    workflow = deepcopy(base.workflow)
    workflow["entrypoint"] = "done"
    workflow["steps"] = [{"id": "done", "type": "terminal"}]
    documents = PackageDocuments(
        root=base.root,
        workflow=workflow,
        connectors={"version": "appv24.connectors/v1", "connectors": []},
        permissions={"version": "appv24.permissions/v1", "permissions": []},
        dry_run_fixtures=base.dry_run_fixtures,
        rollback_text=base.rollback_text,
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "declared_external_system_unmodeled" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "workflow design mentions CRM but no crm connector step models it" in messages


def test_semantics_rejects_step_output_reference_to_non_predecessor_step() -> None:
    documents = _external_crm_documents()
    workflow = deepcopy(documents.workflow)
    workflow["steps"][0]["with"] = {"email": "{{ steps.fetch_customer.outputs.email }}"}
    workflow["steps"].insert(1, {"id": "fetch_customer", "type": "manual", "next": "done"})
    documents = PackageDocuments(
        root=documents.root,
        workflow=workflow,
        connectors=documents.connectors,
        permissions=documents.permissions,
        dry_run_fixtures=documents.dry_run_fixtures,
        rollback_text=documents.rollback_text,
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "future_step_output_reference" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "lookup_contact references outputs from fetch_customer, but fetch_customer does not run before lookup_contact" in messages


def test_semantics_rejects_approval_required_for_unknown_step() -> None:
    base = _external_crm_documents()
    permissions = deepcopy(base.permissions)
    permissions["approvals"] = [
        {"id": "approve_crm_access", "mode": "explicit", "required_for": ["lookup_subscriber"]}
    ]
    documents = PackageDocuments(
        root=base.root,
        workflow=base.workflow,
        connectors=base.connectors,
        permissions=permissions,
        dry_run_fixtures=base.dry_run_fixtures,
        rollback_text=base.rollback_text,
    )

    issues = validate_semantics(documents)

    assert any(issue.code == "unknown_approval_target" for issue in issues)
    messages = "\n".join(issue.message for issue in issues)
    assert "approval approve_crm_access targets unknown workflow step lookup_subscriber" in messages


def test_semantics_accepts_coder_generated_custom_connector_with_passed_qa() -> None:
    documents = _external_crm_documents(
        workflow_hints={"generated_by": "coding_profile", "qa_required": True},
        qa=_passed_extension_qa("connector", "crm.lookup_contact"),
    )

    issues = validate_semantics(documents)

    assert not any(issue.code.startswith("extension_qa") for issue in issues)


def test_semantics_requires_generated_connector_qa_to_match_scopes_and_outputs() -> None:
    qa = _passed_extension_qa("connector", "crm.lookup_contact")
    qa.pop("contract", None)
    documents = _external_crm_documents(
        workflow_hints={"generated_by": "coding_profile", "qa_required": True},
        qa=qa,
    )

    issues = validate_semantics(documents)

    codes = {issue.code for issue in issues}
    assert "extension_qa_required_scopes" in codes
    assert "extension_qa_allowed_outputs" in codes


def test_extension_connector_contract_uses_required_target_field_hints() -> None:
    documents = _external_crm_send_documents(
        workflow_hints={
            "input_fields": ["customer_id", "text"],
            "required_input_fields": ["customer_id", "text"],
            "required_target_fields": ["customer_id"],
        }
    )

    contracts = extension_connector_contract_catalog(documents)

    assert contracts["crm.send_customer_update"].target_policy.required_target_fields == ("customer_id",)


def test_extension_connector_contract_catalog_blocks_generated_action_without_passed_qa() -> None:
    documents = _external_crm_send_documents(
        workflow_hints={
            "generated_by": "coding_profile",
            "qa_required": True,
            "input_fields": ["customer_id", "text"],
        },
    )

    contracts = extension_connector_contract_catalog(documents)

    assert "crm.send_customer_update" not in contracts


def test_extension_connector_contract_catalog_accepts_generated_action_with_passed_qa() -> None:
    documents = _external_crm_send_documents(
        workflow_hints={
            "generated_by": "coding_profile",
            "qa_required": True,
            "input_fields": ["customer_id", "text"],
        },
        qa=_passed_extension_qa("connector", "crm.send_customer_update"),
    )

    contracts = extension_connector_contract_catalog(documents)

    assert contracts["crm.send_customer_update"].live_modes == (ConnectorLiveMode.LIVE_PRODUCTION,)


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
    permission_scopes: list[str] | None = None,
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
                    "scopes": permission_scopes or ["telegram:subscribers:read", "telegram:send"],
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


def _external_crm_documents(
    *,
    lookup_with: dict[str, object] | None = None,
    workflow_hints: dict[str, object] | None = None,
    fixture_input: dict[str, object] | None = None,
    qa: dict[str, object] | None = None,
) -> PackageDocuments:
    lookup_action: dict[str, object] = {
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
    if workflow_hints is not None:
        lookup_action["workflow_hints"] = workflow_hints
    if qa is not None:
        lookup_action["qa"] = qa
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
                    "with": {"email": {"inputRef": "email"}} if lookup_with is None else lookup_with,
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
                        lookup_action
                    ],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": fixture_input or {"email": "customer@example.com"}}},
        rollback_text=None,
    )


def _external_crm_send_documents(
    *,
    workflow_hints: dict[str, object],
    qa: dict[str, object] | None = None,
) -> PackageDocuments:
    action: dict[str, object] = {
        "id": "send_customer_update",
        "custom": True,
        "source": "mcp://crm.send_customer_update@v1",
        "tool": "crm.send_customer_update",
        "side_effect": True,
        "side_effect_class": "send",
        "execution_mode": "brokered",
        "live_modes": ["live-production"],
        "approval_required": True,
        "dry_run_supported": True,
        "required_scopes": ["crm:send"],
        "idempotency": {"key_fields": ["text"]},
        "outputs": {"allowed": ["message_id", "artifact_path"]},
        "workflow_hints": workflow_hints,
    }
    if qa is not None:
        action["qa"] = qa
    return PackageDocuments(
        root=Path("<test>"),
        workflow={
            "version": "appv24.workflow/v1",
            "name": "crm_send",
            "description": "Send a CRM customer update through an external connector interface.",
            "design": {
                "intent": "Send a customer update.",
                "trigger": "Manual run.",
                "inputs": ["Customer id.", "Message text."],
                "outputs": ["CRM message id."],
                "assumptions": ["The CRM connector interface is versioned and operator configured."],
                "side_effects": ["Send one CRM customer message."],
                "approval_gates": ["Explicit operator approval before send."],
                "failure_modes": ["CRM connector unavailable."],
                "rollback": ["Send a corrective CRM update manually."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "send_customer_update",
            "steps": [
                {
                    "id": "send_customer_update",
                    "uses": "crm.send_customer_update",
                    "side_effect_class": "send",
                    "approval": "approve_crm_send",
                    "with": {"customer_id": {"inputRef": "customer_id"}, "text": {"inputRef": "text"}},
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
                    "custom": True,
                    "source": "mcp://crm@v1",
                    "actions": [action],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["send_customer_update"], "scopes": ["crm:send"]}],
            "approvals": [{"id": "approve_crm_send", "required_for": ["send_customer_update"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"customer_id": "customer-1", "text": "Hello"}}},
        rollback_text="Send a corrective CRM update manually.",
    )


def _passed_extension_qa(kind: str, extension_id: str) -> dict[str, object]:
    qa: dict[str, object] = {
        "version": "appv24.extension_qa/v1",
        "kind": kind,
        "extension_id": extension_id,
        "source": f"mcp://{extension_id}@v1",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": f"pytest appV2.4/tests/test_{extension_id.replace('.', '_')}.py", "status": "passed"}],
            "side_effect_policy": "passed",
            "runtime_boundary": "passed",
            "adapter_contract": "passed",
        },
        "fingerprints": {
            "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
            "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
        },
    }
    if kind == "connector":
        contracts = {
            "crm.lookup_contact": {
                "required_scopes": ["crm:read"],
                "allowed_outputs": ["contact", "artifact_path"],
            },
            "crm.send_customer_update": {
                "required_scopes": ["crm:send"],
                "allowed_outputs": ["message_id", "artifact_path"],
            },
        }
        qa["contract"] = contracts.get(extension_id, {"required_scopes": [], "allowed_outputs": []})
    return qa
