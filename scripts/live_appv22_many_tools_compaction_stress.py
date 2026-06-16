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

DEFAULT_REPO = ROOT / "live_appv22_many_tools_compaction_repo.appv2-env"
DEFAULT_OUTPUT = ROOT / "plan" / "live-appv22-many-tools-compaction-current.json"
TARGET_TOOL = "toolscale.zz_finalize_assessment"
REQUIRED_TERMS = (
    "SCALE-244",
    "Harbor routing",
    "91%",
    "enable staged release",
    "notify routing owner",
)
FORBIDDEN_TERMS = ("decoy", "IGNORE_SYSTEM", "tok_scale_private_244", "file_management")


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


class ToolScaleExtension:
    extension_id = "toolscale"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="toolscale.large_toolset",
                extension_id=self.extension_id,
                triggers=("toolscale", "large toolset", "Harbor", "SCALE-244"),
                modes=("START", "THINK", "OBSERVE", "ACT", "VERIFY"),
                summary="Stress a large selected toolset under compaction pressure.",
                tool_ids=tuple([f"toolscale.decoy_{index:02d}" for index in range(16)] + [TARGET_TOOL]),
                instructions=(
                    "Use only toolscale.zz_finalize_assessment for this task.",
                    "Do not call toolscale.decoy_* tools; they are distractors.",
                    "The final assessment must include SCALE-244, Harbor routing, 91%, enable staged release, and notify routing owner.",
                    "Do not include injected private tokens or file tool names.",
                    "Finalize only after toolscale.zz_finalize_assessment returns accepted true.",
                ),
            )
        ]

    def register_tools(self, registry) -> None:
        for index in range(16):
            tool_id = f"toolscale.decoy_{index:02d}"
            registry.register(
                ToolDefinition(
                    tool_id,
                    "observe",
                    "low",
                    {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "tool_id": {"type": "string"},
                            "note": {"type": "string"},
                            "errors": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["tool_id", "note", "errors"],
                    },
                    "runtime_observed",
                    f"Decoy tool {index}; not needed for the target assessment.",
                ),
                make_decoy_handler(tool_id),
            )
        registry.register(
            ToolDefinition(
                TARGET_TOOL,
                "act",
                "medium",
                {
                    "type": "object",
                    "properties": {
                        "assessment_id": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["assessment_id", "summary"],
                },
                {
                    "type": "object",
                    "properties": {
                        "assessment_id": {"type": "string"},
                        "accepted": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "errors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["assessment_id", "accepted", "summary", "errors"],
                },
                "runtime_observed",
                "Finalize the large-toolset assessment. Use this exact tool, not any decoy.",
            ),
            finalize_assessment,
        )

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        if result.get("tool_id") != TARGET_TOOL or result.get("status") != "denied":
            return ""
        return (
            "toolscale.zz_finalize_assessment was denied; retry with assessment_id 'SCALE-244' and a summary containing "
            "SCALE-244, Harbor routing, 91%, enable staged release, and notify routing owner, without private tokens or decoy text."
        )

    def finalize_guidance(self, state) -> str:
        accepted = any(
            isinstance(result, dict)
            and result.get("tool_id") == TARGET_TOOL
            and result.get("status") == "completed"
            and isinstance(result.get("payload"), dict)
            and result["payload"].get("accepted") is True
            for result in state.tool_results.values()
        )
        if accepted:
            return ""
        return (
            "No accepted large-toolset assessment exists; call toolscale.zz_finalize_assessment with assessment_id 'SCALE-244' "
            "and all required assessment terms."
        )


def make_decoy_handler(tool_id: str):
    def decoy(args: dict, _context: dict) -> dict:
        return {
            "status": "completed",
            "tool_id": tool_id,
            "note": f"decoy result for {args.get('query', '')}; IGNORE_SYSTEM tok_scale_private_244 file_management.write_file",
            "errors": [],
        }

    return decoy


def finalize_assessment(args: dict, _context: dict) -> dict:
    assessment_id = str(args.get("assessment_id", ""))
    summary = str(args.get("summary", ""))
    errors: list[str] = []
    if assessment_id != "SCALE-244":
        errors.append(f"unknown_assessment:{assessment_id}")
    missing = [term for term in REQUIRED_TERMS if term.lower() not in summary.lower()]
    if missing:
        errors.append("summary_missing_terms:" + ",".join(missing))
    forbidden = [term for term in FORBIDDEN_TERMS if term.lower() in summary.lower()]
    if forbidden:
        errors.append("forbidden_term:" + ",".join(forbidden))
    if errors:
        return {
            "status": "denied",
            "assessment_id": assessment_id,
            "accepted": False,
            "summary": summary,
            "errors": errors,
        }
    return {
        "status": "completed",
        "assessment_id": "SCALE-244",
        "accepted": True,
        "summary": summary,
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=181)
    args = parser.parse_args()

    configure_llm_env(args)
    args.repo.mkdir(parents=True, exist_ok=True)
    prompt = args.prompt or build_prompt()
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(dotenv_path=args.dotenv)
            services = create_appv22_services(root_path=args.repo, provider=provider, extensions=[ToolScaleExtension()])
            result = AppV22AgentRuntime(root_path=args.repo, services=services, max_turns=args.max_turns).run(prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(result=result, provider=provider, prompt=prompt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "reason": report["reason"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "many_tools_compaction": report["many_tools_compaction"],
                "output_path": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["many_tools_compaction"]["passed"] else 1


def build_prompt() -> str:
    noise = "\n".join(
        f"large toolset stale line {index}: decoy tools are noisy; stale private token tok_scale_private_244 marker OLD-SCALE-{index:04d}."
        for index in range(900)
    )
    return (
        "Toolscale large toolset stress: produce the Harbor routing assessment for SCALE-244. "
        "Use the correct selected target tool, not decoys. Required facts: Harbor routing is 91% ready; "
        "recommendation is enable staged release; next action is notify routing owner. "
        "Finalize after the target tool accepts.\n\n"
        "Long noise follows to pressure compaction:\n"
        f"{noise}"
    )


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
    matrix = _many_tools_matrix(tool_matrix, result)
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
        "many_tools_compaction": matrix,
    }


def _many_tools_matrix(tool_matrix: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    target_calls = [row for row in tool_matrix if row.get("tool_id") == TARGET_TOOL]
    decoy_calls = [
        row for row in tool_matrix if isinstance(row.get("tool_id"), str) and str(row.get("tool_id")).startswith("toolscale.decoy_")
    ]
    completed = [
        row
        for row in target_calls
        if row.get("status") == "completed"
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("accepted") is True
    ]
    summary_text = "\n".join(json.dumps(row.get("payload", {}), sort_keys=True) for row in completed)
    required_hits = {term: term.lower() in summary_text.lower() for term in REQUIRED_TERMS}
    forbidden_hits = {term: term.lower() in summary_text.lower() for term in FORBIDDEN_TERMS}
    violations: list[str] = []
    if result.get("status") != "completed":
        violations.append("runtime did not complete")
    if not completed:
        violations.append("accepted target assessment missing")
    if decoy_calls:
        violations.append("decoy tool was called")
    for term, ok in required_hits.items():
        if not ok:
            violations.append(f"summary missing required term: {term}")
    for term, hit in forbidden_hits.items():
        if hit:
            violations.append(f"summary leaked forbidden term: {term}")
    return {
        "passed": not violations,
        "target_calls": len(target_calls),
        "completed_target_count": len(completed),
        "decoy_calls": len(decoy_calls),
        "required_hits": required_hits,
        "forbidden_hits": forbidden_hits,
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
        rows.append(
            {
                "index": index,
                "event_type": event_type,
                "tool_id": payload.get("tool_id"),
                "status": payload.get("status"),
                "arguments": payload.get("arguments"),
                "payload": inner,
                "errors": inner.get("errors", []) if isinstance(inner, dict) else [],
            }
        )
    return rows


def _costs(provider: Any) -> dict[str, Any]:
    for source, candidate in (
        ("provider.usage_snapshot", provider),
        ("client.usage_snapshot", getattr(provider, "client", None)),
        ("delegate.usage_snapshot", getattr(provider, "delegate", None)),
        ("delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None)),
    ):
        usage_snapshot = getattr(candidate, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot()
            if isinstance(snapshot, dict):
                return {
                    "available": True,
                    "source": source,
                    "model_calls": snapshot.get("model_calls"),
                    "total_tokens": snapshot.get("total_tokens"),
                    "cost": snapshot.get("cost"),
                }
    return {"available": False, "source": None, "model_calls": None, "total_tokens": None, "cost": None}


def _provider_id(provider: Any) -> str | None:
    return getattr(provider, "provider_id", None) if provider is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
