from __future__ import annotations

from pathlib import Path

from appv24.workflows.catalog import built_in_capability_catalog, is_builtin_workflow_action
from appv24.workflows.connector_contracts import (
    ApprovalClass,
    SideEffectClass,
    WorkflowLiveMode,
    builtin_connector_contract_catalog,
)
from appv24.workflows.connector_packs import builtin_connector_catalog
from appv24.workflows.loader import PackageDocuments
from appv24.workflows.prompt_fragments import render_capability_catalog_fragment
from appv24.workflows.semantic import validate_semantics


def test_builtin_contract_catalog_is_default_deny() -> None:
    catalog = builtin_connector_contract_catalog()

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

    assert "http.fetch(input: url, query) -> status_code, url, text/json" in fragment
    assert "gmail.create_draft(input: to, subject, body) -> draft_id, thread_id" in fragment
    assert "ConnectorContract(" not in fragment
    assert "input_schema" not in fragment


def test_semantics_reject_contract_scope_mismatch() -> None:
    issues = validate_semantics(_http_fetch_documents(required_scopes=[], permission_scopes=[]))

    messages = "\n".join(issue.message for issue in issues)
    assert "http.fetch required_scopes must include http:read" in messages
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
