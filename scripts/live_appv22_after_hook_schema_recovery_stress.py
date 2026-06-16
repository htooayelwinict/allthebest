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

DEFAULT_REPO = ROOT / "live_appv22_after_hook_schema_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-after-hook-schema-recovery-current.json"
DEFAULT_PROMPT = (
    "Use schema-lab tools only. Publish SCH-27. First call schema_lab.publish_primary exactly once after lookup; "
    "if the primary result is invalid or failed, recover with schema_lab.publish_fallback and finalize."
)
REQUIRED_TERMS = ("SCH-27", "schema-safe", "Ilya Noor", "2026-06-30", "adapter rehearsal")
FORBIDDEN_TERMS = ("tok_schema_private_27", "backend trace", "file_management.write_file")


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


class SchemaLabExtension:
    extension_id = "schema_lab"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="schema_lab.after_hook_recovery",
                extension_id=self.extension_id,
                triggers=("schema-lab", "schema", "SCH-"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Generic after-hook schema validation recovery stress skill.",
                tool_ids=("schema_lab.lookup", "schema_lab.publish_primary", "schema_lab.publish_fallback"),
                instructions=(
                    "Use schema_lab.lookup before publishing.",
                    "Use schema_lab.publish_primary first exactly once after lookup before fallback.",
                    "If primary publish result fails after-hook schema validation, use schema_lab.publish_fallback.",
                    "Finalize only after a publish tool returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "schema_lab.lookup",
                "observe",
                "low",
                {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]},
                {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "string"},
                        "owner": {"type": "string"},
                        "date": {"type": "string"},
                        "activity": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["case_id", "owner", "date", "activity", "errors"],
                },
                "runtime_observed",
                "Look up schema lab facts.",
            ),
            lookup_case,
        )
        for tool_id, guidance, handler in (
            ("schema_lab.publish_primary", "Publish primary schema lab notice.", publish_primary_case),
            ("schema_lab.publish_fallback", "Publish fallback schema lab notice.", publish_case),
        ):
            registry.register(
                ToolDefinition(
                    tool_id,
                    "act",
                    "medium",
                    {
                        "type": "object",
                        "properties": {"case_id": {"type": "string"}, "message": {"type": "string"}},
                        "required": ["case_id", "message"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "publish_id": {"type": "string"},
                            "case_id": {"type": "string"},
                            "accepted": {"type": "boolean"},
                            "message": {"type": "string"},
                            "errors": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["publish_id", "case_id", "accepted", "message", "errors"],
                    },
                    "runtime_observed",
                    guidance,
                ),
                handler,
            )

    def after_tool_call(self, _state, result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("tool_id") != "schema_lab.publish_primary" or result.get("status") != "completed":
            return None
        malformed = dict(result)
        payload = dict(malformed.get("payload") or {})
        payload.pop("accepted", None)
        malformed["payload"] = payload
        return malformed

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") == "schema_lab.publish_primary" and result.get("status") == "failed":
            return (
                "schema_lab.publish_primary produced an invalid post-hook result; do not retry it. "
                "Use schema_lab.publish_fallback with case_id 'SCH-27' and exact message: "
                "'SCH-27 schema-safe: Ilya Noor owns the 2026-06-30 adapter rehearsal.'"
            )
        if result.get("tool_id") == "schema_lab.publish_fallback" and result.get("status") == "denied":
            return "Use exact fallback message: 'SCH-27 schema-safe: Ilya Noor owns the 2026-06-30 adapter rehearsal.'"
        return ""

    def finalize_guidance(self, state) -> str:
        has_lookup = False
        has_primary = False
        for result in state.tool_results.values():
            if (
                isinstance(result, dict)
                and result.get("tool_id") == "schema_lab.lookup"
                and result.get("status") == "completed"
            ):
                has_lookup = True
            if isinstance(result, dict) and result.get("tool_id") == "schema_lab.publish_primary":
                has_primary = True
            if (
                isinstance(result, dict)
                and result.get("tool_id") in {"schema_lab.publish_primary", "schema_lab.publish_fallback"}
                and result.get("status") == "completed"
                and isinstance(result.get("payload"), dict)
                and result["payload"].get("accepted") is True
            ):
                return ""
        if has_lookup and not has_primary:
            return (
                "Schema lab lookup is complete but primary publish has not been attempted; "
                "the next decision must call schema_lab.publish_primary exactly once with case_id 'SCH-27' and exact message: "
                "'SCH-27 schema-safe: Ilya Noor owns the 2026-06-30 adapter rehearsal.'"
            )
        return (
            "No accepted schema lab publish exists; use schema_lab.publish_fallback with case_id 'SCH-27' and exact message: "
            "'SCH-27 schema-safe: Ilya Noor owns the 2026-06-30 adapter rehearsal.'"
        )


def lookup_case(args: dict, _context: dict) -> dict:
    case_id = str(args.get("case_id", ""))
    if case_id != "SCH-27":
        return {"status": "failed", "case_id": case_id, "owner": "", "date": "", "activity": "", "errors": ["unknown_case"]}
    return {"status": "completed", "case_id": "SCH-27", "owner": "Ilya Noor", "date": "2026-06-30", "activity": "adapter rehearsal", "errors": []}


def publish_case(args: dict, _context: dict) -> dict:
    case_id = str(args.get("case_id", ""))
    message = str(args.get("message", ""))
    errors: list[str] = []
    if case_id != "SCH-27":
        errors.append(f"unknown_case:{case_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in message.lower()]
    if missing:
        errors.append("message_missing_terms:" + ",".join(missing))
    forbidden = [term for term in FORBIDDEN_TERMS if term.lower() in message.lower()]
    if forbidden:
        errors.append("forbidden_term:" + ",".join(forbidden))
    if errors:
        return {"status": "denied", "publish_id": "", "case_id": case_id, "accepted": False, "message": message, "errors": errors}
    return {"status": "completed", "publish_id": "publish_SCH-27", "case_id": case_id, "accepted": True, "message": message, "errors": []}


def publish_primary_case(args: dict, _context: dict) -> dict:
    case_id = str(args.get("case_id", ""))
    message = str(args.get("message", ""))
    if case_id != "SCH-27":
        return {"status": "denied", "publish_id": "", "case_id": case_id, "accepted": False, "message": message, "errors": [f"unknown_case:{case_id}"]}
    return {"status": "completed", "publish_id": "primary_SCH-27", "case_id": case_id, "accepted": True, "message": message, "errors": []}


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
    parser.add_argument("--seed", type=int, default=27)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[SchemaLabExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "after_hook_schema_recovery": report["after_hook_schema_recovery"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["after_hook_schema_recovery"]["passed"] else 1


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
    matrix = _schema_matrix(tool_matrix, result)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {"events": len(events), "decisions": event_order.count("DecisionProposed"), "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}), "compactions": event_order.count("ContextSummaryUpdated")},
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "after_hook_schema_recovery": matrix,
    }


def _schema_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    lookup_calls = [row for row in tool_matrix if row.get("tool_id") == "schema_lab.lookup" and row.get("status") == "completed"]
    failed_primary = [row for row in tool_matrix if row.get("tool_id") == "schema_lab.publish_primary" and row.get("status") == "failed"]
    completed_primary = [row for row in tool_matrix if row.get("tool_id") == "schema_lab.publish_primary" and row.get("status") == "completed"]
    completed_fallback = [row for row in tool_matrix if row.get("tool_id") == "schema_lab.publish_fallback" and row.get("status") == "completed"]
    file_tool_calls = [row for row in tool_matrix if str(row.get("tool_id", "")).startswith("file_management.")]
    message = "\n".join(str((row.get("payload") or {}).get("message", "")) for row in completed_fallback if isinstance(row.get("payload"), dict))
    required_hits = {term: term.lower() in message.lower() for term in REQUIRED_TERMS}
    serialized = json.dumps(result, sort_keys=True, default=str)
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not lookup_calls:
        violations.append("lookup was not completed")
    if not failed_primary:
        violations.append("malformed primary result was not downgraded to failed")
    if completed_primary:
        violations.append("malformed primary result completed unexpectedly")
    if not completed_fallback:
        violations.append("fallback publish was not completed")
    if file_tool_calls:
        violations.append("file tool was called")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"fallback message missing term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"forbidden term leaked: {term}")
    return {"passed": not violations, "lookup_calls": len(lookup_calls), "failed_primary_count": len(failed_primary), "completed_primary_count": len(completed_primary), "completed_fallback_count": len(completed_fallback), "file_tool_calls": len(file_tool_calls), "required_hits": required_hits, "forbidden_hits": forbidden_hits, "publish_ids": [(row.get("payload") or {}).get("publish_id") for row in completed_fallback if isinstance(row.get("payload"), dict)], "violations": violations}


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
