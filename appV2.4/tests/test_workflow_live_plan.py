from __future__ import annotations

from pathlib import Path
import shutil

import yaml

from appv24.workflows.live_plan import build_workflow_live_plan_from_path, format_workflow_live_plan


def test_live_plan_for_mixed_financial_workflow_separates_live_and_local_phases(tmp_path: Path) -> None:
    package = _copy_example(tmp_path, "financial-catalyst-alerts")

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.workflow_name == "financial-catalyst-alerts"
    assert plan.fixture_name == "happy_path.yaml"
    assert plan.status == "partial-live"
    assert plan.live_read_actions == ("fetch_news: source.fetch_items",)
    assert plan.local_only_actions == (
        "extract_catalysts: llm.extract_structured",
        "check_policy: policy.check",
    )
    assert plan.live_draft_actions == ("create_alert_draft: notification.create_draft",)
    assert plan.live_production_actions == ()
    assert plan.transport_gaps == ("notification.create_draft: no built-in live-draft transport",)
    assert plan.approval_actions == ("create_alert_draft: notification.create_draft approval=alert-draft-review",)
    assert plan.phase_sequence == (
        "1 live-read: fetch_news: source.fetch_items",
        "2 local: extract_catalysts: llm.extract_structured, check_policy: policy.check",
        "3 live-draft: create_alert_draft: notification.create_draft (transport gap)",
    )
    assert plan.next_commands == (
        f"/workflow-live-ready live-read {package.resolve()} happy_path.yaml --allow source.fetch_items",
        f"/workflow-run-live-read-to-approval {package.resolve()} happy_path.yaml --allow source.fetch_items,llm.extract_structured",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-draft {package.resolve()} happy_path.yaml <approval-run-id> --allow notification.create_draft",
    )
    assert not (package / ".workflow-runs").exists()

    rendered = format_workflow_live_plan(plan)
    assert "Workflow live plan: partial-live" in rendered
    assert "- fetch_news: source.fetch_items" in rendered
    assert "- extract_catalysts: llm.extract_structured" in rendered
    assert "phase sequence:" in rendered
    assert "- 2 local: extract_catalysts: llm.extract_structured, check_policy: policy.check" in rendered
    assert "no built-in live-draft transport" in rendered


def test_live_plan_for_telegram_production_workflow_reports_production_path(tmp_path: Path) -> None:
    package = _copy_example(tmp_path, "telegram-production-alert")

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.status == "live-production"
    assert plan.live_read_actions == ()
    assert plan.live_draft_actions == ()
    assert plan.live_production_actions == ("notify_admin: telegram.send_message",)
    assert plan.local_only_actions == ()
    assert plan.transport_gaps == ()
    assert plan.approval_actions == ("notify_admin: telegram.send_message approval=operator-approval",)
    assert plan.phase_sequence == ("1 live-production: notify_admin: telegram.send_message",)
    assert plan.next_commands == (
        f"/workflow-run-approval {package.resolve()} happy_path.yaml",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
        f"/workflow-run-live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow telegram.send_message",
    )


def test_live_plan_uses_qa_passed_custom_connector_contracts(tmp_path: Path) -> None:
    package = _write_read_then_external_crm_send_package(tmp_path)

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.status == "partial-live"
    assert plan.live_read_actions == ("search_news: brave.search",)
    assert plan.live_production_actions == ("send_customer_update: crm.send_customer_update",)
    assert plan.local_only_actions == ()
    assert plan.transport_gaps == ()
    assert plan.approval_actions == ("send_customer_update: crm.send_customer_update approval=approve_crm_send",)
    assert plan.phase_sequence == (
        "1 live-read: search_news: brave.search",
        "2 live-production: send_customer_update: crm.send_customer_update",
    )
    assert plan.next_commands == (
        f"/workflow-live-ready live-read {package.resolve()} happy_path.yaml --allow brave.search",
        f"/workflow-run-live-read-to-approval {package.resolve()} happy_path.yaml --allow brave.search,crm.send_customer_update",
        f"/workflow-approvals {package.resolve()} <approval-run-id>",
        f"/workflow-live-ready live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow crm.send_customer_update",
        f"/workflow-run-live-production {package.resolve()} happy_path.yaml <approval-run-id> --allow crm.send_customer_update",
    )


