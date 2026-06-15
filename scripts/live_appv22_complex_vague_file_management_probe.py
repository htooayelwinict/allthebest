from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.providers.deterministic import DeterministicAppV22Provider
from appv22.runtime.services import create_appv22_services

DEFAULT_PROMPT = "Can you clean this mess up safely and keep a record?"
DEFAULT_REPO = ROOT / "live_appv22_complex_vague_file_management_repo"
DEFAULT_REPORT = ROOT / "plan" / "live-appv22-complex-vague-file-management-probe.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["deterministic", "appv2-env"], default="deterministic")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    repo = seed_repo(DEFAULT_REPO)
    provider = create_provider(args.provider, dotenv_path=args.dotenv)
    services = create_appv22_services(
        root_path=repo,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=12).run(args.prompt)
    report = build_report(repo=repo, result=result, provider=provider, prompt=args.prompt)

    DEFAULT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "output_path": str(DEFAULT_REPORT),
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "completed" else 1


def create_provider(provider_name: str, *, dotenv_path: str):
    if provider_name == "deterministic":
        return DeterministicAppV22Provider()
    return create_appv22_provider_from_appv2_env(dotenv_path=dotenv_path)


def seed_repo(repo: Path) -> Path:
    if repo.exists():
        shutil.rmtree(repo)

    files = {
        "README.md": "# Probe Workspace\n",
        "src/app.py": "print('protected runtime file')\n",
        "tests/test_probe.py": "def test_probe():\n    assert True\n",
        "assets/logo.svg": "<svg></svg>\n",
        "secrets/prod.env": "TOKEN=protected\n",
        "docs/existing.md": "Already organized and protected by docs prefix.\n",
        "notes/team/standup.md": "Move this team note into docs.\n",
        "notes/team/keep_decisions.md": "Keep this note in place by protected name prefix.\n",
        "projects/alpha/spec.md": "First spec with colliding basename.\n",
        "projects/beta/spec.md": "Second spec should be held because docs/spec.md is claimed.\n",
        "tmp/session/run.log": "Move this run log into artifacts/logs.\n",
        "tmp/other/run.log": "Hold this log because artifacts/logs/run.log is claimed.\n",
        "tmp/session/trace.json": "{\"move\": true}\n",
        "tmp/session/keep_trace.json": "{\"keep\": true}\n",
    }
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    events = result.get("events", [])
    event_order = [str(event.get("event_type", "")) for event in events if isinstance(event, dict)]
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {
            "events": len(events),
            "decisions": _count_events(event_order, "DecisionProposed"),
            "tool_calls": _count_events(event_order, "ToolCallCompleted", "ToolCallDenied"),
            "mutation_receipts": _count_events(event_order, "MutationApplied"),
            "verification_receipts": _count_events(event_order, "VerificationRecorded"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "files": _file_list(repo),
    }


def _count_events(event_order: list[str], *event_types: str) -> int:
    return sum(1 for event_type in event_order if event_type in event_types)


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(getattr(provider, "provider_id", type(provider).__name__))


def _costs(provider: Any) -> dict[str, Any]:
    for candidate in (provider, getattr(provider, "client", None), getattr(provider, "delegate", None)):
        usage_snapshot = getattr(candidate, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot()
            if isinstance(snapshot, dict):
                return snapshot
    delegate_client = getattr(getattr(provider, "delegate", None), "client", None)
    usage_snapshot = getattr(delegate_client, "usage_snapshot", None)
    if callable(usage_snapshot):
        snapshot = usage_snapshot()
        if isinstance(snapshot, dict):
            return snapshot
    return {"model_calls": 0, "total_tokens": 0, "cost": 0.0}


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
