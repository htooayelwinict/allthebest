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
from appv22.runtime.services import create_appv22_services

DEFAULT_REPO = ROOT / "live_appv22_provider_failure_safety_repo"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-provider-failure-safety-current.json"
SECRET = "tok_provider_private_337"
FORBIDDEN_TERMS = ("tok_provider_private_337", "PROVIDER_SECRET", "RuntimeError", "backend transport")


class FailingProvider:
    provider_id = "failing-provider-redaction"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, _prompt: dict):
        self.calls += 1
        raise RuntimeError(f"backend transport failed with PROVIDER_SECRET={SECRET}")

    def usage_snapshot(self) -> dict[str, Any]:
        return {"model_calls": self.calls, "total_tokens": 0, "cost": 0.0}


class ProviderFailureExtension:
    extension_id = "provider_failure"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="provider_failure.safety",
                extension_id=self.extension_id,
                triggers=("provider", "failure", "redaction"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Provider failure safety stress extension.",
                tool_ids=(),
                instructions=("Provider failure should be converted to sanitized runtime failure.",),
            )
        ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    args = parser.parse_args()

    args.repo.mkdir(parents=True, exist_ok=True)
    provider = FailingProvider()
    services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[ProviderFailureExtension()])
    result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=3).run(
        "provider failure redaction stress"
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
                "provider_failure_safety": report["provider_failure_safety"],
                "output_path": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["provider_failure_safety"]["passed"] else 1


def build_report(*, result: dict[str, Any], provider: FailingProvider) -> dict[str, Any]:
    events = [event for event in result.get("events", []) if isinstance(event, dict)]
    event_order = [str(event.get("event_type", "")) for event in events]
    serialized = json.dumps(result, sort_keys=True, default=str)
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    failures = [event for event in events if event.get("event_type") == "RunFailed"]
    provider_failures = [event for event in events if event.get("event_type") == "ProviderCallFailed"]
    violations: list[str] = []
    if result.get("status") != "failed":
        violations.append("runtime did not fail safely")
    if result.get("reason") != "runtime_loop_error":
        violations.append("unexpected failure reason")
    if not failures:
        violations.append("RunFailed event missing")
    if len(provider_failures) != 2:
        violations.append("expected two sanitized provider failure events after retry exhaustion")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"provider failure leaked forbidden term: {term}")
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
        "provider_failure_safety": {
            "passed": not violations,
            "run_failed_count": len(failures),
            "provider_failure_event_count": len(provider_failures),
            "model_calls": provider.calls,
            "forbidden_hits": forbidden_hits,
            "violations": violations,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
