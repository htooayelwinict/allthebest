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

DEFAULT_REPO = ROOT / "live_appv22_finalize_hook_exception_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-finalize-hook-exception-current.json"
DEFAULT_PROMPT = "Use closeout tools only. Publish CLOSE-61 and finalize."
REQUIRED_TERMS = ("CLOSE-61", "safe closeout", "Uma Patel", "2026-05-19", "handover rehearsal")
FORBIDDEN_TERMS = ("tok_finalize_hook_private_61", "backend trace", "finalize stack", "file_management.write_file")


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


class CloseoutExtension:
    extension_id = "closeout"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="closeout.finalize_hook_exception",
                extension_id=self.extension_id,
                triggers=("closeout", "CLOSE-", "finalize"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Generic finalize hook exception isolation stress skill.",
                tool_ids=("closeout.lookup", "closeout.publish"),
                instructions=(
                    "Use closeout.lookup before publishing.",
                    "Use closeout.publish with exact public facts.",
                    "Finalize after closeout.publish returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "closeout.lookup",
                "observe",
                "low",
                {"type": "object", "properties": {"closeout_id": {"type": "string"}}, "required": ["closeout_id"]},
                {
                    "type": "object",
                    "properties": {
                        "closeout_id": {"type": "string"},
                        "owner": {"type": "string"},
                        "date": {"type": "string"},
                        "activity": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["closeout_id", "owner", "date", "activity", "errors"],
                },
                "runtime_observed",
                "Look up closeout facts.",
            ),
            lookup_closeout,
        )
        registry.register(
            ToolDefinition(
                "closeout.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"closeout_id": {"type": "string"}, "message": {"type": "string"}},
                    "required": ["closeout_id", "message"],
                },
                {
                    "type": "object",
                    "properties": {
                        "publish_id": {"type": "string"},
                        "closeout_id": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "message": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["publish_id", "closeout_id", "accepted", "message", "errors"],
                },
                "runtime_observed",
                "Publish closeout message.",
            ),
            publish_closeout,
        )

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") == "closeout.publish" and result.get("status") == "denied":
            return "Use exact message: 'CLOSE-61 safe closeout: Uma Patel owns the 2026-05-19 handover rehearsal.'"
        return ""

    def finalize_guidance(self, _state) -> str:
        raise RuntimeError("tok_finalize_hook_private_61 backend trace finalize stack file_management.write_file")


def lookup_closeout(args: dict, _context: dict) -> dict:
    closeout_id = str(args.get("closeout_id", ""))
    if closeout_id != "CLOSE-61":
        return {"status": "failed", "closeout_id": closeout_id, "owner": "", "date": "", "activity": "", "errors": ["unknown_closeout"]}
    return {"status": "completed", "closeout_id": "CLOSE-61", "owner": "Uma Patel", "date": "2026-05-19", "activity": "handover rehearsal", "errors": []}


def publish_closeout(args: dict, _context: dict) -> dict:
    closeout_id = str(args.get("closeout_id", ""))
    message = str(args.get("message", ""))
    errors: list[str] = []
    if closeout_id != "CLOSE-61":
        errors.append(f"unknown_closeout:{closeout_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in message.lower()]
    if missing:
        errors.append("message_missing_terms:" + ",".join(missing))
    forbidden = [term for term in FORBIDDEN_TERMS if term.lower() in message.lower()]
    if forbidden:
        errors.append("forbidden_term:" + ",".join(forbidden))
    if errors:
        return {"status": "denied", "publish_id": "", "closeout_id": closeout_id, "accepted": False, "message": message, "errors": errors}
    return {"status": "completed", "publish_id": "publish_CLOSE-61", "closeout_id": closeout_id, "accepted": True, "message": message, "errors": []}


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
    parser.add_argument("--seed", type=int, default=61)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[CloseoutExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "finalize_hook_exception": report["finalize_hook_exception"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["finalize_hook_exception"]["passed"] else 1


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
    matrix = _finalize_matrix(tool_matrix, result)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {"events": len(events), "decisions": event_order.count("DecisionProposed"), "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}), "compactions": event_order.count("ContextSummaryUpdated")},
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "finalize_hook_exception": matrix,
    }


def _finalize_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    lookup_calls = [row for row in tool_matrix if row.get("tool_id") == "closeout.lookup" and row.get("status") == "completed"]
    publish_calls = [row for row in tool_matrix if row.get("tool_id") == "closeout.publish" and row.get("status") == "completed"]
    file_tool_calls = [row for row in tool_matrix if str(row.get("tool_id", "")).startswith("file_management.")]
    serialized = json.dumps(result, sort_keys=True, default=str)
    message = "\n".join(str((row.get("payload") or {}).get("message", "")) for row in publish_calls if isinstance(row.get("payload"), dict))
    required_hits = {term: term.lower() in message.lower() for term in REQUIRED_TERMS}
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not lookup_calls:
        violations.append("lookup was not completed")
    if not publish_calls:
        violations.append("publish was not completed")
    if file_tool_calls:
        violations.append("file tool was called")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"message missing term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"finalize hook exception leaked: {term}")
    return {"passed": not violations, "lookup_calls": len(lookup_calls), "publish_calls": len(publish_calls), "file_tool_calls": len(file_tool_calls), "required_hits": required_hits, "forbidden_hits": forbidden_hits, "publish_ids": [(row.get("payload") or {}).get("publish_id") for row in publish_calls if isinstance(row.get("payload"), dict)], "violations": violations}


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