def test_live_plan_includes_later_live_read_before_approval_after_local_step(tmp_path: Path) -> None:
    package = _write_ai_news_subscriber_broadcast_package(tmp_path)

    plan = build_workflow_live_plan_from_path(package, "happy_path.yaml")

    assert plan.status == "partial-live"
    assert plan.phase_sequence == (
        "1 live-read: search_ai_news: brave.search",
        "2 local: summarize_news: llm.summarize",
        "3 live-read: list_subscribers: telegram.list_subscribers",
        "4 live-production: send_digest: telegram.broadcast_message",
    )
    assert (
        f"/workflow-run-live-read-to-approval {package.resolve()} happy_path.yaml "
        "--allow brave.search,llm.summarize,telegram.list_subscribers"
    ) in plan.next_commands


def _copy_example(root: Path, name: str) -> Path:
    source = Path("examples/appv24/workflows") / name
    package = root / name
    shutil.copytree(source, package)
    return package


def _write_read_then_external_crm_send_package(root: Path) -> Path:
    package = root / "read-then-external-crm-send"
    package.mkdir(parents=True, exist_ok=True)
    (package / "dry_run_fixtures").mkdir()
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "read-then-external-crm-send",
                "description": "Read live context, then send one approved CRM customer update.",
                "design": {
                    "intent": "Read current context before an approved CRM customer send.",
                    "trigger": "Manual operator run.",
                    "inputs": ["customer_id", "text"],
                    "outputs": ["message_id"],
                    "assumptions": ["The CRM send extension has passed QA."],
                    "side_effects": ["send CRM customer update"],
                    "approval_gates": ["approve_crm_send"],
                    "failure_modes": ["CRM connector unavailable."],
                    "rollback": ["Send a corrective CRM update manually."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_news",
                "steps": [
                    {
                        "id": "search_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 3},
                        "next": "send_customer_update",
                    },
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
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query", "artifact_path"]},
                            }
                        ],
                    },
                    {
                        "id": "crm",
                        "adapter": "mcp",
                        "custom": True,
                        "source": "mcp://crm@v1",
                        "actions": [
                            {
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
                                "idempotency": {"key_fields": ["customer_id", "text"]},
                                "outputs": {"allowed": ["message_id", "artifact_path"]},
                                "workflow_hints": {
                                    "business_goal": "Send customer updates through CRM.",
                                    "catalog_group": "Draft or send",
                                    "catalog_label": "Send CRM customer update",
                                    "generated_by": "coding_profile",
                                    "input_fields": ["customer_id", "text"],
                                    "qa_required": True,
                                    "required_input_fields": ["customer_id", "text"],
                                    "required_target_fields": ["customer_id"],
                                },
                                "qa": {
                                    "version": "appv24.extension_qa/v1",
                                    "kind": "connector",
                                    "extension_id": "crm.send_customer_update",
                                    "source": "mcp://crm.send_customer_update@v1",
                                    "status": "passed",
                                    "qa": {
                                        "schema_validation": "passed",
                                        "tests": [
                                            {
                                                "command": "PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_crm_send_connector.py",
                                                "status": "passed",
                                            }
                                        ],
                                        "side_effect_policy": "passed",
                                        "runtime_boundary": "passed",
                                        "adapter_contract": "passed",
                                    },
                                    "contract": {
                                        "required_scopes": ["crm:send"],
                                        "allowed_outputs": ["message_id", "artifact_path"],
                                    },
                                    "fingerprints": {
                                        "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                                        "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                                        "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
                                        "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
                                    },
                                },
                            }
                        ],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "crm", "actions": ["send_customer_update"], "scopes": ["crm:send"]},
                ],
                "approvals": [{"id": "approve_crm_send", "mode": "explicit", "required_for": ["send_customer_update"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text(
        "input:\n  customer_id: customer-1\n  text: Customer update.\n",
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "rejection_path.yaml").write_text("expected:\n  status: rejected\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective CRM update manually.\n", encoding="utf-8")
    return package


def _write_ai_news_subscriber_broadcast_package(root: Path) -> Path:
    package = root / "ai-news-subscriber-broadcast"
    package.mkdir(parents=True, exist_ok=True)
    (package / "dry_run_fixtures").mkdir()
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "ai-news-subscriber-broadcast",
                "description": "Search AI news and broadcast a digest to registered Telegram subscribers.",
                "design": {
                    "intent": "Broadcast an AI news digest.",
                    "trigger": "Manual operator run.",
                    "inputs": [],
                    "outputs": ["sent_count", "message_ids", "chat_ids"],
                    "assumptions": ["Telegram subscribers are connector-owned."],
                    "side_effects": ["send Telegram messages"],
                    "approval_gates": ["approve_telegram_broadcast"],
                    "failure_modes": ["Telegram API unavailable."],
                    "rollback": ["Send a corrective Telegram message manually if required."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search_ai_news",
                "steps": [
                    {
                        "id": "search_ai_news",
                        "uses": "brave.search",
                        "with": {"query": "artificial intelligence news today", "count": 5},
                        "next": "summarize_news",
                    },
                    {
                        "id": "summarize_news",
                        "uses": "llm.summarize",
                        "with": {
                            "items": "{{ steps.search_ai_news.outputs.items }}",
                            "output_contract": "summary_v1",
                        },
                        "next": "list_subscribers",
                    },
                    {
                        "id": "list_subscribers",
                        "uses": "telegram.list_subscribers",
                        "with": {"audience": "registered_users"},
                        "next": "send_digest",
                    },
                    {
                        "id": "send_digest",
                        "uses": "telegram.broadcast_message",
                        "side_effect_class": "send",
                        "approval": "approve_telegram_broadcast",
                        "with": {
                            "recipients": "{{ steps.list_subscribers.outputs.recipients }}",
                            "audience_hash": "{{ steps.list_subscribers.outputs.audience_hash }}",
                            "text": "{{ steps.summarize_news.outputs.summary }}",
                        },
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "side_effect": False,
                                "required_scopes": ["brave:search"],
                                "outputs": {"allowed": ["items", "source_count", "query", "artifact_path"]},
                            }
                        ],
                    },
                    {
                        "id": "llm",
                        "adapter": "llm",
                        "actions": [
                            {
                                "id": "summarize",
                                "side_effect": False,
                                "outputs": {
                                    "allowed": ["summary", "citations", "confidence", "raw_response", "artifact_path"]
                                },
                            }
                        ],
                    },
                    {
                        "id": "telegram",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "list_subscribers",
                                "side_effect": False,
                                "required_scopes": ["telegram:subscribers:read"],
                                "outputs": {
                                    "allowed": ["recipients", "recipient_count", "audience_hash", "artifact_path"]
                                },
                            },
                            {
                                "id": "broadcast_message",
                                "execution_mode": "brokered",
                                "side_effect": True,
                                "side_effect_class": "send",
                                "approval_required": True,
                                "dry_run_supported": True,
                                "required_scopes": ["telegram:send"],
                                "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids", "artifact_path"]},
                                "idempotency": {"key_fields": ["audience_hash", "text"]},
                            },
                        ],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [
                    {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
                    {"connector": "llm", "actions": ["summarize"], "scopes": []},
                    {
                        "connector": "telegram",
                        "actions": ["list_subscribers", "broadcast_message"],
                        "scopes": ["telegram:subscribers:read", "telegram:send"],
                    },
                ],
                "approvals": [
                    {
                        "id": "approve_telegram_broadcast",
                        "mode": "explicit",
                        "required_for": ["send_digest"],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text("input: {}\nsteps: {}\n", encoding="utf-8")
    (package / "dry_run_fixtures" / "rejection_path.yaml").write_text(
        "expected:\n  status: rejected\n",
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nSend a corrective Telegram message if required.\n", encoding="utf-8")
    return package
