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

DEFAULT_REPO = ROOT / "live_appv22_non_file_ops_extension_repo.appv2-env"
DEFAULT_PROMPT = (
    "Use the ops extension tools to prepare an incident handoff receipt for incident INC-842. "
    "Do not use file tools. Look up the incident, then create the handoff receipt from the returned facts, then finalize."
)
REQUIRED_TERMS = ("INC-842", "Orion", "Nina Park", "2026-09-14 17:45", "cache saturation", "page database owner")


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


class OpsExtension:
    extension_id = "ops"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="ops.incident_handoff",
                extension_id=self.extension_id,
                triggers=("ops", "incident", "handoff", "receipt", "INC-"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Non-file ops tools for incident lookup and handoff receipt creation.",
                tool_ids=("ops.lookup_incident", "ops.create_handoff_receipt"),
                instructions=(
                    "Use ops.lookup_incident to fetch exact incident facts before creating a receipt.",
                    "Use ops.create_handoff_receipt with incident_id and summary after lookup evidence exists.",
                    "Do not call file tools for ops-only requests.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "ops.lookup_incident",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"incident_id": {"type": "string"}},
                    "required": ["incident_id"],
                },
                {
                    "type": "object",
                    "properties": {
                        "incident_id": {"type": "string"},
                        "service": {"type": "string"},
                        "owner": {"type": "string"},
                        "deadline": {"type": "string"},
                        "symptom": {"type": "string"},
                        "next_action": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["incident_id", "service", "owner", "deadline", "symptom", "next_action", "errors"],
                },
                "runtime_observed",
                "Look up exact incident facts by incident_id.",
            ),
            lookup_incident,
        )
        registry.register(
            ToolDefinition(
                "ops.create_handoff_receipt",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {
                        "incident_id": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["incident_id", "summary"],
                },
                {
                    "type": "object",
                    "properties": {
                        "receipt_id": {"type": "string"},
                        "incident_id": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["receipt_id", "incident_id", "accepted", "summary", "errors"],
                },
                "runtime_observed",
                "Create a non-file handoff receipt for an incident using exact looked-up facts.",
            ),
            create_handoff_receipt,
        )

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") != "ops.create_handoff_receipt" or result.get("status") != "denied":
            return ""
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        if any("summary_missing_terms" in str(error) for error in errors):
            return (
                "ops.create_handoff_receipt denied the summary because exact incident facts were missing; "
                "retry ops.create_handoff_receipt with incident_id 'INC-842' and a summary containing: "
                "INC-842, Orion, Nina Park, 2026-09-14 17:45, cache saturation, page database owner."
            )
        return ""

    def finalize_guidance(self, state) -> str:
        lookup = None
        accepted_receipt = None
        for result in state.tool_results.values():
            if not isinstance(result, dict):
                continue
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            if result.get("tool_id") == "ops.lookup_incident" and result.get("status") == "completed":
                lookup = payload
            if (
                result.get("tool_id") == "ops.create_handoff_receipt"
                and result.get("status") == "completed"
                and payload.get("accepted") is True
            ):
                accepted_receipt = payload
        if lookup is None or accepted_receipt is not None:
            return ""
        return (
            "Incident lookup is complete but no accepted handoff receipt exists; "
            "the next decision must call ops.create_handoff_receipt with incident_id 'INC-842' "
            "and a summary containing INC-842, Orion, Nina Park, 2026-09-14 17:45, "
            "cache saturation, and page database owner."
        )


def lookup_incident(args: dict, _context: dict) -> dict:
    incident_id = str(args.get("incident_id", ""))
    if incident_id != "INC-842":
        return {"status": "failed", "incident_id": incident_id, "service": "", "owner": "", "deadline": "", "symptom": "", "next_action": "", "errors": [f"unknown_incident:{incident_id}"]}
    return {
        "status": "completed",
        "incident_id": "INC-842",
        "service": "Orion",
        "owner": "Nina Park",
        "deadline": "2026-09-14 17:45",
        "symptom": "cache saturation",
        "next_action": "page database owner",
        "errors": [],
    }


def create_handoff_receipt(args: dict, _context: dict) -> dict:
    incident_id = str(args.get("incident_id", ""))
    summary = str(args.get("summary", ""))
    if incident_id != "INC-842":
        return {"status": "failed", "receipt_id": "", "incident_id": incident_id, "accepted": False, "summary": summary, "errors": [f"unknown_incident:{incident_id}"]}
    missing = [term for term in REQUIRED_TERMS if term.lower() not in summary.lower()]
    if missing:
        return {"status": "denied", "receipt_id": "", "incident_id": incident_id, "accepted": False, "summary": summary, "errors": ["summary_missing_terms:" + ",".join(missing)]}
    return {"status": "completed", "receipt_id": "opsrcpt_INC-842", "incident_id": incident_id, "accepted": True, "summary": summary, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=ROOT / "plan" / "live-appv22-non-file-ops-extension-current.json")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=83)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[OpsExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "ops_extension": report["ops_extension"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["ops_extension"]["passed"] else 1


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
    matrix = _ops_matrix(tool_matrix, result)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {
            "events": len(events),
            "decisions": event_order.count("DecisionProposed"),
            "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}),
            "compactions": event_order.count("ContextSummaryUpdated"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "ops_extension": matrix,
    }


def _ops_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    lookup_calls = [row for row in tool_matrix if row.get("tool_id") == "ops.lookup_incident" and row.get("status") == "completed"]
    receipt_calls = [row for row in tool_matrix if row.get("tool_id") == "ops.create_handoff_receipt" and row.get("status") == "completed"]
    file_tool_calls = [row for row in tool_matrix if str(row.get("tool_id", "")).startswith("file_management.")]
    receipt_summary = "\n".join(str((row.get("payload") or {}).get("summary", "")) for row in receipt_calls if isinstance(row.get("payload"), dict))
    term_hits = {term: term.lower() in receipt_summary.lower() for term in REQUIRED_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not lookup_calls:
        violations.append("ops lookup was not completed")
    if not receipt_calls:
        violations.append("ops receipt was not completed")
    if file_tool_calls:
        violations.append("file tool was called in non-file ops scenario")
    for term, ok in term_hits.items():
        if not ok:
            violations.append(f"receipt missing term: {term}")
    return {
        "passed": not violations,
        "lookup_calls": len(lookup_calls),
        "receipt_calls": len(receipt_calls),
        "file_tool_calls": len(file_tool_calls),
        "term_hits": term_hits,
        "receipt_ids": [(row.get("payload") or {}).get("receipt_id") for row in receipt_calls if isinstance(row.get("payload"), dict)],
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
