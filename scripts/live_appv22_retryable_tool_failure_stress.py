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

DEFAULT_REPO = ROOT / "live_appv22_retryable_tool_failure_repo.appv2-env"
DEFAULT_PROMPT = (
    "Use the flaky status extension to fetch deployment DEP-515. The first backend attempt may fail transiently; "
    "if the tool says retryable, retry the same selected tool call, then finalize only after the completed result."
)
REQUIRED_TERMS = ("DEP-515", "Helios", "stable", "2026-10-02 10:15")


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


class FlakyStatusExtension:
    extension_id = "flaky_status"

    def __init__(self) -> None:
        self.calls = 0

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="flaky_status.fetch",
                extension_id=self.extension_id,
                triggers=("flaky", "status", "deployment", "DEP-"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Fetch deployment status from a transient backend; retry when result says retryable.",
                tool_ids=("flaky_status.fetch_deployment",),
                instructions=(
                    "Call flaky_status.fetch_deployment with deployment_id DEP-515.",
                    "If the tool result is failed and retryable, retry the same tool call once.",
                    "Finalize only after a completed deployment status result.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "flaky_status.fetch_deployment",
                "observe",
                "low",
                {
                    "type": "object",
                    "properties": {"deployment_id": {"type": "string"}},
                    "required": ["deployment_id"],
                },
                {
                    "type": "object",
                    "properties": {
                        "deployment_id": {"type": "string"},
                        "service": {"type": "string"},
                        "status": {"type": "string"},
                        "checked_at": {"type": "string"},
                        "retryable": {"type": "boolean"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["deployment_id", "service", "status", "checked_at", "retryable", "errors"],
                },
                "runtime_observed",
                "Fetch deployment status. If failed with retryable true, retry the same call.",
            ),
            self.fetch_deployment,
        )

    def fetch_deployment(self, args: dict, _context: dict) -> dict:
        deployment_id = str(args.get("deployment_id", ""))
        if deployment_id != "DEP-515":
            return {"status": "failed", "deployment_id": deployment_id, "service": "", "checked_at": "", "retryable": False, "errors": [f"unknown_deployment:{deployment_id}"]}
        self.calls += 1
        if self.calls == 1:
            return {
                "status": "failed",
                "deployment_id": "DEP-515",
                "service": "Helios",
                "checked_at": "",
                "retryable": True,
                "errors": ["transient_backend_unavailable"],
            }
        return {
            "status": "completed",
            "deployment_id": "DEP-515",
            "service": "Helios",
            "status_value": "stable",
            "status": "completed",
            "checked_at": "2026-10-02 10:15",
            "retryable": False,
            "errors": [],
        }

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") != "flaky_status.fetch_deployment" or result.get("status") != "failed":
            return ""
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if payload.get("retryable") is True:
            return "flaky_status.fetch_deployment failed with retryable=true; retry the same tool_call with deployment_id 'DEP-515'."
        return ""

    def finalize_guidance(self, state) -> str:
        saw_retryable_failure = False
        saw_completed = False
        for result in state.tool_results.values():
            if not isinstance(result, dict) or result.get("tool_id") != "flaky_status.fetch_deployment":
                continue
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            if result.get("status") == "failed" and payload.get("retryable") is True:
                saw_retryable_failure = True
            if result.get("status") == "completed":
                saw_completed = True
        if saw_completed or not saw_retryable_failure:
            return ""
        return "A retryable deployment fetch failed but no completed fetch exists; retry flaky_status.fetch_deployment with deployment_id 'DEP-515' before finalizing."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=ROOT / "plan" / "live-appv22-retryable-tool-failure-current.json")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=109)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[FlakyStatusExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "retryable_failure_recovery": report["retryable_failure_recovery"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["retryable_failure_recovery"]["passed"] else 1


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
    matrix = _retry_matrix(tool_matrix, result)
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
        "retryable_failure_recovery": matrix,
    }


def _retry_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    failed_retryable = [row for row in tool_matrix if row.get("tool_id") == "flaky_status.fetch_deployment" and row.get("status") == "failed" and isinstance(row.get("payload"), dict) and row["payload"].get("retryable") is True]
    completed = [row for row in tool_matrix if row.get("tool_id") == "flaky_status.fetch_deployment" and row.get("status") == "completed"]
    completed_text = "\n".join(json.dumps(row.get("payload", {}), sort_keys=True) for row in completed)
    term_hits = {term: term.lower() in completed_text.lower() for term in REQUIRED_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not failed_retryable:
        violations.append("retryable failure was not observed")
    if not completed:
        violations.append("completed retry result missing")
    for term, ok in term_hits.items():
        if not ok:
            violations.append(f"completed result missing term: {term}")
    return {
        "passed": not violations,
        "retryable_failure_count": len(failed_retryable),
        "completed_count": len(completed),
        "term_hits": term_hits,
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
