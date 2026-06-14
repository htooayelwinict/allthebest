"""Run a nested AppV2.1 file-management scenario and emit a full matrix report."""

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
    repo = _seed_nested_repo(ROOT / "live_appv21_nested_file_management_matrix_repo")
    _load_dotenv_into_environment(args.dotenv)
    _configure_worker_env(args)

    provider = None
    if args.provider == "appv2-env":
        provider = create_appv21_provider_from_appv2_env(dotenv_path=args.dotenv)
    services = create_appv21_runtime_services(root_path=repo, provider=provider) if provider is not None else None
    runtime = AppV21AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns)
    result = runtime.run(
        "Organize this nested file-management workspace. Move markdown notes/specs/reports to docs, move logs and JSON dumps to artifacts/logs, preserve tests, source, assets, secrets, keep/do_not_move/old_blob files, and create a manifest recording moves, held files, and collisions."
    )

    report = _build_report(repo=repo, result=result, provider=provider, max_turns=args.max_turns)
    out = ROOT / "plan" / "live-appv21-nested-file-management-matrix-probe.json"
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
                "tool_calls": report["tool_count"],
                "decisions": report["decision_count"],
                "loops": report["loop_turns"],
                "collisions": len((report["file_matrix"].get("manifest") or {}).get("collisions") or []),
                "held": len((report["file_matrix"].get("manifest") or {}).get("held") or []),
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
    parser.add_argument("--max-tokens", default="3000")
    parser.add_argument("--top-p", default="0.2")
    parser.add_argument("--frequency-penalty", default="0")
    parser.add_argument("--presence-penalty", default="0")
    parser.add_argument("--seed", default="55")
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


def _seed_nested_repo(repo: Path) -> Path:
    if repo.exists():
        shutil.rmtree(repo)
    files = {
        "README.md": "# Nested file management probe\n",
        "docs/summary.md": "Existing summary should win collision.\n",
        "docs/workspace_manifest.json": "{\"existing\": true}\n",
        "docs/archive/legacy_notes.md": "Already documented; should stay because it is under docs.\n",
        "teams/platform/notes/summary.md": "Platform summary collision candidate\n",
        "teams/platform/notes/incidents.md": "Incident notes\n",
        "teams/platform/notes/keep_runbook.md": "Keep this runbook in place\n",
        "teams/platform/specs/api.md": "API spec\n",
        "teams/platform/specs/security.md": "Security spec\n",
        "teams/product/reports/summary.md": "Product summary collision candidate\n",
        "teams/product/reports/q3.md": "Q3 report\n",
        "teams/product/reports/q4.md": "Q4 report\n",
        "teams/product/reports/do_not_move.md": "Protected product note\n",
        "research/alpha/spec.md": "Alpha spec\n",
        "research/beta/spec.md": "Beta spec collision candidate\n",
        "research/gamma/api.md": "API collision candidate\n",
        "tmp/builds/build.log": "build log\n",
        "tmp/builds/build.json": "{\"build\": true}\n",
        "tmp/sessions/error.log": "error log\n",
        "tmp/sessions/error.json": "{\"error\": true}\n",
        "artifacts/tmp/perf.log": "perf log\n",
        "artifacts/tmp/perf.json": "{\"perf\": true}\n",
        "ops/old_blob.txt": "old blob stays\n",
        "ops/keep_inventory.md": "keep inventory stays\n",
        "secrets/.env": "TOKEN=do-not-read\n",
        "secrets/id_rsa": "PRIVATE KEY\n",
        "assets/logo.svg": "<svg></svg>\n",
        "assets/mockup.png": "png bytes placeholder\n",
        "src/app.py": "print('app')\n",
        "src/config/settings.json": "{\"safe\": true}\n",
        "tests/test_nested_workspace.py": "def test_placeholder():\n    assert True\n",
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
        "event_count": totals["events"],
        "loop_turns": totals["loop_turns"],
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
            if "old_blob" in path
            or "keep" in path
            or "do_not_move" in path
            or path.startswith("tests/")
            or path.startswith("assets/")
            or path.startswith("src/")
            or path.startswith("secrets/")
        ],
        "manifest": manifest,
    }


if __name__ == "__main__":
    raise SystemExit(main())
