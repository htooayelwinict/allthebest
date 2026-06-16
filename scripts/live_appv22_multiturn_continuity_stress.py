from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.base import SkillCard
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from appv22.tools.definitions import ToolDefinition

DEFAULT_REPO = ROOT / "live_appv22_multiturn_continuity_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-multiturn-continuity-current.json"
REQUIRED_TERMS = ("THREAD-73", "Juniper onboarding", "Lina Vale", "2027-01-09", "send welcome packet")


class ProbeTimeoutError(TimeoutError):
    pass


@contextmanager
def bounded_probe_run(timeout_seconds: int):
    if timeout_seconds <= 0:
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise ProbeTimeoutError(f"probe exceeded {timeout_seconds}s timeout")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(timeout_seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


class ThreadMemoryExtension:
    extension_id = "thread_memory"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="thread_memory.continuity",
                extension_id=self.extension_id,
                triggers=("thread", "continue", "onboarding", "Juniper"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Generic multi-turn continuity stress tools.",
                tool_ids=("thread_memory.lookup", "thread_memory.publish_followup"),
                instructions=(
                    "Use thread_memory.lookup only when durable world_refs do not already contain THREAD-73 facts.",
                    "On a continuation request, use existing world_refs/context_summary before re-observing.",
                    "Use thread_memory.publish_followup to publish the follow-up after facts are available.",
                    "Finalize only after thread_memory.publish_followup returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "thread_memory.lookup",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"thread_id": {"type": "string"}},
                    "required": ["thread_id"],
                },
                {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "program": {"type": "string"},
                        "owner": {"type": "string"},
                        "date": {"type": "string"},
                        "next_action": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["thread_id", "program", "owner", "date", "next_action", "errors"],
                },
                "runtime_observed",
                "Look up thread facts for continuity.",
            ),
            lookup_thread,
        )
        registry.register(
            ToolDefinition(
                "thread_memory.publish_followup",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["thread_id", "message"],
                },
                {
                    "type": "object",
                    "properties": {
                        "followup_id": {"type": "string"},
                        "thread_id": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "message": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["followup_id", "thread_id", "accepted", "message", "errors"],
                },
                "runtime_observed",
                "Publish follow-up from carried thread facts.",
            ),
            publish_followup,
        )

    def finalize_guidance(self, state) -> str:
        has_followup = any(
            isinstance(result, dict)
            and result.get("tool_id") == "thread_memory.publish_followup"
            and result.get("status") == "completed"
            and isinstance(result.get("payload"), dict)
            and result["payload"].get("accepted") is True
            for result in state.tool_results.values()
        )
        if has_followup:
            return ""
        goal = state.request.user_goal.lower()
        if "follow" in goal or "continue" in goal:
            return (
                "Continuation request needs an accepted follow-up; use carried THREAD-73 evidence from world_refs/context_summary "
                "and call thread_memory.publish_followup without re-observing if facts are already available."
            )
        return ""


def lookup_thread(args: dict, _context: dict) -> dict:
    if str(args.get("thread_id", "")) != "THREAD-73":
        return {
            "status": "failed",
            "thread_id": str(args.get("thread_id", "")),
            "program": "",
            "owner": "",
            "date": "",
            "next_action": "",
            "errors": ["unknown_thread"],
        }
    return {
        "status": "completed",
        "thread_id": "THREAD-73",
        "program": "Juniper onboarding",
        "owner": "Lina Vale",
        "date": "2027-01-09",
        "next_action": "send welcome packet",
        "errors": [],
    }


