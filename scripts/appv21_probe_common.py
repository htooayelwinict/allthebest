"""Shared helpers for AppV2.1 probe scripts."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.1"))


def seed_repo(name: str) -> Path:
    repo = ROOT / name
    if repo.exists():
        shutil.rmtree(repo)
    files = {
        "README.md": "# Probe repo\n",
        "notes/drafts/task_notes.md": "Task notes\n",
        "artifacts/tmp/old_build.log": "old build\n",
        "notes/raw/old_blob.txt": "keep\n",
    }
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    events = result.get("events", [])
    event_order = [event["event_type"] for event in events]
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "event_order": event_order,
        "decision_count": event_order.count("DecisionProposed"),
        "tool_count": event_order.count("ToolCallCompleted"),
        "denied_count": event_order.count("ToolCallDenied"),
        "pause_count": event_order.count("RunPaused"),
        "compaction_count": event_order.count("ContextCompacted"),
        "raw": result,
    }


def write_report(name: str, result: dict[str, Any]) -> Path:
    out = ROOT / "plan" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summarize(result), indent=2, sort_keys=True), encoding="utf-8")
    print(f"OUTPUT_PATH={out}")
    print(json.dumps({key: summarize(result)[key] for key in ["status", "decision_count", "denied_count", "pause_count"]}, sort_keys=True))
    return out
