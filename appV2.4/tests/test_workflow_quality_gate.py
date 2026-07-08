from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.quality_gate import check_workflow_quality_documents


def _documents_with_workflow(workflow: dict, *, permissions: dict | None = None):
    example = golden_workflow_examples()["telegram_support_ticket"]
    return replace(
        example.documents,
        workflow=workflow,
        permissions=example.documents.permissions if permissions is None else permissions,
    )


def _golden_workflow_copy() -> dict:
    example = golden_workflow_examples()["telegram_support_ticket"]
    return deepcopy(example.documents.workflow)


def test_quality_gate_rejects_action_without_next() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = dict(example.documents.workflow)
    steps = [dict(step) for step in workflow["steps"]]
    steps[1].pop("next")
    workflow["steps"] = steps
    documents = replace(example.documents, workflow=workflow)

    result = check_workflow_quality_documents(documents, broker=example.broker)

    assert not result.ok
    missing_transition = [issue for issue in result.issues if issue.code == "missing_step_transition"]
    assert len(missing_transition) == 1
    assert missing_transition[0].path == ("steps", "create_ticket_draft", "next")


def test_quality_gate_rejects_decision_without_branches() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"][0].pop("branches")
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    assert "missing_decision_branches" in {issue.code for issue in result.issues}


def test_quality_gate_rejects_unreachable_non_terminal_step() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"].append(
        {
            "id": "orphan_review",
            "type": "action",
            "uses": "ticket.create_draft",
            "side_effect_class": "create",
            "approval": "admin_approval",
            "next": "done",
        }
    )
    example = golden_workflow_examples()["telegram_support_ticket"]
    permissions = deepcopy(example.documents.permissions)
    permissions["approvals"][0]["required_for"].append("orphan_review")
    documents = _documents_with_workflow(workflow, permissions=permissions)

    result = check_workflow_quality_documents(documents)

    assert "unreachable_step" in {issue.code for issue in result.issues}


def test_quality_gate_rejects_entrypoint_path_without_reachable_terminal() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"] = [
        {
            "id": "check_authorized_customer",
            "type": "decision",
            "branches": {"authorized": "create_ticket_draft"},
        },
        {
            "id": "create_ticket_draft",
            "type": "action",
            "uses": "ticket.create_draft",
            "side_effect_class": "create",
            "approval": "admin_approval",
            "next": "notify_admin",
        },
            {
                "id": "notify_admin",
                "type": "action",
                "uses": "telegram.send_message",
                "side_effect_class": "send",
                "approval": "admin_approval",
                "with": {"chat_id": {"inputRef": "admin_chat_id"}, "text": {"inputRef": "message_text"}},
                "next": "create_ticket_draft",
            },
        {"id": "done", "type": "terminal"},
    ]
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    codes = {issue.code for issue in result.issues}
    assert "terminal_unreachable" in codes
    assert "cycle_without_terminal_escape" in codes


def test_quality_gate_allows_cycle_with_terminal_escape() -> None:
    workflow = _golden_workflow_copy()
    workflow["steps"][1]["next"] = "review_loop"
    workflow["steps"].insert(
        2,
        {
            "id": "review_loop",
            "type": "decision",
            "branches": {
                "retry": "create_ticket_draft",
                "finish": "notify_admin",
            },
        },
    )
    documents = _documents_with_workflow(workflow)

    result = check_workflow_quality_documents(documents)

    assert "cycle_without_terminal_escape" not in {issue.code for issue in result.issues}


def test_quality_gate_passes_valid_golden_example_fixtures() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    result = check_workflow_quality_documents(example.documents, broker=example.broker)

    assert result.ok
    reports = {report.fixture_name: report for report in result.fixture_reports}
    assert set(reports) == set(example.fixtures)
    assert reports["happy_path.yaml"].terminal_step == "done"
    assert reports["rejection_path.yaml"].terminal_step == "rejected"


