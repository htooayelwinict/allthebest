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

DEFAULT_REPO = ROOT / "live_appv22_schema_denial_recovery_repo.appv2-env"
DEFAULT_PROMPT = (
    "Use the QA extension tools for package PKG-314. First call qa.score_release with risk_score as the string 'high' "
    "so the schema denial path is exercised; after the tool denies that bad argument, retry with risk_score 7 and then finalize."
)
REQUIRED_TERMS = ("PKG-314", "approved with cautions")


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


class QaExtension:
    extension_id = "qa"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="qa.release_scoring",
                extension_id=self.extension_id,
                triggers=("QA", "qa", "score", "schema", "PKG-"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Non-file QA release scoring tool with strict argument schema.",
                tool_ids=("qa.score_release",),
                instructions=(
                    "Use qa.score_release to score release packages.",
                    "If qa.score_release is denied for invalid argument type, retry with risk_score as an integer.",
                    "Finalize only after qa.score_release completes with an accepted verdict.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        registry.register(
            ToolDefinition(
                "qa.score_release",
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {
                        "package_id": {"type": "string"},
                        "risk_score": {"type": "integer"},
                    },
                    "required": ["package_id", "risk_score"],
                },
                {
                    "type": "object",
                    "properties": {
                        "package_id": {"type": "string"},
                        "risk_score": {"type": "integer"},
                        "verdict": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["package_id", "risk_score", "verdict", "accepted", "errors"],
                },
                "runtime_observed",
                "Score a release package. risk_score must be an integer.",
            ),
            score_release,
        )

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") != "qa.score_release" or result.get("status") != "denied":
            return ""
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        if any("invalid_argument_type:risk_score:expected_integer" in str(error) for error in errors):
            return "qa.score_release rejected risk_score type; retry qa.score_release with package_id 'PKG-314' and integer risk_score 7."
        return ""

    def finalize_guidance(self, state) -> str:
        accepted = False
        saw_denial = False
        for result in state.tool_results.values():
            if not isinstance(result, dict) or result.get("tool_id") != "qa.score_release":
                continue
            if result.get("status") == "denied":
                saw_denial = True
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            if result.get("status") == "completed" and payload.get("accepted") is True:
                accepted = True
        if accepted:
            return ""
        if saw_denial:
            return "Schema denial occurred but no accepted QA score exists; call qa.score_release with package_id 'PKG-314' and integer risk_score 7 before finalizing."
        return ""


def score_release(args: dict, _context: dict) -> dict:
    package_id = str(args.get("package_id", ""))
    risk_score = args.get("risk_score")
    if package_id != "PKG-314":
        return {"status": "failed", "package_id": package_id, "risk_score": 0, "verdict": "", "accepted": False, "errors": [f"unknown_package:{package_id}"]}
    if risk_score == 7:
        verdict = "approved with cautions"
    else:
        verdict = "review required"
    return {"status": "completed", "package_id": package_id, "risk_score": int(risk_score), "verdict": verdict, "accepted": risk_score == 7, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=ROOT / "plan" / "live-appv22-schema-denial-recovery-current.json")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=97)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[QaExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=args.prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": report["status"], "reason": report["reason"], "provider": report["provider"], "totals": report["totals"], "costs": report["costs"], "schema_denial_recovery": report["schema_denial_recovery"], "output_path": str(args.output)}, sort_keys=True))
    return 0 if report["schema_denial_recovery"]["passed"] else 1


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
        "totals": {
            "events": len(events),
            "decisions": event_order.count("DecisionProposed"),
            "tool_calls": sum(1 for event_type in event_order if event_type in {"ToolCallCompleted", "ToolCallDenied"}),
            "compactions": event_order.count("ContextSummaryUpdated"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": tool_matrix,
        "schema_denial_recovery": matrix,
    }


def _schema_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    denials = [row for row in tool_matrix if row.get("tool_id") == "qa.score_release" and row.get("status") == "denied"]
    completed = [row for row in tool_matrix if row.get("tool_id") == "qa.score_release" and row.get("status") == "completed"]
    completed_text = "\n".join(json.dumps(row.get("payload", {}), sort_keys=True) for row in completed)
    term_hits = {term: term.lower() in completed_text.lower() for term in REQUIRED_TERMS}
    accepted = any(isinstance(row.get("payload"), dict) and row["payload"].get("accepted") is True for row in completed)
    risk_score_7 = any(isinstance(row.get("payload"), dict) and row["payload"].get("risk_score") == 7 for row in completed)
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not denials:
        violations.append("schema denial was not observed")
    if not completed:
        violations.append("qa score was not completed after denial")
    if not accepted:
        violations.append("accepted QA score missing")
    if not risk_score_7:
        violations.append("completed score missing structured risk_score 7")
    for term, ok in term_hits.items():
        if not ok:
            violations.append(f"completed score missing term: {term}")
    return {
        "passed": not violations,
        "schema_denial_count": len(denials),
        "completed_score_count": len(completed),
        "accepted": accepted,
        "risk_score_7": risk_score_7,
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
