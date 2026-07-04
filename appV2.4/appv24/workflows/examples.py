"""Golden workflow package examples for appv24 tests and docs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from appv24.workflows.broker import BrokerResult, LocalWorkflowBroker
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class GoldenWorkflowExample:
    name: str
    documents: PackageDocuments
    fixtures: Mapping[str, Mapping[str, object]]
    broker: LocalWorkflowBroker


def golden_workflow_examples() -> dict[str, GoldenWorkflowExample]:
    return {"telegram_support_ticket": _telegram_support_ticket()}


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
