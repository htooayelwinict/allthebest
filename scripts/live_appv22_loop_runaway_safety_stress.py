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

DEFAULT_REPO = ROOT / "live_appv22_loop_runaway_safety_repo"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-loop-runaway-safety-current.json"
SECRET = "tok_loop_private_552"
FORBIDDEN_TERMS = ("tok_loop_private_552", "LOOP_SECRET", "backend trace")


class MalformedLoopProvider:
    provider_id = "malformed-loop-provider"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, _prompt: dict):
        self.calls += 1
        return RuntimeDecision(
            "tool_call",
            f"backend trace LOOP_SECRET={SECRET}; malformed retry {self.calls}",
            {"next_step": "call the selected tool but omit tool_id again"},
        )

    def usage_snapshot(self) -> dict[str, Any]:
        return {"model_calls": self.calls, "total_tokens": 0, "cost": 0.0}


class LoopSafetyExtension:
    extension_id = "loop_safety"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="loop_safety.action",
                extension_id=self.extension_id,
                triggers=("loop", "runaway", "safety"),
                modes=("START", "THINK", "ACT", "VERIFY"),
                summary="Loop runaway safety stress.",
                tool_ids=("loop_safety.publish",),
                instructions=("Use loop_safety.publish if the provider emits a valid tool call.",),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "loop_safety.publish",
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
                "Publish loop safety proof.",
            ),
            lambda args, _context: {"status": "completed", "accepted": True, "message": args.get("message", ""), "errors": []},
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--max-turns", type=int, default=4)
    args = parser.parse_args()

    args.repo.mkdir(parents=True, exist_ok=True)
    provider = MalformedLoopProvider()
    services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[LoopSafetyExtension()])
    result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(
        "loop runaway safety"
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
                "loop_runaway_safety": report["loop_runaway_safety"],
                "output_path": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["loop_runaway_safety"]["passed"] else 1


def build_report(*, result: dict[str, Any], provider: MalformedLoopProvider) -> dict[str, Any]:
    events = [event for event in result.get("events", []) if isinstance(event, dict)]
    event_order = [str(event.get("event_type", "")) for event in events]
    serialized = json.dumps(result, sort_keys=True, default=str)
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    malformed_guidance_count = serialized.count("Malformed tool_call decision was missing payload.tool_id")
    decisions = [event for event in events if event.get("event_type") == "DecisionProposed"]
    violations: list[str] = []
    if result.get("status") != "failed":
        violations.append("runtime did not fail safely")
    if result.get("reason") != "max_turns_exceeded":
        violations.append("expected max_turns_exceeded")
    if len(decisions) != provider.calls:
        violations.append("decision count did not match provider calls")
    if malformed_guidance_count < 1:
        violations.append("malformed tool-call guidance missing")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"loop runaway leaked forbidden term: {term}")
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
        "loop_runaway_safety": {
            "passed": not violations,
            "provider_calls": provider.calls,
            "decision_count": len(decisions),
            "malformed_guidance_count": malformed_guidance_count,
            "forbidden_hits": forbidden_hits,
            "violations": violations,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
