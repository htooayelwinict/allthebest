"""Local no-network artifact providers for workflow broker dry-runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.broker import BrokerRequest, BrokerResult, filter_broker_outputs
from appv24.workflows.contracts import llm_output_contract_fields


_POLICY_ALLOWED_DECISIONS = frozenset(
    ("accept", "accepted", "allow", "allowed", "approve", "approved", "authorized", "pass", "passed", "true")
)
_POLICY_REJECTED_DECISIONS = frozenset(
    ("block", "blocked", "deny", "denied", "disallow", "disallowed", "fail", "failed", "false", "reject", "rejected", "unauthorized")
)


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

    def telegram_send_message_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-telegram-send-draft-{request.step_id}"
        path = self._write_artifact(request, "local-telegram-send-draft")
        return _filtered(
            request,
            {
                "draft_id": draft_id,
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

    def github_list_dependabot_alerts(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-github-dependabot-alerts")
        alert = {
            "alert_id": f"local-alert-{request.step_id}",
            "repository": "owner/project",
            "severity": "high",
            "dependency": "example-package",
            "summary": "Fixture-backed Dependabot alert for dry-run simulation.",
        }
        return _filtered(
            request,
            {
                "alerts": [alert],
                "count": 1,
                "artifact_path": str(path),
            },
        )

    def github_create_issue(self, request: BrokerRequest) -> BrokerResult:
        issue_number = 1001
        path = self._write_artifact(request, "local-github-issue")
        return _filtered(
            request,
            {
                "issue_number": issue_number,
                "issue_url": f"https://github.local/dry-run/issues/{issue_number}",
                "artifact_path": str(path),
            },
        )

    def github_create_issue_draft(self, request: BrokerRequest) -> BrokerResult:
        draft_id = f"local-github-issue-draft-{request.step_id}"
        path = self._write_artifact(request, "local-github-issue-draft")
        return _filtered(
            request,
            {
                "draft_id": draft_id,
                "artifact_path": str(path),
            },
        )

    def http_fetch(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-http-fetch")
        return _filtered(
            request,
            {
                "status_code": 200,
                "url": "https://example.test/dry-run",
                "text": "Dry-run HTTP response body.",
                "json": {"ok": True, "source": "local-http-fetch"},
                "artifact_path": str(path),
            },
        )

    def brave_search(self, request: BrokerRequest) -> BrokerResult:
        path = self._write_artifact(request, "local-brave-search")
        query = _string_from_with_or_fixture(request, "query") or "financial catalyst"
        return _filtered(
            request,
            {
                "items": [
                    {
                        "title": "Public market catalyst",
                        "url": "https://example.test/news/public-market-catalyst",
                        "description": f"Fixture-backed Brave result for {query}.",
                        "age": "1 day ago",
                    }
                ],
                "source_count": 1,
                "query": query,
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
            "summary": _summary_from_fixture_items(request) or "Dry-run summary generated from fixture-backed inputs.",
            "artifact_path": str(path),
        }
        if _output_contract(request) == "summary_v1":
            outputs.update({"raw_response": _raw_response(request, path)})
        return _filtered(request, outputs)

    def policy_check(self, request: BrokerRequest) -> BrokerResult:
        policy = request.fixture.get("policy") if isinstance(request.fixture, dict) else None
        policy_map = policy if isinstance(policy, dict) else {}
        path = self._write_artifact(request, "local-policy")
        decision = policy_map.get(request.step_id)
        if decision == "rejected":
            return _filtered(
                request,
                {
                    "approved": False,
                    "allowed": False,
                    "reason": "policy rejected",
                    "matched_rule": f"policy.{request.step_id}.rejected",
                    "artifact_path": str(path),
                },
                status="blocked",
                issues=(f"policy rejected for {request.step_id}",),
            )
        if decision == "approved":
            return _filtered(request, _policy_outputs(True, "policy passed", f"policy.{request.step_id}.approved", path))

        decision_hint = _downstream_decision_policy_hint(request)
        if decision_hint is not None:
            allowed, decision_step, selected = decision_hint
            state = "allowed" if allowed else "rejected"
            return _filtered(
                request,
                _policy_outputs(
                    allowed,
                    f"policy {state} by fixture decision {decision_step}={selected}",
                    f"decision.{decision_step}.{selected}",
                    path,
                ),
            )
        return _filtered(
            request,
            _policy_outputs(True, "policy passed", f"policy.{request.step_id}.approved", path),
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
            "brave.search": self.brave_search,
            "github.create_issue": self.github_create_issue,
            "github.create_issue_draft": self.github_create_issue_draft,
            "github.list_dependabot_alerts": self.github_list_dependabot_alerts,
            "ticket.create_draft": self.ticket_create_draft,
            "gmail.create_draft": self.gmail_create_draft,
            "http.fetch": self.http_fetch,
            "llm.classify": self.llm_classify,
            "llm.extract_structured": self.llm_extract_structured,
            "llm.summarize": self.llm_summarize,
            "notification.create_draft": self.notification_create_draft,
            "policy.check": self.policy_check,
            "source.fetch_document": self.source_fetch_document,
            "source.fetch_items": self.source_fetch_items,
            "telegram.create_message_draft": self.telegram_create_message_draft,
            "telegram.send_message": self.telegram_send_message,
            "telegram.send_message_draft": self.telegram_send_message_draft,
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


def _summary_from_fixture_items(request: BrokerRequest) -> str | None:
    items = list(_iter_fixture_items(request.fixture))
    if not items:
        return None
    lines = [f"Summary from {len(items)} item{'s' if len(items) != 1 else ''}:"]
    for index, item in enumerate(items[:5], start=1):
        title = _first_text(item, ("title", "name", "headline", "summary", "text"))
        description = _first_text(item, ("description", "snippet", "content", "body"))
        url = _first_text(item, ("url", "source_url", "link"))
        parts = [title or f"Item {index}"]
        if description and description != title:
            parts.append(description)
        if url:
            parts.append(url)
        lines.append(f"- {' - '.join(parts)}")
    return "\n".join(lines)


def _iter_fixture_items(fixture: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    found: list[Mapping[str, Any]] = []
    _collect_output_items(_mapping(fixture.get("handoff")).get("outputs"), found)
    _collect_step_output_items(fixture.get("steps"), found)
    return tuple(found)


def _collect_step_output_items(value: object, found: list[Mapping[str, Any]]) -> None:
    if not isinstance(value, Mapping):
        return
    for step_value in value.values():
        outputs = _mapping(step_value).get("outputs")
        _collect_output_items(outputs, found)


def _collect_output_items(value: object, found: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        items = value.get("items") or value.get("alerts") or value.get("results")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, Mapping):
                    found.append(item)
        for nested in value.values():
            if isinstance(nested, Mapping):
                _collect_output_items(nested, found)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                found.append(item)


def _first_text(item: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_from_with_or_fixture(request: BrokerRequest, key: str) -> str | None:
    with_map = _mapping(request.input_ref.get("with"))
    value = with_map.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = request.fixture.get(key) if isinstance(request.fixture, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _policy_outputs(approved: bool, reason: str, matched_rule: str, path: Path) -> dict[str, object]:
    return {
        "approved": approved,
        "allowed": approved,
        "reason": reason,
        "matched_rule": matched_rule,
        "artifact_path": str(path),
    }


def _downstream_decision_policy_hint(request: BrokerRequest) -> tuple[bool, str, str] | None:
    next_step = request.input_ref.get("next")
    if not isinstance(next_step, str) or not next_step:
        return None
    decisions = request.fixture.get("decision") if isinstance(request.fixture, dict) else None
    if not isinstance(decisions, dict):
        return None
    selected = decisions.get(next_step)
    if not isinstance(selected, str) or not selected:
        return None
    normalized = selected.strip().lower().replace("-", "_").replace(" ", "_")
    tokens = tuple(token for token in normalized.split("_") if token)
    if normalized in _POLICY_REJECTED_DECISIONS or any(token in _POLICY_REJECTED_DECISIONS for token in tokens):
        return False, next_step, selected
    if normalized in _POLICY_ALLOWED_DECISIONS or any(token in _POLICY_ALLOWED_DECISIONS for token in tokens):
        return True, next_step, selected
    return None
