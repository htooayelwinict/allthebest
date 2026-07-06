"""Compact workflow output contract registry."""

from __future__ import annotations

BUILTIN_LLM_OUTPUT_CONTRACTS = frozenset(
    {
        "event_alerts_v1",
        "classification_v1",
        "items_v1",
        "summary_v1",
    }
)

LLM_OUTPUT_CONTRACT_FIELDS = {
    "event_alerts_v1": (
        "alerts",
        "confidence_score",
        "raw_response",
    ),
    "classification_v1": (
        "classification",
        "confidence_score",
        "raw_response",
    ),
    "items_v1": (
        "items",
        "count",
        "raw_response",
    ),
    "summary_v1": (
        "summary",
        "raw_response",
    ),
}

BUILTIN_ACTION_OUTPUT_FIELDS = {
    "gmail.create_draft": (
        "draft_id",
        "artifact_path",
    ),
    "http.fetch": (
        "status_code",
        "url",
        "text",
        "json",
        "artifact_path",
    ),
    "llm.classify": (
        "classification",
        "confidence_score",
        "raw_response",
        "confidence",
        "artifact_path",
    ),
    "llm.extract_structured": (
        "alerts",
        "items",
        "confidence_score",
        "raw_response",
        "confidence",
        "artifact_path",
    ),
    "llm.summarize": (
        "summary",
        "raw_response",
        "artifact_path",
    ),
    "notification.create_draft": (
        "draft_id",
        "artifact_path",
    ),
    "policy.check": (
        "approved",
        "reason",
        "artifact_path",
    ),
    "source.fetch_document": (
        "document",
        "artifact_path",
    ),
    "source.fetch_items": (
        "items",
        "source_count",
        "artifact_path",
    ),
    "telegram.send_message": (
        "message_id",
        "artifact_path",
    ),
    "ticket.create_draft": (
        "ticket_id",
        "artifact_path",
    ),
}


def is_builtin_llm_output_contract(contract_name: str) -> bool:
    return contract_name in BUILTIN_LLM_OUTPUT_CONTRACTS


def llm_output_contract_fields(contract_name: str) -> tuple[str, ...]:
    return LLM_OUTPUT_CONTRACT_FIELDS.get(contract_name, ())


def builtin_action_output_fields(action_ref: str) -> tuple[str, ...]:
    return BUILTIN_ACTION_OUTPUT_FIELDS.get(action_ref, ())
