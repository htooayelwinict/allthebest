"""Run the greenfield AppV2.1 runtime-first probe."""

from __future__ import annotations

import json
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.providers.appv2_env import create_appv21_provider_from_appv2_env
from appv21.providers.env_config import load_dotenv_values


def main() -> int:
    args = _parse_args()
    repo = ROOT / "live_appv21_workspace_repo"
    _seed_repo(repo)
    _load_dotenv_into_environment(args.dotenv)
    _configure_worker_env(args)
    provider = None
    if args.provider == "appv2-env":
        provider = create_appv21_provider_from_appv2_env(dotenv_path=args.dotenv)
    runtime = AppV21AgentRuntime(root_path=repo, services=None if provider is None else _services(repo, provider))
    result = runtime.run("Clean up and organize this workspace, move notes/logs/artifacts, and create a workspace manifest.")
    out = ROOT / "plan" / "live-appv21-runtime-first-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"OUTPUT_PATH={out}")
    print(json.dumps({"status": result["status"], "event_count": len(result.get("events", []))}, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["deterministic", "appv2-env"], default="deterministic")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--worker-timeout", default="90")
    parser.add_argument("--max-tokens", default="2400")
    return parser.parse_args()


def _services(repo: Path, provider):
    from appv21.runtime.services import create_appv21_runtime_services

    return create_appv21_runtime_services(root_path=repo, provider=provider)


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
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    files = {
        "README.md": "# Probe repo\n",
        "notes/drafts/task_notes.md": "Task notes\n",
        "notes/raw/plan_notes.md": "Plan notes\n",
        "notes/raw/old_blob.txt": "keep this file\n",
        "reports/q1_summary.md": "Q1 summary\n",
        "tmp/tmp_report.md": "Temporary report\n",
        "artifacts/tmp/error_dump.json": "{\"error\": true}\n",
        "artifacts/tmp/old_build.log": "old build\n",
        "tests/test_workspace_cleanup.py": "def test_placeholder():\n    assert True\n",
    }
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