def test_quality_gate_reports_fixture_branch_mismatch() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    documents = replace(
        example.documents,
        dry_run_fixtures={
            "happy_path.yaml": {},
            "rejection_path.yaml": example.fixtures["rejection_path.yaml"],
        },
    )

    result = check_workflow_quality_documents(documents, broker=example.broker)

    assert not result.ok
    assert "missing_fixture_decision" in {issue.code for issue in result.issues}
    assert result.issues[0].file == "dry_run_fixtures/happy_path.yaml"


def test_quality_gate_reports_broker_blocked_fixture() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    result = check_workflow_quality_documents(example.documents, broker=LocalWorkflowBroker({}))

    assert not result.ok
    assert "fixture_broker_blocked" in {issue.code for issue in result.issues}


def test_quality_gate_rejects_removed_ticketing_create_ticket_action() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    documents = deepcopy(example.documents)
    workflow = deepcopy(documents.workflow)
    connectors = deepcopy(documents.connectors)
    permissions = deepcopy(documents.permissions)

    workflow["steps"][1]["uses"] = "ticketing.create_ticket"
    connectors["connectors"][0] = {
        "id": "ticketing",
        "adapter": "manual",
        "description": "Ticketing production mutation adapter.",
        "actions": [
            {
                "id": "create_ticket",
                "operationId": "ticketing_create_ticket",
                "execution_mode": "brokered",
                "side_effect": True,
                "side_effect_class": "create",
                "approval_required": True,
                "dry_run_supported": True,
                "required_scopes": ["ticket:draft"],
                "outputs": {"allowed": ["ticket_id"]},
                "idempotency": {"key_fields": ["customer_id", "summary"]},
            }
        ],
    }
    permissions["permissions"][0] = {
        "connector": "ticketing",
        "actions": ["create_ticket"],
        "scopes": ["ticket:draft"],
    }
    documents = replace(documents, workflow=workflow, connectors=connectors, permissions=permissions)

    result = check_workflow_quality_documents(documents)

    assert not result.ok
    codes = {issue.code for issue in result.issues}
    assert "unknown_catalog_action" in codes
    assert "unknown_connector_contract" in codes
    assert any("ticketing.create_ticket" in issue.message for issue in result.issues)


