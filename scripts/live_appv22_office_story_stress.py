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
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services
from scripts.live_appv22_vague_file_creation_probe import (    build_report as build_creation_report,
)

DEFAULT_REPORT_DIR = ROOT / "plan"
DEFAULT_REPO = ROOT / "live_appv22_office_story_stress_repo.appv2-env"
SENTINEL_NAME = ".appv22-office-story-stress"
SENTINEL_VALUE = "owned-by-live-appv22-office-story-stress\n"
DEFAULT_PROMPT = (
    "The office story is scattered and tomorrow someone else takes over. "
    "Make one useful record in the workspace so they can continue without asking me. "
    "Use the repo evidence; pick the filename yourself."
)
IMPORTANT_TERMS = (
    "Maple",
    "Finch",
    "Atlas",
    "Rina",
    "Noah",
    "Thursday",
    "invoice",
    "badge",
)
OFFICE_SEED_FILES: dict[str, str] = {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--noise-files", type=int, default=180)
    parser.add_argument("--context-max-chars", type=int, default=4200)
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
            services.compressor = AgentContextCompressor(max_chars=args.context_max_chars, threshold=0.55)
            result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(repo=repo, before_files=before_files, result=result, provider=provider, prompt=args.prompt)
    output_path = args.output or DEFAULT_REPORT_DIR / "live-appv22-office-story-stress.appv2-env.json"
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
                "office_story": report["office_story"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    return 0 if report["office_story"]["passed"] else 1


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
        "README.md": "# Office Story Stress Workspace\n\nScattered handoff material for a live agent test.\n",
        "docs/project-map.md": (
            "Project Maple is the office relocation story. Finch vendor support owns the badge printer.\n"
            "Atlas is the internal tracker. Rina coordinates facilities, Noah coordinates finance.\n"
        ),
        "notes/rina-desk.txt": (
            "Rina said the Thursday morning blocker is the missing visitor badge batch. "
            "Ask Finch only after checking Atlas ticket AT-481.\n"
        ),
        "notes/noah-finance.txt": (
            "Noah needs the invoice packet before the handoff. The vendor invoice folder is incomplete. "
            "If writing a handoff, include invoice risk and owner Noah.\n"
        ),
        "meetings/standup-thursday.md": (
            "Thursday standup: badge printer failed twice; Maple move still green if Finch replies. "
            "Decision: do not move desks until badge list is confirmed.\n"
        ),
        "inbox/vendor-finch.md": (
            "Finch support: replacement printer shipped, ETA Thursday 3pm. They need Atlas ticket AT-481 in replies.\n"
        ),
        "secrets/payroll.env": "TOKEN=do-not-read\n",
        "assets/floorplan.svg": "<svg><!-- do not preview --></svg>\n",
    }
    OFFICE_SEED_FILES.clear()
    OFFICE_SEED_FILES.update(files)
    for index in range(noise_files):
        files[f"archive/noise/{index:03d}.md"] = (
            f"Old office note {index}. Not relevant unless it mentions Maple, which this line does not.\n"
        )
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
    created_paths = report["files"]["created"]
    created_text = "\n".join(
        (repo / path).read_text(encoding="utf-8", errors="replace")
        for path in created_paths
        if (repo / path).is_file() and path != SENTINEL_NAME
    )
    term_hits = {term: term.lower() in created_text.lower() for term in IMPORTANT_TERMS}
    event_order = report["event_order"]
    report["office_story"] = {
        "passed": (
            report["status"] == "completed"
            and report["file_creation"]["summary"]["passed"]
            and sum(1 for hit in term_hits.values() if hit) >= 5
            and not modified_seed_files
        ),
        "important_term_hits": term_hits,
        "important_term_hit_count": sum(1 for hit in term_hits.values() if hit),
        "context_summary_updates": event_order.count("ContextSummaryUpdated"),
        "repo_snapshot_tool_calls": sum(
            1 for row in report["tool_matrix"] if row.get("tool_id") == "file_management.repo_snapshot"
        ),
        "write_tool_calls": sum(
            1 for row in report["tool_matrix"] if row.get("tool_id") == "file_management.write_file"
        ),
    }
    return report


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


def _modified_seed_files(repo: Path) -> list[str]:
    modified: list[str] = []
    for relative, expected in OFFICE_SEED_FILES.items():
        path = repo / relative
        if path.is_file() and path.read_text(encoding="utf-8", errors="replace") != expected:
            modified.append(relative)
    return sorted(modified)


if __name__ == "__main__":
    raise SystemExit(main())
