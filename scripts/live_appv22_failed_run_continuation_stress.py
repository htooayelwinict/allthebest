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
sys.path.insert(0, str(ROOT / "scripts"))

from appv22 import AppV22AgentRuntime
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from live_appv22_multiturn_continuity_stress import ThreadMemoryExtension, REQUIRED_TERMS

DEFAULT_REPO = ROOT / "live_appv22_failed_run_continuation_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-failed-run-continuation-current.json"
FIRST_PROMPT = "Thread continuity setup: observe THREAD-73 facts only. Do not publish yet."
SECOND_PROMPT = "Continue after the prior bounded run and publish the THREAD-73 follow-up using carried evidence. Do not re-observe if evidence is already available."


class FailedRunThreadMemoryExtension(ThreadMemoryExtension):
    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") == "thread_memory.publish_followup" and result.get("status") == "denied":
            return (
                "thread_memory.publish_followup was denied; retry with thread_id 'THREAD-73' and exact message: "
                "'THREAD-73 Juniper onboarding: Lina Vale will send welcome packet on 2027-01-09.'"
            )
        return ""


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--run-timeout-seconds", type=int, default=260)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=731)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[FailedRunThreadMemoryExtension()])
            runtime = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=1)
            first = runtime.run(FIRST_PROMPT)
            second_runtime = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=5)
            second = second_runtime.continue_run(first, SECOND_PROMPT)
    except ProbeTimeoutError as exc:
        first = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}
        second = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(first=first, second=second, provider=provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "failed_run_continuation": report["failed_run_continuation"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["failed_run_continuation"]["passed"] else 1


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
    matrix = _continuation_matrix(first, second, first_tools, second_tools)
    return {
        "status": "completed" if matrix["passed"] else "failed",
        "reason": "failed_run_continuation_validated" if matrix["passed"] else "failed_run_continuation_violations",
        "provider": _provider_id(provider),
        "first_status": first.get("status"),
        "first_reason": first.get("reason"),
        "second_status": second.get("status"),
        "second_reason": second.get("reason"),
        "totals": {"events": len(first_events) + len(second_events), "decisions": _count_events(first_events, "DecisionProposed") + _count_events(second_events, "DecisionProposed"), "tool_calls": len(first_tools) + len(second_tools), "compactions": _count_events(first_events, "ContextSummaryUpdated") + _count_events(second_events, "ContextSummaryUpdated")},
        "costs": _costs(provider),
        "first_tool_matrix": first_tools,
        "second_tool_matrix": second_tools,
        "failed_run_continuation": matrix,
    }


def _continuation_matrix(first: dict[str, Any], second: dict[str, Any], first_tools: list[dict[str, Any]], second_tools: list[dict[str, Any]]) -> dict[str, Any]:
    first_lookup = [row for row in first_tools if row.get("tool_id") == "thread_memory.lookup" and row.get("status") == "completed"]
    second_lookup = [row for row in second_tools if row.get("tool_id") == "thread_memory.lookup" and row.get("status") == "completed"]
    second_publish = [row for row in second_tools if row.get("tool_id") == "thread_memory.publish_followup" and row.get("status") == "completed"]
    same_session = bool(first.get("session_id")) and first.get("session_id") == second.get("session_id")
    failed_carried_world_refs = bool(first.get("world_refs"))
    failed_carried_summary = bool((first.get("context_summary") or {}).get("evidence_refs"))
    second_carried_world_refs = bool(second.get("world_refs"))
    message = "\n".join(str((row.get("payload") or {}).get("message", "")) for row in second_publish if isinstance(row.get("payload"), dict))
    term_hits = {term: term.lower() in message.lower() for term in REQUIRED_TERMS}
    violations: list[str] = []
    if first.get("status") != "failed" or first.get("reason") != "max_turns_exceeded":
        violations.append("first run did not fail by max_turns_exceeded")
    if not first_lookup:
        violations.append("first run did not complete lookup before failure")
    if not failed_carried_world_refs:
        violations.append("failed result did not carry world_refs")
    if not failed_carried_summary:
        violations.append("failed result did not carry context_summary evidence_refs")
    if second.get("status") != "completed":
        violations.append("second continuation did not complete")
    if not same_session:
        violations.append("session was not preserved")
    if not second_carried_world_refs:
        violations.append("second result did not carry world refs")
    if second_lookup:
        violations.append("second run re-observed despite carried failed-run evidence")
    if not second_publish:
        violations.append("second run did not publish follow-up")
    for term, ok in term_hits.items():
        if not ok:
            violations.append(f"follow-up missing term: {term}")
    return {"passed": not violations, "same_session": same_session, "failed_carried_world_refs": failed_carried_world_refs, "failed_carried_summary": failed_carried_summary, "first_lookup_count": len(first_lookup), "second_lookup_count": len(second_lookup), "second_publish_count": len(second_publish), "term_hits": term_hits, "followup_ids": [(row.get("payload") or {}).get("followup_id") for row in second_publish if isinstance(row.get("payload"), dict)], "violations": violations}


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


def _count_events(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


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
