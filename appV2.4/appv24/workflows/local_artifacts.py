"""Local no-network artifact providers for workflow broker dry-runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.broker import BrokerRequest, BrokerResult, filter_broker_outputs
from appv24.workflows.contracts import llm_output_contract_fields


class LocalArtifactProviders:
    def __init__(self, artifact_root: Path):
        self.artifact_root = artifact_root
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def ticket_create_draft(self, request: BrokerRequest) -> BrokerResult:
        ticket_id = f"local-ticket-{request.step_id}"
        path = self.artifact_root / f"{ticket_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "ticket_id": ticket_id,
                "artifact_path": str(path),
            },
        )

    def gmail_create_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-draft-{request.step_id}"
        path = self.artifact_root / f"{draft_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )

    def telegram_send_message(self, request: BrokerRequest) -> BrokerResult:
        message_id = f"local-message-{request.step_id}"
        path = self.artifact_root / f"local-telegram-{request.step_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "message_id": message_id,
                "artifact_path": str(path),
            },
        )

    def telegram_create_message_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-telegram-draft-{request.step_id}"
        path = self.artifact_root / f"local-telegram-draft-{request.step_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )

    def source_fetch_items(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-source-items")
        return _filtered(
            request,
            {
                "items": [
                    {
                        "id": f"source-item-{request.step_id}",
                        "title": "Public catalyst item",
                        "url": "https://example.test/news/public-catalyst",
                        "published_at": "2026-07-06T00:00:00Z",
                    }
                ],
                "source_count": 1,
                "artifact_path": str(path),
            },
        )

    def source_fetch_document(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-source-document")
        return _filtered(
            request,
            {
                "document": {
                    "id": f"source-document-{request.step_id}",
                    "url": "https://example.test/filing/public-document",
                    "text": "Public document content for dry-run simulation.",
                },
                "artifact_path": str(path),
            },
        )

    def llm_extract_structured(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-llm-extract")
        event = {
            "ticker": "NVDA",
            "event_type": "guidance",
            "title": "Public catalyst item",
            "summary": "Company guidance event extracted from public source.",
            "source_url": "https://example.test/news/public-catalyst",
            "source_urls": ["https://example.test/news/public-catalyst"],
            "confidence": "medium",
            "not_action_advice": True,
        }
        if _output_contract(request) == "event_alerts_v1":
            outputs = {
                "alerts": [event],
                "confidence_score": "medium",
                "raw_response": _raw_response(request, path),
            }
        else:
            outputs = {
                "items": [event],
                "confidence": "medium",
                "artifact_path": str(path),
            }
        return _filtered(
            request,
            outputs,
        )

    def llm_classify(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-llm-classify")
        if _output_contract(request) == "classification_v1":
            outputs = {
                "classification": "informational",
                "confidence_score": "medium",
                "raw_response": _raw_response(request, path),
            }
        else:
            outputs = {
                "classification": "informational",
                "confidence": "medium",
                "artifact_path": str(path),
            }
        return _filtered(request, outputs)

    def llm_summarize(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-llm-summary")
        outputs = {
            "summary": "Dry-run summary generated from fixture-backed inputs.",
            "artifact_path": str(path),
        }
        if _output_contract(request) == "summary_v1":
            outputs.update({"raw_response": _raw_response(request, path)})
        return _filtered(request, outputs)

    def policy_check(self, request: BrokerRequest) -> BrokerResult:
        policy = request.fixture.get("policy") if isinstance(request.fixture, dict) else None
        policy_map = policy if isinstance(policy, dict) else {}
        decision = policy_map.get(request.step_id, "approved")
        path = self._write_artifact(request, "local-policy")
        if decision == "rejected":
            return _filtered(
                request,
                {
                    "approved": False,
                    "reason": "policy rejected",
                    "artifact_path": str(path),
                },
                status="blocked",
                issues=(f"policy rejected for {request.step_id}",),
            )
        return _filtered(
            request,
            {
                "approved": True,
                "reason": "policy passed",
                "artifact_path": str(path),
            },
        )

    def notification_create_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-notification-draft-{request.step_id}"
        path = self._write_artifact(request, "local-notification-draft")
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )

    def provider_map(self) -> dict[str, object]:
        return {
            "ticket.create_draft": self.ticket_create_draft,
            "gmail.create_draft": self.gmail_create_draft,
            "llm.classify": self.llm_classify,
            "llm.extract_structured": self.llm_extract_structured,
            "llm.summarize": self.llm_summarize,
            "notification.create_draft": self.notification_create_draft,
            "policy.check": self.policy_check,
            "source.fetch_document": self.source_fetch_document,
            "source.fetch_items": self.source_fetch_items,
            "telegram.create_message_draft": self.telegram_create_message_draft,
            "telegram.send_message": self.telegram_send_message,
        }

    def _write_artifact(self, request: BrokerRequest, prefix: str) -> Path:
        path = self.artifact_root / f"{prefix}-{request.step_id}.json"
        path.write_text(
            json.dumps({"step_id": request.step_id, "input": request.input_ref}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path


def _filtered(
    request: BrokerRequest,
    outputs: dict[str, object],
    *,
    status: str = "simulated",
    issues: tuple[str, ...] = (),
) -> BrokerResult:
    return filter_broker_outputs(
        request,
        BrokerResult(status=status, provider_id="local_artifacts", outputs=outputs, issues=issues),
    )


def _output_contract(request: BrokerRequest) -> str | None:
    with_map = _mapping(request.input_ref.get("with"))
    contract = with_map.get("output_contract")
    if isinstance(contract, str) and llm_output_contract_fields(contract):
        return contract
    return None


def _raw_response(request: BrokerRequest, path: Path) -> dict[str, object]:
    return {
        "artifact_path": str(path),
        "contract": _output_contract(request),
        "provider": "local_artifacts",
    }


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}
