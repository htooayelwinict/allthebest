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
from appv22.context.compressor import AgentContextCompressor
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from live_appv22_non_file_ops_extension_stress import OpsExtension, REQUIRED_TERMS as OPS_REQUIRED_TERMS
from live_appv22_workflow_board_stress import WorkflowBoardExtension

DEFAULT_REPO = ROOT / "live_appv22_topic_switch_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-topic-switch-compaction-current.json"
FIRST_PROMPT = (
    "The rollout board is messy. Use the board tools only, make the Q4 launch card unblockable, "
    "assign the right owner and due date from board evidence, publish a concise status update, then finalize."
)
SECOND_PROMPT = (
    "New topic. Ignore any previous rollout-board work except as historical background. "
    "Use the ops extension tools to prepare an incident handoff receipt for incident INC-842, then finalize."
)
BOARD_TOOL_PREFIX = "workflow_board."
OPS_TOOL_PREFIX = "ops."


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
    parser.add_argument("--run-timeout-seconds", type=int, default=300)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=421)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(
                root_path=args.repo,
                provider=provider,
                extensions=[WorkflowBoardExtension(), OpsExtension()],
            )
            services.compressor = AgentContextCompressor(max_chars=10_000, threshold=0.55)
            runtime = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=10)
            first = runtime.run(FIRST_PROMPT)
            second = runtime.continue_run(first, SECOND_PROMPT)
    except ProbeTimeoutError as exc:
        first = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}
        second = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(first=first, second=second, provider=provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "topic_switch_compaction": report["topic_switch_compaction"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["topic_switch_compaction"]["passed"] else 1


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
    matrix = _topic_switch_matrix(first, second, first_tools, second_tools)
    return {
        "status": "completed" if matrix["passed"] else "failed",
        "reason": "topic_switch_validated" if matrix["passed"] else "topic_switch_violations",
        "provider": _provider_id(provider),
        "first_status": first.get("status"),
        "first_reason": first.get("reason"),
        "second_status": second.get("status"),
        "second_reason": second.get("reason"),
        "totals": {
            "events": len(first_events) + len(second_events),
            "decisions": _count_events(first_events, "DecisionProposed") + _count_events(second_events, "DecisionProposed"),
            "tool_calls": len(first_tools) + len(second_tools),
            "compactions": _count_events(first_events, "ContextSummaryUpdated") + _count_events(second_events, "ContextSummaryUpdated"),
        },
        "costs": _costs(provider),
        "first_tool_matrix": first_tools,
        "second_tool_matrix": second_tools,
        "topic_switch_compaction": matrix,
    }


def _topic_switch_matrix(first: dict[str, Any], second: dict[str, Any], first_tools: list[dict[str, Any]], second_tools: list[dict[str, Any]]) -> dict[str, Any]:
    first_board_publish = [row for row in first_tools if row.get("tool_id") == "workflow_board.publish_status" and row.get("status") == "completed"]
    second_ops_lookup = [row for row in second_tools if row.get("tool_id") == "ops.lookup_incident" and row.get("status") == "completed"]
    second_ops_receipt = [row for row in second_tools if row.get("tool_id") == "ops.create_handoff_receipt" and row.get("status") == "completed"]
    second_board_calls = [row for row in second_tools if str(row.get("tool_id", "")).startswith(BOARD_TOOL_PREFIX)]
    second_file_calls = [row for row in second_tools if str(row.get("tool_id", "")).startswith("file_management.")]
    receipt_summary = "\n".join(str((row.get("payload") or {}).get("summary", "")) for row in second_ops_receipt if isinstance(row.get("payload"), dict))
    ops_term_hits = {term: term.lower() in receipt_summary.lower() for term in OPS_REQUIRED_TERMS}
    stale_hits = {term: term.lower() in receipt_summary.lower() for term in ("BOARD-204", "Linh Tran", "consent banner", "launch checklist")}
    same_session = bool(first.get("session_id")) and first.get("session_id") == second.get("session_id")
    carried_world_refs = bool(first.get("world_refs")) and bool(second.get("world_refs"))
    violations: list[str] = []
    if first.get("status") != "completed":
        violations.append("first board task did not complete")
    if second.get("status") != "completed":
        violations.append("second ops task did not complete")
    if not same_session:
        violations.append("session was not preserved across continue_run")
    if not carried_world_refs:
        violations.append("world refs were not carried across continue_run")
    if not first_board_publish:
        violations.append("first task did not publish board status")
    if not second_ops_lookup:
        violations.append("second task did not perform ops lookup")
    if not second_ops_receipt:
        violations.append("second task did not create ops receipt")
    if second_board_calls:
        violations.append("second task called stale workflow-board tools")
    if second_file_calls:
        violations.append("second task called file tools")
    for term, ok in ops_term_hits.items():
        if not ok:
            violations.append(f"ops receipt missing term: {term}")
    for term, hit in stale_hits.items():
        if hit:
            violations.append(f"ops receipt leaked stale board term: {term}")
    return {
        "passed": not violations,
        "same_session": same_session,
        "carried_world_refs": carried_world_refs,
        "first_board_publish_count": len(first_board_publish),
        "second_ops_lookup_count": len(second_ops_lookup),
        "second_ops_receipt_count": len(second_ops_receipt),
        "second_board_tool_calls": len(second_board_calls),
        "second_file_tool_calls": len(second_file_calls),
        "ops_term_hits": ops_term_hits,
        "stale_hits": stale_hits,
        "receipt_ids": [(row.get("payload") or {}).get("receipt_id") for row in second_ops_receipt if isinstance(row.get("payload"), dict)],
        "violations": violations,
    }


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
