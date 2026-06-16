from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import signal
import shutil
import sys
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.context.budget import estimate_chars
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from scripts.live_appv22_vague_file_creation_probe import (    build_report as build_creation_report,
)

DEFAULT_REPORT_DIR = ROOT / "plan"
DEFAULT_REPO = ROOT / "live_appv22_long_context_fact_stress_repo.appv2-env"
SENTINEL_NAME = ".appv22-long-context-fact-stress"
SENTINEL_VALUE = "owned-by-live-appv22-long-context-fact-stress\n"
DEFAULT_PROMPT = (
    "The handoff facts are buried in noisy workspace notes. Make one concise handoff file for the next operator. "
    "Preserve exact codes, owners, dates, and the escalation rule. Pick a sensible new filename."
)
REQUIRED_FACTS = (
    "ORCHID-77-BRIDGE",
    "NEON-42-FERRY",
    "Mira Chen",
    "Pavel Ortiz",
    "2026-06-18 09:30",
    "2026-06-19 15:00",
    "Escalate to FinchOps only after two failed badge syncs",
)
SEED_FILES: dict[str, str] = {}


class RecordingCompressor(AgentContextCompressor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.records: list[dict[str, Any]] = []

    def compress(self, messages, *, previous_summary):
        before = estimate_chars(messages)
        compressed = super().compress(messages, previous_summary=previous_summary)
        after = estimate_chars(compressed)
        self.records.append(
            {
                "before_chars": before,
                "after_chars": after,
                "triggered": before > int(self.max_chars * self.threshold),
                "shrink_ratio": round(after / before, 4) if before else 1.0,
                "sections": [
                    message.get("section")
                    for message in compressed
                    if message.get("name") == "provider_context_section"
                ],
                "summary_messages": sum(1 for message in compressed if message.get("name") == "context_summary"),
            }
        )
        return compressed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--noise-files", type=int, default=260)
    parser.add_argument("--context-max-chars", type=int, default=3600)
    args = parser.parse_args()

    configure_llm_env(args)
    repo = seed_repo(args.repo, noise_files=args.noise_files)
    before_files = _file_list(repo)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_appv22_provider_from_appv2_env(
                dotenv_path=args.dotenv,
            )
            services = create_appv22_services(
                root_path=repo,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            services.gateway_guard = GatewayContextGuard(max_chars=80_000, threshold=1.0)
            compressor = RecordingCompressor(max_chars=args.context_max_chars, threshold=0.45)
            services.compressor = compressor
            result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(repo=repo, before_files=before_files, result=result, provider=provider, prompt=args.prompt)
    report["compaction_efficiency"] = _compaction_efficiency(getattr(locals().get("compressor", None), "records", []))
    output_path = args.output or DEFAULT_REPORT_DIR / "live-appv22-long-context-fact-stress.appv2-env.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "reason": report["reason"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "fact_stress": report["fact_stress"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    return 0 if report["fact_stress"]["passed"] else 1


def configure_llm_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = str(args.temperature)
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


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


def seed_repo(repo: Path, *, noise_files: int) -> Path:
    if repo.exists():
        sentinel = repo / SENTINEL_NAME
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != SENTINEL_VALUE:
            raise RuntimeError(f"refusing to delete non-probe-owned directory: {repo}")
        shutil.rmtree(repo)

    files = {
        SENTINEL_NAME: SENTINEL_VALUE,
        "README.md": "# Long Context Fact Stress\n\nNoisy workspace. Exact facts are in docs and notes.\n",
        "docs/operator-brief.md": (
            "Bridge code ORCHID-77-BRIDGE belongs to owner Mira Chen. "
            "First checkpoint is 2026-06-18 09:30. "
            "Escalate to FinchOps only after two failed badge syncs.\n"
        ),
        "notes/ferry-window.txt": (
            "Ferry code NEON-42-FERRY belongs to owner Pavel Ortiz. "
            "Final checkpoint is 2026-06-19 15:00. Keep exact spelling.\n"
        ),
        "meetings/noisy-summary.md": (
            "Several fake codes are obsolete: ORCHID-17-BRIDGE, NEON-24-FERRY. "
            "Do not use obsolete codes in the handoff.\n"
        ),
        "docs/escalation.md": (
            "Escalation rule: Escalate to FinchOps only after two failed badge syncs. "
            "Do not escalate after one failure.\n"
        ),
    }
    for index in range(noise_files):
        files[f"archive/noise/{index:03d}.md"] = (
            f"Noise item {index}: fake owner Alpha {index}, fake code ORCHID-{index:02d}-NOISE, "
            "ignore for current handoff.\n"
        )
    SEED_FILES.clear()
    SEED_FILES.update(files)
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, before_files: list[str], result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    report = build_creation_report(repo=repo, before_files=before_files, result=result, provider=provider, prompt=prompt)
    modified_seed_files = _modified_seed_files(repo)
    report["files"]["modified_seed_files"] = modified_seed_files
    report["file_creation"]["summary"]["modified_seed_files"] = modified_seed_files
    created_text = "\n".join(
        (repo / path).read_text(encoding="utf-8", errors="replace")
        for path in report["files"]["created"]
        if (repo / path).is_file() and path != SENTINEL_NAME
    )
    fact_hits = {fact: fact.lower() in created_text.lower() for fact in REQUIRED_FACTS}
    obsolete_leaks = {
        "ORCHID-17-BRIDGE": "ORCHID-17-BRIDGE" in created_text,
        "NEON-24-FERRY": "NEON-24-FERRY" in created_text,
    }
    event_order = report["event_order"]
    report["fact_stress"] = {
        "passed": (
            report["status"] == "completed"
            and bool(report["files"]["created"])
            and all(fact_hits.values())
            and not any(obsolete_leaks.values())
            and not modified_seed_files
        ),
        "required_fact_hits": fact_hits,
        "required_fact_hit_count": sum(1 for hit in fact_hits.values() if hit),
        "obsolete_leaks": obsolete_leaks,
        "context_summary_updates": event_order.count("ContextSummaryUpdated"),
        "repo_snapshot_tool_calls": sum(
            1 for row in report["tool_matrix"] if row.get("tool_id") == "file_management.repo_snapshot"
        ),
        "read_file_tool_calls": sum(
            1 for row in report["tool_matrix"] if row.get("tool_id") == "file_management.read_file"
        ),
        "write_tool_calls": sum(
            1 for row in report["tool_matrix"] if row.get("tool_id") == "file_management.write_file"
        ),
    }
    return report


def _compaction_efficiency(records: list[dict[str, Any]]) -> dict[str, Any]:
    triggered = [record for record in records if record.get("triggered")]
    shrink_ratios = [
        record.get("shrink_ratio")
        for record in triggered
        if isinstance(record.get("shrink_ratio"), int | float)
    ]
    return {
        "records": records,
        "calls": len(records),
        "triggered_calls": len(triggered),
        "max_before_chars": max((record.get("before_chars", 0) for record in records), default=0),
        "min_triggered_shrink_ratio": min(shrink_ratios) if shrink_ratios else None,
        "avg_triggered_shrink_ratio": round(sum(shrink_ratios) / len(shrink_ratios), 4) if shrink_ratios else None,
        "preserved_tool_definitions": any(
            "tool_definitions" in (record.get("sections") or [])
            for record in records
        ),
        "preserved_world": any("world" in (record.get("sections") or []) for record in records),
        "summary_messages": sum(int(record.get("summary_messages", 0)) for record in records),
    }


def _modified_seed_files(repo: Path) -> list[str]:
    modified: list[str] = []
    for relative, expected in SEED_FILES.items():
        path = repo / relative
        if path.is_file() and path.read_text(encoding="utf-8", errors="replace") != expected:
            modified.append(relative)
    return sorted(modified)


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
