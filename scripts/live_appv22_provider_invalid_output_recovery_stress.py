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
from appv22.providers.appv2_env import AppV22NativeProvider, create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from live_appv22_workflow_board_stress import WorkflowBoardExtension

DEFAULT_REPO = ROOT / "live_appv22_provider_invalid_output_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-provider-invalid-output-recovery-current.json"
DEFAULT_PROMPT = (
    "The rollout board is messy. Use the board tools only, make the Q4 launch card unblockable, "
    "assign the right owner and due date from board evidence, publish a concise status update, then finalize."
)
REQUIRED_TERMS = ("BOARD-204", "Linh Tran", "2026-11-03", "consent banner", "payments QA", "launch checklist")
FORBIDDEN_TERMS = ("tok_invalid_provider_private", "RAW_INVALID_JSON", "backend trace")


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


class OneBadJsonClient:
    def __init__(self, delegate_client: Any) -> None:
        self.delegate_client = delegate_client
        self.calls = 0

    def complete_json(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "RAW_INVALID_JSON tok_invalid_provider_private backend trace file_management.write_file"
        return self.delegate_client.complete_json(**kwargs)

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        usage_snapshot = getattr(self.delegate_client, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot(reset=reset)
            if isinstance(snapshot, dict):
                snapshot = dict(snapshot)
                snapshot["model_calls"] = (snapshot.get("model_calls") or 0) + 1
                return snapshot
        return {"model_calls": self.calls, "total_tokens": None, "cost": None}


class OneBadJsonProvider(AppV22NativeProvider):
    provider_id = "one-bad-json-appv22-native"

    @property
    def bad_json_calls(self) -> int:
        return 1 if getattr(self.client, "calls", 0) >= 1 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--run-timeout-seconds", type=int, default=260)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=920)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            base_provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            provider = OneBadJsonProvider(client=OneBadJsonClient(base_provider.client))
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[WorkflowBoardExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "provider_invalid_output_recovery": report["provider_invalid_output_recovery"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["provider_invalid_output_recovery"]["passed"] else 1


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
    matrix = _invalid_output_matrix(tool_matrix, result, provider)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {"events": len(events), "decisions": event_order.count("DecisionProposed"), "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}), "compactions": event_order.count("ContextSummaryUpdated")},
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "provider_invalid_output_recovery": matrix,
    }


def _invalid_output_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any], provider: Any) -> dict[str, Any]:
    inspect_calls = [row for row in tool_matrix if row.get("tool_id") == "workflow_board.inspect_board" and row.get("status") == "completed"]
    assign_calls = [row for row in tool_matrix if row.get("tool_id") == "workflow_board.assign_card" and row.get("status") == "completed"]
    publish_calls = [row for row in tool_matrix if row.get("tool_id") == "workflow_board.publish_status" and row.get("status") == "completed"]
    control_surface = _control_surface(result)
    serialized_control_surface = json.dumps(control_surface, sort_keys=True, default=str)
    published_update = "\n".join(str((row.get("payload") or {}).get("update", "")) for row in publish_calls if isinstance(row.get("payload"), dict))
    term_hits = {term: term.lower() in published_update.lower() for term in REQUIRED_TERMS}
    forbidden_hits = {term: term.lower() in serialized_control_surface.lower() for term in FORBIDDEN_TERMS}
    invalid_compact_count = sum(
        1
        for event in result.get("events", [])
        if isinstance(event, dict)
        and event.get("event_type") == "DecisionProposed"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("kind") == "compact"
    )
    violations: list[str] = []
    if getattr(provider, "bad_json_calls", 0) != 1:
        violations.append("invalid provider output was not injected exactly once")
    if invalid_compact_count < 1:
        violations.append("invalid provider JSON was not recovered through sanitized compaction")
    if result.get("status") != "completed":
        violations.append("runtime did not recover to completion")
    if not inspect_calls:
        violations.append("board inspection was not completed")
    if not assign_calls:
        violations.append("assignment was not completed")
    if not publish_calls:
        violations.append("publish was not completed")
    for term, ok in term_hits.items():
        if not ok:
            violations.append(f"published update missing term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"invalid provider output leaked into control surface: {term}")
    return {"passed": not violations, "bad_json_calls": getattr(provider, "bad_json_calls", 0), "invalid_compact_count": invalid_compact_count, "inspect_calls": len(inspect_calls), "assign_calls": len(assign_calls), "publish_calls": len(publish_calls), "term_hits": term_hits, "forbidden_hits": forbidden_hits, "violations": violations}


def _control_surface(result: dict[str, Any]) -> dict[str, Any]:
    events = result.get("events", [])
    control_events: list[dict[str, Any]] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("event_type")
            if event_type in {"ToolCallCompleted", "ToolCallDenied"}:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                control_events.append(
                    {
                        "event_type": event_type,
                        "tool_id": payload.get("tool_id"),
                        "status": payload.get("status"),
                        "errors": payload.get("errors", []),
                    }
                )
                continue
            control_events.append(event)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "response": result.get("response"),
        "context_summary": result.get("context_summary"),
        "events": control_events,
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
