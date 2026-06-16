from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.base import SkillCard
from appv22.runtime.decisions import RuntimeDecision
from appv22.runtime.services import create_appv22_services
from appv22.tools.definitions import ToolDefinition

DEFAULT_REPO = ROOT / "live_appv22_provider_retry_recovery_repo"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-provider-retry-recovery-current.json"
SECRET = "tok_provider_retry_private_441"
FORBIDDEN_TERMS = ("tok_provider_retry_private_441", "PROVIDER_RETRY_SECRET", "TimeoutError", "upstream timeout")


class TransientProvider:
    provider_id = "transient-provider-retry"

    def __init__(self) -> None:
        self.calls = 0
        self.decisions = [
            RuntimeDecision(
                "tool_call",
                "publish after provider retry",
                {"tool_id": "retry_recovery.publish", "arguments": {"message": "RETRY-441 recovered after provider retry"}},
            ),
            RuntimeDecision("finalize", "done"),
        ]

    def decide(self, _prompt: dict):
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError(f"upstream timeout PROVIDER_RETRY_SECRET={SECRET}")
        return self.decisions.pop(0)

    def usage_snapshot(self) -> dict[str, Any]:
        return {"model_calls": self.calls, "total_tokens": 0, "cost": 0.0}


class RetryRecoveryExtension:
    extension_id = "retry_recovery"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="retry_recovery.publish",
                extension_id=self.extension_id,
                triggers=("retry", "provider", "RETRY-441"),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="Provider retry recovery stress action.",
                tool_ids=("retry_recovery.publish",),
                instructions=("Publish once provider retry recovers.",),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "retry_recovery.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                {
                    "type": "object",
                    "properties": {
                        "accepted": {"type": "boolean"},
                        "message": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["accepted", "message", "errors"],
                },
                "runtime_observed",
                "Publish retry recovery proof.",
            ),
            publish,
        )


def publish(args: dict, _context: dict) -> dict:
    message = str(args.get("message", ""))
    return {"status": "completed", "accepted": "RETRY-441" in message, "message": message, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    args = parser.parse_args()

    args.repo.mkdir(parents=True, exist_ok=True)
    provider = TransientProvider()
    services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[RetryRecoveryExtension()])
    result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=4, provider_retry_attempts=1).run(
        "provider retry recovery RETRY-441"
    )
    report = build_report(result=result, provider=provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "reason": report["reason"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "provider_retry_recovery": report["provider_retry_recovery"],
                "output_path": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["provider_retry_recovery"]["passed"] else 1


def build_report(*, result: dict[str, Any], provider: TransientProvider) -> dict[str, Any]:
    events = [event for event in result.get("events", []) if isinstance(event, dict)]
    event_order = [str(event.get("event_type", "")) for event in events]
    tool_matrix = _tool_matrix(events)
    provider_failures = [event for event in events if event.get("event_type") == "ProviderCallFailed"]
    completed_publish = [
        row
        for row in tool_matrix
        if row.get("tool_id") == "retry_recovery.publish"
        and row.get("status") == "completed"
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("accepted") is True
    ]
    serialized = json.dumps(result, sort_keys=True, default=str)
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not recover after provider retry")
    if len(provider_failures) != 1:
        violations.append("expected exactly one provider failure event")
    if not completed_publish:
        violations.append("accepted publish missing after provider retry")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"provider retry leaked forbidden term: {term}")
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "provider": provider.provider_id,
        "totals": {
            "events": len(events),
            "decisions": event_order.count("DecisionProposed"),
            "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}),
            "compactions": event_order.count("ContextSummaryUpdated"),
        },
        "costs": provider.usage_snapshot(),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "provider_retry_recovery": {
            "passed": not violations,
            "provider_failure_count": len(provider_failures),
            "completed_publish_count": len(completed_publish),
            "forbidden_hits": forbidden_hits,
            "violations": violations,
        },
    }


def _tool_matrix(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = event.get("event_type")
        if event_type not in {"ToolCallCompleted", "ToolCallDenied"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        rows.append(
            {
                "index": index,
                "event_type": event_type,
                "tool_id": payload.get("tool_id"),
                "status": payload.get("status"),
                "arguments": payload.get("arguments"),
                "payload": inner,
                "errors": inner.get("errors", []) if isinstance(inner, dict) else [],
            }
        )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