def publish_followup(args: dict, _context: dict) -> dict:
    thread_id = str(args.get("thread_id", ""))
    message = str(args.get("message", ""))
    errors: list[str] = []
    if thread_id != "THREAD-73":
        errors.append(f"unknown_thread:{thread_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in message.lower()]
    if missing:
        errors.append("message_missing_terms:" + ",".join(missing))
    if errors:
        return {
            "status": "denied",
            "followup_id": "",
            "thread_id": thread_id,
            "accepted": False,
            "message": message,
            "errors": errors,
        }
    return {
        "status": "completed",
        "followup_id": "followup_THREAD-73",
        "thread_id": "THREAD-73",
        "accepted": True,
        "message": message,
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=227)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[ThreadMemoryExtension()])
            runtime = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=8)
            first = runtime.run("Thread continuity setup: observe THREAD-73 facts, pause/finalize after evidence is available. Do not publish yet.")
            second = runtime.continue_run(
                first,
                "Continue the same thread and publish the follow-up using the carried facts. Do not re-observe if THREAD-73 evidence is already available.",
            )
    except ProbeTimeoutError as exc:
        first = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}
        second = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(first=first, second=second, provider=provider)
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
                "multiturn_continuity": report["multiturn_continuity"],
                "output_path": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["multiturn_continuity"]["passed"] else 1


def configure_llm_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = str(args.temperature)
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def build_report(*, first: dict[str, Any], second: dict[str, Any], provider: Any) -> dict[str, Any]:
    first_events = [event for event in first.get("events", []) if isinstance(event, dict)]
    second_events = [event for event in second.get("events", []) if isinstance(event, dict)]
    first_tools = _tool_matrix(first_events)
    second_tools = _tool_matrix(second_events)
    first_lookup = [row for row in first_tools if row.get("tool_id") == "thread_memory.lookup"]
    second_lookup = [row for row in second_tools if row.get("tool_id") == "thread_memory.lookup"]
    followups = [
        row
        for row in second_tools
        if row.get("tool_id") == "thread_memory.publish_followup"
        and row.get("status") == "completed"
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("accepted") is True
    ]
    followup_text = "\n".join(json.dumps(row.get("payload", {}), sort_keys=True) for row in followups)
    required_hits = {term: term.lower() in followup_text.lower() for term in REQUIRED_TERMS}
    carried_world_refs = isinstance(second.get("world_refs"), dict) and any(
        isinstance(ref, dict) and ref.get("kind") == "thread_memory.lookup"
        for ref in second["world_refs"].values()
    )
    same_session = first.get("session_id") and first.get("session_id") == second.get("session_id")
    violations: list[str] = []
    if first.get("status") != "completed":
        violations.append("first turn did not complete")
    if second.get("status") != "completed":
        violations.append("second turn did not complete")
    if not first_lookup:
        violations.append("first turn lookup missing")
    if second_lookup:
        violations.append("second turn re-observed instead of using carried evidence")
    if not followups:
        violations.append("second turn accepted follow-up missing")
    if not same_session:
        violations.append("session id was not preserved")
    if not carried_world_refs:
        violations.append("carried world_refs missing")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"follow-up missing required term: {term}")
    all_events = [*first_events, *second_events]
    event_order = [str(event.get("event_type", "")) for event in all_events]
    tool_matrix = [*first_tools, *second_tools]
    return {
        "status": second.get("status"),
        "reason": second.get("reason"),
        "provider": getattr(provider, "provider_id", None) if provider is not None else None,
        "totals": {
            "events": len(all_events),
            "decisions": event_order.count("DecisionProposed"),
            "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}),
            "compactions": event_order.count("ContextSummaryUpdated"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "multiturn_continuity": {
            "passed": not violations,
            "same_session": bool(same_session),
            "first_lookup_count": len(first_lookup),
            "second_lookup_count": len(second_lookup),
            "completed_followup_count": len(followups),
            "carried_world_refs": carried_world_refs,
            "required_hits": required_hits,
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


def _costs(provider: Any) -> dict[str, Any]:
    for source, candidate in (
        ("provider.usage_snapshot", provider),
        ("client.usage_snapshot", getattr(provider, "client", None)),
        ("delegate.usage_snapshot", getattr(provider, "delegate", None)),
        ("delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None)),
    ):
        usage_snapshot = getattr(candidate, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot()
            if isinstance(snapshot, dict):
                return {
                    "available": True,
                    "source": source,
                    "model_calls": snapshot.get("model_calls"),
                    "total_tokens": snapshot.get("total_tokens"),
                    "cost": snapshot.get("cost"),
                }
    return {"available": False, "source": None, "model_calls": None, "total_tokens": None, "cost": None}


if __name__ == "__main__":
    raise SystemExit(main())
