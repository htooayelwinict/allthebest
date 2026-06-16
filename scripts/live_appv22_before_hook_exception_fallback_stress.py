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

DEFAULT_REPO = ROOT / "live_appv22_before_hook_exception_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-before-hook-exception-fallback-current.json"
DEFAULT_PROMPT = (
    "Use router tools only. Publish ROUTE-18. If the primary route is blocked by runtime policy, "
    "recover with the fallback route and finalize."
)
REQUIRED_TERMS = ("ROUTE-18", "safe fallback", "Cass Lee", "2026-07-24", "routing rehearsal")
FORBIDDEN_TERMS = ("tok_before_hook_private_18", "backend trace", "policy stack", "file_management.write_file")


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


class RouterExtension:
    extension_id = "router"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="router.before_hook_fallback",
                extension_id=self.extension_id,
                triggers=("router", "route", "ROUTE-", "fallback"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Generic before-hook exception fallback stress skill.",
                tool_ids=("router.lookup", "router.publish_primary", "router.publish_fallback"),
                instructions=(
                    "Use router.lookup before publishing.",
                    "Try router.publish_primary when appropriate, but if it is blocked by runtime policy, do not retry it.",
                    "Use router.publish_fallback when primary route is blocked.",
                    "Finalize only after router.publish_fallback or router.publish_primary returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "router.lookup",
                "observe",
                "low",
                {"type": "object", "properties": {"route_id": {"type": "string"}}, "required": ["route_id"]},
                {
                    "type": "object",
                    "properties": {
                        "route_id": {"type": "string"},
                        "owner": {"type": "string"},
                        "date": {"type": "string"},
                        "activity": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["route_id", "owner", "date", "activity", "errors"],
                },
                "runtime_observed",
                "Look up route facts.",
            ),
            lookup_route,
        )
        for tool_id, guidance in (
            ("router.publish_primary", "Publish through the primary route."),
            ("router.publish_fallback", "Publish through the safe fallback route."),
        ):
            registry.register(
                ToolDefinition(
                    tool_id,
                    "act",
                    "medium",
                    {
                        "type": "object",
                        "properties": {"route_id": {"type": "string"}, "message": {"type": "string"}},
                        "required": ["route_id", "message"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "publish_id": {"type": "string"},
                            "route_id": {"type": "string"},
                            "accepted": {"type": "boolean"},
                            "message": {"type": "string"},
                            "errors": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["publish_id", "route_id", "accepted", "message", "errors"],
                    },
                    "runtime_observed",
                    guidance,
                ),
                publish_route,
            )

    def before_tool_call(self, _state, tool_id: str, _arguments: dict[str, Any]) -> dict[str, Any] | None:
        if tool_id == "router.publish_primary":
            raise RuntimeError("tok_before_hook_private_18 backend trace policy stack file_management.write_file")
        return None

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") == "router.publish_primary" and result.get("status") == "denied":
            return (
                "router.publish_primary is blocked by runtime policy; do not retry it. "
                "Use router.publish_fallback with route_id 'ROUTE-18' and exact message: "
                "'ROUTE-18 safe fallback: Cass Lee owns the 2026-07-24 routing rehearsal.'"
            )
        if result.get("tool_id") == "router.publish_fallback" and result.get("status") == "denied":
            return (
                "router.publish_fallback needs exact safe message: "
                "'ROUTE-18 safe fallback: Cass Lee owns the 2026-07-24 routing rehearsal.'"
            )
        return ""

    def finalize_guidance(self, state) -> str:
        for result in state.tool_results.values():
            if (
                isinstance(result, dict)
                and result.get("tool_id") in {"router.publish_primary", "router.publish_fallback"}
                and result.get("status") == "completed"
                and isinstance(result.get("payload"), dict)
                and result["payload"].get("accepted") is True
            ):
                return ""
        return (
            "No accepted route publish exists; if primary is blocked, call router.publish_fallback with route_id 'ROUTE-18' "
            "and exact message: 'ROUTE-18 safe fallback: Cass Lee owns the 2026-07-24 routing rehearsal.'"
        )


def lookup_route(args: dict, _context: dict) -> dict:
    route_id = str(args.get("route_id", ""))
    if route_id != "ROUTE-18":
        return {"status": "failed", "route_id": route_id, "owner": "", "date": "", "activity": "", "errors": ["unknown_route"]}
    return {"status": "completed", "route_id": "ROUTE-18", "owner": "Cass Lee", "date": "2026-07-24", "activity": "routing rehearsal", "errors": []}


def publish_route(args: dict, context: dict) -> dict:
    route_id = str(args.get("route_id", ""))
    message = str(args.get("message", ""))
    errors: list[str] = []
    if route_id != "ROUTE-18":
        errors.append(f"unknown_route:{route_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in message.lower()]
    if missing:
        errors.append("message_missing_terms:" + ",".join(missing))
    forbidden = [term for term in FORBIDDEN_TERMS if term.lower() in message.lower()]
    if forbidden:
        errors.append("forbidden_term:" + ",".join(forbidden))
    tool_id = str(context.get("request", {}).get("tool_id", "")) if isinstance(context.get("request"), dict) else ""
    # Broker request_context intentionally does not expose tool_id; infer from caller by accepted message only.
    if errors:
        return {"status": "denied", "publish_id": "", "route_id": route_id, "accepted": False, "message": message, "errors": errors}
    return {"status": "completed", "publish_id": "publish_ROUTE-18", "route_id": route_id, "accepted": True, "message": message, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=18)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[RouterExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "before_hook_exception_fallback": report["before_hook_exception_fallback"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["before_hook_exception_fallback"]["passed"] else 1


def configure_llm_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = str(args.temperature)
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def build_report(*, result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    events = [event for event in result.get("events", []) if isinstance(event, dict)]
    event_order = [str(event.get("event_type", "")) for event in events]
    tool_matrix = _tool_matrix(events)
    matrix = _fallback_matrix(tool_matrix, result)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {"events": len(events), "decisions": event_order.count("DecisionProposed"), "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}), "compactions": event_order.count("ContextSummaryUpdated")},
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "before_hook_exception_fallback": matrix,
    }


def _fallback_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    lookup_calls = [row for row in tool_matrix if row.get("tool_id") == "router.lookup" and row.get("status") == "completed"]
    blocked_primary = [row for row in tool_matrix if row.get("tool_id") == "router.publish_primary" and row.get("status") == "denied"]
    completed_primary = [row for row in tool_matrix if row.get("tool_id") == "router.publish_primary" and row.get("status") == "completed"]
    completed_fallback = [row for row in tool_matrix if row.get("tool_id") == "router.publish_fallback" and row.get("status") == "completed"]
    file_tool_calls = [row for row in tool_matrix if str(row.get("tool_id", "")).startswith("file_management.")]
    serialized = json.dumps(result, sort_keys=True, default=str)
    message = "\n".join(str((row.get("payload") or {}).get("message", "")) for row in completed_fallback if isinstance(row.get("payload"), dict))
    required_hits = {term: term.lower() in message.lower() for term in REQUIRED_TERMS}
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not lookup_calls:
        violations.append("lookup was not completed")
    if not blocked_primary:
        violations.append("primary publish was not blocked by before hook")
    if completed_primary:
        violations.append("blocked primary publish completed unexpectedly")
    if not completed_fallback:
        violations.append("fallback publish was not completed")
    if file_tool_calls:
        violations.append("file tool was called")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"fallback message missing term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"before-hook exception leaked forbidden term: {term}")
    return {"passed": not violations, "lookup_calls": len(lookup_calls), "blocked_primary_count": len(blocked_primary), "completed_primary_count": len(completed_primary), "completed_fallback_count": len(completed_fallback), "file_tool_calls": len(file_tool_calls), "required_hits": required_hits, "forbidden_hits": forbidden_hits, "publish_ids": [(row.get("payload") or {}).get("publish_id") for row in completed_fallback if isinstance(row.get("payload"), dict)], "violations": violations}


def _tool_matrix(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = event.get("event_type")
        if event_type not in {"ToolCallCompleted", "ToolCallDenied"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        rows.append({"index": index, "event_type": event_type, "tool_id": payload.get("tool_id"), "status": payload.get("status"), "arguments": payload.get("arguments"), "payload": inner, "errors": inner.get("errors", [])})
    return rows


def _costs(provider: Any) -> dict[str, Any]:
    for source, candidate in (("provider.usage_snapshot", provider), ("client.usage_snapshot", getattr(provider, "client", None)), ("delegate.usage_snapshot", getattr(provider, "delegate", None)), ("delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None))):
        usage_snapshot = getattr(candidate, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot()
            if isinstance(snapshot, dict):
                return {"available": True, "source": source, "model_calls": snapshot.get("model_calls"), "total_tokens": snapshot.get("total_tokens"), "cost": snapshot.get("cost")}
    return {"available": False, "source": None, "model_calls": None, "total_tokens": None, "cost": None}


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(getattr(provider, "provider_id", type(provider).__name__))


if __name__ == "__main__":
    raise SystemExit(main())