def test_quality_gate_rejects_registered_user_broadcast_draft_and_manual_detours() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = {
        "version": "appv24.workflow/v1",
        "name": "ai-news-telegram-broadcast",
        "description": "Daily AI news summary workflow for registered telegram users.",
        "design": {
            "intent": "Send an AI news digest to registered telegram users.",
            "trigger": "Daily schedule.",
            "inputs": ["AI news search query", "telegram subscriber audience"],
            "outputs": ["Telegram broadcast result"],
            "assumptions": ["Subscribers are provided by telegram.list_subscribers."],
            "side_effects": ["Send telegram broadcast after approval."],
            "approval_gates": [
                "approve_create_message_draft",
                "approve_summary_content",
                "approve_broadcast_message",
            ],
            "failure_modes": ["Search, subscriber listing, summarization, or broadcast fails."],
            "rollback": ["Review the telegram broadcast manually."],
        },
        "triggers": [{"id": "daily_schedule", "type": "schedule", "schedule": "0 9 * * *"}],
        "entrypoint": "fetch_ai_news",
        "steps": [
            {
                "id": "fetch_ai_news",
                "uses": "brave.search",
                "with": {"query": "artificial intelligence news today", "freshness": "day", "count": 10},
                "next": "summarize_news",
            },
            {
                "id": "summarize_news",
                "uses": "llm.summarize",
                "with": {"text": "{{ steps.fetch_ai_news.outputs.items }}", "output_contract": "summary_v1"},
                "next": "list_subscribers",
            },
            {
                "id": "list_subscribers",
                "uses": "telegram.list_subscribers",
                "with": {"audience": "registered_users", "limit": 1000},
                "next": "check_content_policy",
            },
            {
                "id": "check_content_policy",
                "uses": "policy.check",
                "with": {
                    "input_ref": "summarize_news.summary",
                    "checks": {"require_source_urls": True, "disallow_unverified_claims": True},
                },
                "side_effect_class": "read",
                "next": "create_message_draft",
            },
            {
                "id": "create_message_draft",
                "uses": "telegram.create_message_draft",
                "with": {
                    "chat_id": "{{ list_subscribers.recipients[0].chat_id }}",
                    "text": "{{ summarize_news.summary }}",
                },
                "side_effect_class": "create",
                "approval": "approve_create_message_draft",
                "next": "approve_summary",
            },
            {
                "id": "approve_summary",
                "type": "manual_approval",
                "approval": "approve_summary_content",
                "next": "broadcast_decision",
            },
            {
                "id": "broadcast_decision",
                "type": "decision",
                "branches": {"approved": "broadcast_message", "rejected": "done"},
            },
                {
                    "id": "broadcast_message",
                    "uses": "telegram.broadcast_message",
                    "with": {
                        "recipients": "{{ list_subscribers.recipients }}",
                        "audience_hash": "{{ list_subscribers.audience_hash }}",
                        "text": "{{ create_message_draft.text }}",
                    },
                    "side_effect_class": "send",
                "approval": "approve_broadcast_message",
                "next": "done",
            },
            {"id": "done", "type": "terminal"},
        ],
    }
    connectors = {
        "version": "appv24.connectors/v1",
        "connectors": [
            {
                "id": "brave",
                "adapter": "direct_api",
                "actions": [{"id": "search", "side_effect": False, "required_scopes": ["brave:search"]}],
            },
            {
                "id": "llm",
                "adapter": "llm",
                "actions": [
                    {"id": "summarize", "side_effect": False, "outputs": {"allowed": ["summary", "raw_response"]}}
                ],
            },
            {
                "id": "policy",
                "adapter": "manual",
                "actions": [
                    {
                        "id": "check",
                        "side_effect": False,
                        "side_effect_class": "read",
                        "execution_mode": "brokered",
                        "dry_run_supported": True,
                        "outputs": {"allowed": ["approved", "reason"]},
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
                    },
                    {
                        "id": "create_message_draft",
                        "side_effect": True,
                        "side_effect_class": "create",
                        "execution_mode": "brokered",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["telegram:draft"],
                        "idempotency": {"key_fields": ["chat_id", "message_hash"]},
                        "outputs": {"allowed": ["draft_id", "text"]},
                    },
                    {
                        "id": "broadcast_message",
                        "side_effect": True,
                        "side_effect_class": "send",
                        "execution_mode": "brokered",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["telegram:send"],
                        "idempotency": {"key_fields": ["audience_hash", "text"]},
                        "outputs": {"allowed": ["sent_count", "message_ids", "chat_ids"]},
                    },
                ],
            },
        ],
    }
    permissions = {
        "version": "appv24.permissions/v1",
        "permissions": [
            {"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]},
            {"connector": "llm", "actions": ["summarize"], "scopes": []},
            {"connector": "policy", "actions": ["check"], "scopes": []},
            {
                "connector": "telegram",
                "actions": ["list_subscribers", "create_message_draft", "broadcast_message"],
                "scopes": ["telegram:subscribers:read", "telegram:draft", "telegram:send"],
            },
        ],
        "approvals": [
            {"id": "approve_create_message_draft", "mode": "explicit", "required_for": ["create_message_draft"]},
            {"id": "approve_summary_content", "mode": "explicit", "required_for": ["approve_summary"]},
            {"id": "approve_broadcast_message", "mode": "explicit", "required_for": ["broadcast_message"]},
        ],
    }
    documents = replace(
        example.documents,
        workflow=workflow,
        connectors=connectors,
        permissions=permissions,
        dry_run_fixtures={
            "happy_path.yaml": {"decision": {"broadcast_decision": "approved"}},
            "rejection_path.yaml": {"decision": {"broadcast_decision": "rejected"}},
        },
    )

    result = check_workflow_quality_documents(documents)

    issues = {issue.path[-1]: issue for issue in result.issues if issue.code == "registered_user_broadcast_detour"}
    assert set(issues) == {
        "check_content_policy",
        "create_message_draft",
        "approve_summary",
        "broadcast_decision",
    }, result.issues
    assert all("telegram.broadcast_message" in issue.message for issue in issues.values())
