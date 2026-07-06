"""Golden workflow package examples for appv24 tests and docs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from appv24.workflows.broker import BrokerResult, LocalWorkflowBroker
from appv24.workflows.connectors.draft_gmail import gmail_create_draft_provider
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class GoldenWorkflowExample:
    name: str
    documents: PackageDocuments
    fixtures: Mapping[str, Mapping[str, object]]
    broker: LocalWorkflowBroker


def golden_workflow_examples() -> dict[str, GoldenWorkflowExample]:
    return {
        "gmail_draft_reply": _gmail_draft_reply(),
        "telegram_support_ticket": _telegram_support_ticket(),
        "gmail_draft_triage": _gmail_draft_triage(),
    }


def _gmail_draft_reply() -> GoldenWorkflowExample:
    workflow = {
        "version": "appv24.workflow/v1",
        "name": "gmail_draft_reply",
        "description": "Create a human-approved Gmail draft reply for a customer escalation.",
        "design": {
            "intent": "Create a Gmail draft reply for a customer escalation without sending it.",
            "trigger": "Manual review of a customer escalation email.",
            "inputs": ["source_message_id", "recipient", "case_summary"],
            "outputs": ["draft_id"],
            "assumptions": ["Gmail credentials are resolved from secret references outside the package"],
            "side_effects": ["create Gmail draft"],
            "approval_gates": ["human_approval"],
            "failure_modes": ["message no longer exists", "draft creation unavailable", "human rejects draft"],
            "rollback": ["delete the local draft artifact in dry-run"],
        },
        "triggers": [{"id": "manual_review", "type": "manual"}],
        "entrypoint": "decide_reply_needed",
        "steps": [
            {
                "id": "decide_reply_needed",
                "type": "decision",
                "branches": {
                    "needs_reply": "create_gmail_draft",
                    "no_reply": "done",
                },
            },
            {
                "id": "create_gmail_draft",
                "type": "action",
                "uses": "gmail.create_draft",
                "side_effect_class": "create",
                "approval": "human_approval",
                "next": "done",
            },
            {"id": "done", "type": "terminal"},
        ],
    }
    connectors = {
        "version": "appv24.connectors/v1",
        "connectors": [
            {
                "id": "gmail",
                "adapter": "direct_api",
                "description": "Gmail API draft adapter.",
                "actions": [
                    {
                        "id": "create_draft",
                        "operationId": "gmail_create_draft",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "create",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["gmail.compose"],
                        "outputs": {"allowed": ["draft_id"]},
                        "idempotency": {"key_fields": ["source_message_id", "recipient"]},
                    }
                ],
            }
        ],
    }
    permissions = {
        "version": "appv24.permissions/v1",
        "permissions": [
            {"connector": "gmail", "scopes": ["gmail.compose"], "actions": ["create_draft"]},
        ],
        "approvals": [
            {
                "id": "human_approval",
                "mode": "explicit",
                "required_for": ["create_gmail_draft"],
            }
        ],
    }
    fixtures = {
        "happy_path.yaml": {
            "decision": {"decide_reply_needed": "needs_reply"},
        },
        "rejection_path.yaml": {
            "decision": {"decide_reply_needed": "no_reply"},
        },
        "missing_recipient.yaml": {
            "decision": {"decide_reply_needed": "needs_reply"},
            "connector_sandbox": {
                "missing_fields": {
                    "create_gmail_draft": ["recipient"],
                }
            },
            "expected": {
                "status": "blocked",
                "brokered_actions": ["create_gmail_draft"],
            },
        },
    }
    broker = LocalWorkflowBroker(
        {
            "gmail.create_draft": lambda request: BrokerResult(
                status="simulated",
                provider_id="golden_fixture",
                outputs={"draft_id": "draft-1"},
            ),
        }
    )
    documents = PackageDocuments(
        root=Path("golden/gmail_draft_reply"),
        workflow=workflow,
        connectors=connectors,
        permissions=permissions,
        dry_run_fixtures=fixtures,
        rollback_text="# Rollback\n\nDelete the local draft artifact in dry-run.\n",
    )
    return GoldenWorkflowExample(
        name="gmail_draft_reply",
        documents=documents,
        fixtures=fixtures,
        broker=broker,
    )


def _telegram_support_ticket() -> GoldenWorkflowExample:
    workflow = {
        "version": "appv24.workflow/v1",
        "name": "telegram_support_ticket",
        "description": "Create a support ticket draft from an authorized Telegram customer message and notify an admin.",
        "design": {
            "intent": "Create a support ticket draft from an authorized Telegram customer message and notify an admin.",
            "trigger": "Telegram inbound message webhook.",
            "inputs": ["telegram_user_id", "message_text"],
            "outputs": ["ticket_id", "admin_message_id"],
            "assumptions": ["authorized customer ids are configured outside the workflow package"],
            "side_effects": ["create ticket draft", "send admin notification"],
            "approval_gates": ["admin_approval"],
            "failure_modes": ["unauthorized sender", "ticket draft failure", "admin notification failure"],
            "rollback": ["delete local draft artifacts only"],
        },
        "triggers": [{"id": "telegram_message", "type": "webhook"}],
        "entrypoint": "check_authorized_customer",
        "steps": [
            {
                "id": "check_authorized_customer",
                "type": "decision",
                "branches": {
                    "authorized": "create_ticket_draft",
                    "rejected": "rejected",
                },
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
                "next": "done",
            },
            {"id": "rejected", "type": "terminal"},
            {"id": "done", "type": "terminal"},
        ],
    }
    connectors = {
        "version": "appv24.connectors/v1",
        "connectors": [
            {
                "id": "ticket",
                "adapter": "manual",
                "description": "Local ticket draft adapter.",
                "actions": [
                    {
                        "id": "create_draft",
                        "operationId": "ticket_create_draft",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "create",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["ticket:draft"],
                        "outputs": {"allowed": ["ticket_id"]},
                        "idempotency": {"key_fields": ["telegram_user_id", "message_text"]},
                    }
                ],
            },
            {
                "id": "telegram",
                "adapter": "direct_api",
                "description": "Telegram Bot API.",
                "actions": [
                    {
                        "id": "send_message",
                        "operationId": "telegram_send_message",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "send",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["telegram:send"],
                        "outputs": {"allowed": ["message_id"]},
                        "idempotency": {"key_fields": ["ticket_id", "admin_chat_id"]},
                    }
                ],
            },
        ],
    }
    permissions = {
        "version": "appv24.permissions/v1",
        "permissions": [
            {"connector": "ticket", "scopes": ["ticket:draft"], "actions": ["create_draft"]},
            {"connector": "telegram", "scopes": ["telegram:send"], "actions": ["send_message"]},
        ],
        "approvals": [
            {
                "id": "admin_approval",
                "mode": "explicit",
                "required_for": ["create_ticket_draft", "notify_admin"],
            }
        ],
    }
    fixtures = {
        "happy_path.yaml": {
            "decision": {"check_authorized_customer": "authorized"},
        },
        "rejection_path.yaml": {
            "decision": {"check_authorized_customer": "rejected"},
        },
        "unauthorized_user.yaml": {
            "input": {
                "telegram_user_id": "unauthorized-customer-404",
                "message_text": "Please create a ticket for me.",
            },
            "decision": {"check_authorized_customer": "rejected"},
            "expected": {
                "terminal_step": "rejected",
                "brokered_actions": [],
            },
        },
        "ticket_draft_timeout.yaml": {
            "decision": {"check_authorized_customer": "authorized"},
            "connector_sandbox": {
                "failures": {
                    "create_ticket_draft": {
                        "status": "timeout",
                        "message": "ticket draft connector timed out after 30s",
                    }
                }
            },
            "expected": {
                "status": "blocked",
                "blocked_step": "create_ticket_draft",
            },
        },
    }
    broker = LocalWorkflowBroker(
        {
            "ticket.create_draft": lambda request: BrokerResult(
                status="simulated",
                provider_id="golden_fixture",
                outputs={"ticket_id": "ticket-1"},
            ),
            "telegram.send_message": lambda request: BrokerResult(
                status="simulated",
                provider_id="golden_fixture",
                outputs={"message_id": "msg-1"},
            ),
        }
    )
    documents = PackageDocuments(
        root=Path("golden/telegram_support_ticket"),
        workflow=workflow,
        connectors=connectors,
        permissions=permissions,
        dry_run_fixtures=fixtures,
        rollback_text="# Rollback\n\nDelete local draft artifacts only.\n",
    )
    return GoldenWorkflowExample(
        name="telegram_support_ticket",
        documents=documents,
        fixtures=fixtures,
        broker=broker,
    )


def _gmail_draft_triage() -> GoldenWorkflowExample:
    workflow = {
        "version": "appv24.workflow/v1",
        "name": "gmail_draft_triage",
        "description": "Classify inbound email and create a human-approved Gmail draft reply.",
        "design": {
            "intent": "Classify inbound email and create a human-approved Gmail draft reply.",
            "trigger": "Inbound email event.",
            "inputs": ["from", "subject", "body"],
            "outputs": ["draft_id", "thread_id"],
            "assumptions": ["Gmail credentials are provided by secret reference at execution time"],
            "side_effects": ["create Gmail draft only"],
            "approval_gates": ["human_approval"],
            "failure_modes": ["email ignored", "draft creation failure", "human rejects draft"],
            "rollback": ["delete the draft manually if it was created"],
        },
        "triggers": [{"id": "incoming_email", "type": "event"}],
        "entrypoint": "classify_email",
        "steps": [
            {
                "id": "classify_email",
                "type": "decision",
                "branches": {"needs_reply": "draft_reply", "ignore": "ignored"},
            },
            {
                "id": "draft_reply",
                "type": "action",
                "uses": "gmail.create_draft",
                "side_effect_class": "create",
                "approval": "human_approval",
                "next": "done",
            },
            {"id": "ignored", "type": "terminal"},
            {"id": "done", "type": "terminal"},
        ],
    }
    connectors = {
        "version": "appv24.connectors/v1",
        "connectors": [
            {
                "id": "gmail",
                "adapter": "direct_api",
                "description": "Gmail draft API.",
                "actions": [
                    {
                        "id": "create_draft",
                        "operationId": "gmail_create_draft",
                        "execution_mode": "brokered",
                        "side_effect": True,
                        "side_effect_class": "create",
                        "approval_required": True,
                        "dry_run_supported": True,
                        "required_scopes": ["gmail:draft"],
                        "outputs": {"allowed": ["draft_id", "thread_id"]},
                        "idempotency": {"key_fields": ["to", "subject", "body_hash"]},
                    }
                ],
            }
        ],
    }
    permissions = {
        "version": "appv24.permissions/v1",
        "permissions": [
            {"connector": "gmail", "scopes": ["gmail:draft"], "actions": ["create_draft"]},
        ],
        "approvals": [
            {
                "id": "human_approval",
                "mode": "explicit",
                "required_for": ["draft_reply"],
            }
        ],
    }
    fixtures = {
        "happy_path.yaml": {
            "decision": {"classify_email": "needs_reply"},
        },
        "rejection_path.yaml": {
            "decision": {"classify_email": "ignore"},
        },
    }
    documents = PackageDocuments(
        root=Path("golden/gmail_draft_triage"),
        workflow=workflow,
        connectors=connectors,
        permissions=permissions,
        dry_run_fixtures=fixtures,
        rollback_text="# Rollback\n\nDelete the Gmail draft manually if it was created.\n",
    )
    return GoldenWorkflowExample(
        name="gmail_draft_triage",
        documents=documents,
        fixtures=fixtures,
        broker=LocalWorkflowBroker({"gmail.create_draft": gmail_create_draft_provider}),
    )
