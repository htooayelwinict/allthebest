"""Run a complex AppV2.1 file-management scenario and emit a full matrix report."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.providers.appv2_env import create_appv21_provider_from_appv2_env
from appv21.providers.env_config import load_dotenv_values
from appv21.runtime.services import create_appv21_runtime_services

QWEN_INPUT_PER_MILLION = 0.11
QWEN_OUTPUT_PER_MILLION = 0.80


def main() -> int:
    args = _parse_args()
    repo = _seed_complex_repo(ROOT / "live_appv21_complex_file_management_matrix_repo")
    _load_dotenv_into_environment(args.dotenv)
    _configure_worker_env(args)

    provider = None
    if args.provider == "appv2-env":
        provider = create_appv21_provider_from_appv2_env(dotenv_path=args.dotenv)
    services = create_appv21_runtime_services(root_path=repo, provider=provider) if provider is not None else None
    runtime = AppV21AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns)
    result = runtime.run(
        "Organize this complex workspace. Move markdown notes/reports/specs to docs, move logs/json dumps to artifacts/logs, preserve tests, existing docs, assets, keep/do_not_move/old_blob files, and create a manifest that records moves, held files, and collisions."
    )

    report = _build_report(repo=repo, result=result, provider=provider, max_turns=args.max_turns)
    out = ROOT / "plan" / "live-appv21-complex-file-management-matrix-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"OUTPUT_PATH={out}")
    print(
        json.dumps(
            {
                "status": report["status"],
                "provider": report["provider"],
                "model_calls": report["costs"]["model_calls"],
                "total_tokens": report["costs"].get("total_tokens", 0),
                "estimated_cost_usd": report["costs"].get("estimated_qwen_cost_usd", 0.0),
                "tool_calls": report["totals"]["tool_calls"],
                "decisions": report["totals"]["decisions"],
                "loops": report["totals"]["loop_turns"],
                "collisions": len((report["file_matrix"].get("manifest") or {}).get("collisions") or []),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "completed" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deterministic", "appv2-env"], default="appv2-env")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--worker-timeout", default="120")
    parser.add_argument("--max-tokens", default="2800")
    parser.add_argument("--top-p", default="0.2")
    parser.add_argument("--frequency-penalty", default="0")
    parser.add_argument("--presence-penalty", default="0")
    parser.add_argument("--seed", default="34")
    parser.add_argument("--max-turns", type=int, default=14)
    return parser.parse_args()


def _load_dotenv_into_environment(dotenv_path: str) -> None:
    for key, value in load_dotenv_values(dotenv_path).items():
        os.environ.setdefault(key, value)


def _configure_worker_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    if args.worker_model:
        os.environ["APPV2_WORKER_LLM_MODEL"] = args.worker_model
    os.environ["APPV2_WORKER_LLM_PROVIDER_SORT"] = "latency"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = "0"
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_FREQUENCY_PENALTY"] = str(args.frequency_penalty)
    os.environ["APPV2_WORKER_LLM_PRESENCE_PENALTY"] = str(args.presence_penalty)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def _seed_complex_repo(repo: Path) -> Path:
    if repo.exists():
        shutil.rmtree(repo)
    files = {
        "README.md": "# Complex file management probe\n",
        "docs/roadmap.md": "Existing roadmap must stay in place.\n",
        "docs/workspace_manifest.json": "{\"existing\": true}\n",
        "notes/team/standup.md": "Standup notes\n",
        "notes/team/retro.md": "Retro notes\n",
        "notes/team/keep_decisions.md": "Keep decisions in place\n",
        "notes/private/do_not_move.md": "Protected note\n",
        "notes/private/old_blob.txt": "Legacy text should stay\n",
        "projects/alpha/spec.md": "Alpha spec\n",
        "projects/beta/spec.md": "Beta spec collision candidate\n",
        "projects/gamma/roadmap.md": "Roadmap collision with existing docs/roadmap.md\n",
        "reports/2026/q1_summary.md": "Q1 summary\n",
        "reports/2026/q2_summary.md": "Q2 summary\n",
        "research/spec.md": "Research spec collision candidate\n",
        "tmp/session/error_dump.json": "{\"error\": true}\n",
        "tmp/session/run.log": "run log\n",
        "tmp/cache/cache.log": "cache log\n",
        "artifacts/tmp/trace.json": "{\"trace\": true}\n",
        "artifacts/tmp/build.log": "build log\n",
        "assets/logo.svg": "<svg></svg>\n",
        "assets/data.csv": "id,value\n1,2\n",
        "src/app.py": "print('hello')\n",
        "tests/test_complex_workspace.py": "def test_placeholder():\n    assert True\n",
    }
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def _build_report(*, repo: Path, result: dict[str, Any], provider: Any, max_turns: int) -> dict[str, Any]:
    events = result.get("events", [])
    event_types = [event["event_type"] for event in events]
    decisions = [event for event in events if event["event_type"] == "DecisionProposed"]
    tool_events = [event for event in events if event["event_type"] in {"ToolCallCompleted", "ToolCallDenied"}]
    mutation_events = [event for event in events if event["event_type"] in {"MutationLeaseIssued", "MutationApplied"}]
    verification_events = [event for event in events if event["event_type"] == "VerificationRecorded"]
    costs = _usage_snapshot(provider)
    costs["estimated_qwen_cost_usd"] = _estimated_qwen_cost(costs)
    totals = {
        "events": len(events),
        "loop_turns": len(decisions),
        "decisions": len(decisions),
        "tool_calls": len(tool_events),
        "tool_denials": event_types.count("ToolCallDenied"),
        "mutation_leases": event_types.count("MutationLeaseIssued"),
        "mutation_receipts": event_types.count("MutationApplied"),
        "verification_receipts": len(verification_events),
        "pauses": event_types.count("RunPaused"),
        "compactions": event_types.count("ContextCompacted"),
    }
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "provider": getattr(provider, "provider_id", "deterministic-workspace"),
        "max_turns": max_turns,
        "decision_count": totals["decisions"],
        "tool_count": totals["tool_calls"],
        "denied_count": totals["tool_denials"],
        "pause_count": totals["pauses"],
        "compaction_count": totals["compactions"],
        "totals": totals,
        "costs": costs,
        "decision_matrix": [_decision_summary(index, event) for index, event in enumerate(decisions)],
        "tool_matrix": [_event_summary(event) for event in tool_events],
        "mutation_matrix": [_event_summary(event) for event in mutation_events],
        "verification_matrix": [_event_summary(event) for event in verification_events],
        "event_order": event_types,
        "file_matrix": _file_matrix(repo),
        "result_summary": {key: result.get(key) for key in ["artifact_ids", "mutation_receipts", "verification_receipts"]},
    }


def _usage_snapshot(provider: Any) -> dict[str, Any]:
    client = getattr(provider, "client", None)
    if client is not None and hasattr(client, "usage_snapshot"):
        return client.usage_snapshot()
    return {
        "model_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "cost": 0.0,
        "stages": [],
    }


def _estimated_qwen_cost(costs: dict[str, Any]) -> float:
    input_tokens = int(costs.get("input_tokens") or costs.get("prompt_tokens") or 0)
    output_tokens = int(costs.get("output_tokens") or costs.get("completion_tokens") or 0)
    return round((input_tokens / 1_000_000 * QWEN_INPUT_PER_MILLION) + (output_tokens / 1_000_000 * QWEN_OUTPUT_PER_MILLION), 8)


def _decision_summary(index: int, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    return {
        "turn": payload.get("turn_index", index),
        "decision_id": payload.get("decision_id"),
        "decision_kind": payload.get("kind"),
        "reason": payload.get("reason"),
        "evidence_refs": payload.get("evidence_refs", []),
        "payload_keys": sorted((payload.get("payload") or {}).keys()),
    }


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    return {
        "event_type": event.get("event_type"),
        "event_id": event.get("event_id"),
        "tool_name": payload.get("tool_name"),
        "status": payload.get("status"),
        "trust": payload.get("trust"),
        "prompt_summary": payload.get("prompt_summary"),
        "payload_keys": sorted((payload.get("payload") or payload).keys()) if isinstance(payload, dict) else [],
        "receipt_id": payload.get("receipt_id"),
        "lease_id": payload.get("lease_id"),
        "verification_id": payload.get("verification_id"),
    }


def _file_matrix(repo: Path) -> dict[str, Any]:
    paths = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())
    manifest_path = repo / "docs" / "workspace_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    return {
        "files": paths,
        "docs_files": [path for path in paths if path.startswith("docs/")],
        "log_artifacts": [path for path in paths if path.startswith("artifacts/logs/")],
        "preserved_files": [
            path
            for path in paths
            if "old_blob" in path or "keep" in path or "do_not_move" in path or path.startswith("tests/") or path.startswith("assets/") or path.startswith("src/")
        ],
        "manifest": manifest,
    }


if __name__ == "__main__":
    raise SystemExit(main())
