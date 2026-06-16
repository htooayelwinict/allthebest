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

DEFAULT_REPO = ROOT / "live_appv22_hook_exception_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-hook-exception-isolation-current.json"
DEFAULT_PROMPT = "Use hook-lab tools only. Fetch LAB-44 and publish the safe lab note, then finalize."
REQUIRED_TERMS = ("LAB-44", "safe lab note", "Atlas", "Nora Quinn", "rollback rehearsal")
FORBIDDEN_TERMS = ("tok_hook_private_44", "backend trace", "stack frame", "file_management.write_file")


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


class HookLabExtension:
    extension_id = "hook_lab"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="hook_lab.exception_isolation",
                extension_id=self.extension_id,
                triggers=("hook-lab", "LAB-", "safe lab note"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Generic hook exception isolation stress skill.",
                tool_ids=("hook_lab.fetch", "hook_lab.publish"),
                instructions=(
                    "Use hook_lab.fetch before publishing.",
                    "If extension hooks fail internally, continue from safe tool evidence and do not expose hook internals.",
                    "Use hook_lab.publish with exact public facts.",
                    "Finalize only after hook_lab.publish returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "hook_lab.fetch",
                "observe",
                "low",
                {"type": "object", "properties": {"lab_id": {"type": "string"}}, "required": ["lab_id"]},
                {
                    "type": "object",
                    "properties": {
                        "lab_id": {"type": "string"},
                        "project": {"type": "string"},
                        "owner": {"type": "string"},
                        "task": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["lab_id", "project", "owner", "task", "errors"],
                },
                "runtime_observed",
                "Fetch safe lab facts while the extension after-hook fails internally.",
            ),
            fetch_lab,
        )
        registry.register(
            ToolDefinition(
                "hook_lab.publish",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {"lab_id": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["lab_id", "note"],
                },
                {
                    "type": "object",
                    "properties": {
                        "note_id": {"type": "string"},
                        "lab_id": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "note": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["note_id", "lab_id", "accepted", "note", "errors"],
                },
                "runtime_observed",
                "Publish a safe lab note.",
            ),
            publish_lab_note,
        )

    def after_tool_call(self, _state, result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("tool_id") == "hook_lab.fetch":
            raise RuntimeError("tok_hook_private_44 backend trace stack frame file_management.write_file")
        return None

    def finalize_guidance(self, state) -> str:
        for result in state.tool_results.values():
            if (
                isinstance(result, dict)
                and result.get("tool_id") == "hook_lab.publish"
                and result.get("status") == "completed"
                and isinstance(result.get("payload"), dict)
                and result["payload"].get("accepted") is True
            ):
                return ""
        return "No accepted lab note exists; call hook_lab.publish with lab_id 'LAB-44' and note 'LAB-44 safe lab note: Atlas owner Nora Quinn will run rollback rehearsal.'"


def fetch_lab(args: dict, _context: dict) -> dict:
    lab_id = str(args.get("lab_id", ""))
    if lab_id != "LAB-44":
        return {"status": "failed", "lab_id": lab_id, "project": "", "owner": "", "task": "", "errors": ["unknown_lab"]}
    return {"status": "completed", "lab_id": "LAB-44", "project": "Atlas", "owner": "Nora Quinn", "task": "rollback rehearsal", "errors": []}


def publish_lab_note(args: dict, _context: dict) -> dict:
    lab_id = str(args.get("lab_id", ""))
    note = str(args.get("note", ""))
    errors: list[str] = []
    if lab_id != "LAB-44":
        errors.append(f"unknown_lab:{lab_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in note.lower()]
    if missing:
        errors.append("note_missing_terms:" + ",".join(missing))
    forbidden = [term for term in FORBIDDEN_TERMS if term.lower() in note.lower()]
    if forbidden:
        errors.append("forbidden_term:" + ",".join(forbidden))
    if errors:
        return {"status": "denied", "note_id": "", "lab_id": lab_id, "accepted": False, "note": note, "errors": errors}
    return {"status": "completed", "note_id": "note_LAB-44", "lab_id": lab_id, "accepted": True, "note": note, "errors": []}


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
    parser.add_argument("--seed", type=int, default=44)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[HookLabExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "hook_exception_isolation": report["hook_exception_isolation"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["hook_exception_isolation"]["passed"] else 1


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
    matrix = _hook_matrix(tool_matrix, result)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {"events": len(events), "decisions": event_order.count("DecisionProposed"), "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}), "compactions": event_order.count("ContextSummaryUpdated")},
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "hook_exception_isolation": matrix,
    }


def _hook_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    fetch_calls = [row for row in tool_matrix if row.get("tool_id") == "hook_lab.fetch" and row.get("status") == "completed"]
    publish_calls = [row for row in tool_matrix if row.get("tool_id") == "hook_lab.publish" and row.get("status") == "completed"]
    file_tool_calls = [row for row in tool_matrix if str(row.get("tool_id", "")).startswith("file_management.")]
    serialized = json.dumps(result, sort_keys=True, default=str)
    note = "\n".join(str((row.get("payload") or {}).get("note", "")) for row in publish_calls if isinstance(row.get("payload"), dict))
    required_hits = {term: term.lower() in note.lower() for term in REQUIRED_TERMS}
    forbidden_hits = {term: term.lower() in serialized.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not fetch_calls:
        violations.append("lab fetch was not completed")
    if not publish_calls:
        violations.append("lab note was not published")
    if file_tool_calls:
        violations.append("file tool was called")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"note missing term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"hook exception leaked forbidden term: {term}")
    return {"passed": not violations, "fetch_calls": len(fetch_calls), "publish_calls": len(publish_calls), "file_tool_calls": len(file_tool_calls), "required_hits": required_hits, "forbidden_hits": forbidden_hits, "note_ids": [(row.get("payload") or {}).get("note_id") for row in publish_calls if isinstance(row.get("payload"), dict)], "violations": violations}


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
