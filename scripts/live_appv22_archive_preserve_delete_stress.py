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
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services

DEFAULT_REPORT_DIR = ROOT / "plan"
DEFAULT_REPO = ROOT / "live_appv22_archive_preserve_delete_stress_repo.appv2-env"
SENTINEL_NAME = ".appv22-archive-preserve-delete-stress"
SENTINEL_VALUE = "owned-by-live-appv22-archive-preserve-delete-stress\n"
DEFAULT_PROMPT = (
    "Prepare a client handoff package from this workspace. Preserve the client brief template as a copy "
    "inside client-pack, move the release notes into archive, remove obvious junk, and keep a record."
)
SEED_FILES: dict[str, str] = {}
PROTECTED_PATHS = (
    "README.md",
    "secrets/api.key",
    "assets/logo.svg",
    "tmp/keep.log",
)
EXPECTED_PRESENT = (
    "archive/release-notes.md",
    "client-pack/client-brief.md",
    "templates/client-brief.md",
    "docs/workspace_manifest.json",
)
EXPECTED_ABSENT = (
    "docs/release-notes.md",
    "tmp/junk.log",
)
REQUIRED_MANIFEST_TERMS = (
    "docs/release-notes.md",
    "archive/release-notes.md",
    "templates/client-brief.md",
    "client-pack/client-brief.md",
    "tmp/junk.log",
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--run-timeout-seconds", type=int, default=240)
    parser.add_argument("--worker-timeout", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=29)
    args = parser.parse_args()

    configure_llm_env(args)
    repo = seed_repo(args.repo)
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
            result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(repo=repo, before_files=before_files, result=result, provider=provider, prompt=args.prompt)
    output_path = args.output or DEFAULT_REPORT_DIR / "live-appv22-archive-preserve-delete-stress.appv2-env.json"
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
                "archive_preserve_delete": report["archive_preserve_delete"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    return 0 if report["archive_preserve_delete"]["passed"] else 1


def configure_llm_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = str(args.temperature)
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def seed_repo(repo: Path) -> Path:
    if repo.exists():
        sentinel = repo / SENTINEL_NAME
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != SENTINEL_VALUE:
            raise RuntimeError(f"refusing to delete non-probe-owned directory: {repo}")
        shutil.rmtree(repo)

    files = {
        SENTINEL_NAME: SENTINEL_VALUE,
        "README.md": "# Archive Preserve Delete Stress Workspace\n",
        "docs/release-notes.md": "Move this release note into archive/release-notes.md for the client package.\n",
        "templates/client-brief.md": "Preserve this original template and copy it into client-pack/client-brief.md.\n",
        "tmp/junk.log": "delete this obvious junk log\n",
        "tmp/keep.log": "keep this operational log in place\n",
        "docs/existing.md": "Already organized; leave untouched.\n",
        "secrets/api.key": "API_KEY=protected-secret\n",
        "assets/logo.svg": "<svg></svg>\n",
    }
    SEED_FILES.clear()
    SEED_FILES.update(files)
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, before_files: list[str], result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    events = [event for event in result.get("events", []) if isinstance(event, dict)]
    event_order = [str(event.get("event_type", "")) for event in events]
    files_after = _file_list(repo)
    matrix = _archive_preserve_delete_matrix(repo, events)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {
            "events": len(events),
            "decisions": _count_events(event_order, "DecisionProposed"),
            "tool_calls": _count_events(event_order, "ToolCallCompleted", "ToolCallDenied"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "tool_matrix": _tool_matrix(events),
        "files": {"before": before_files, "after": files_after, "created": sorted(set(files_after) - set(before_files))},
        "archive_preserve_delete": matrix,
    }


def _archive_preserve_delete_matrix(repo: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    protected_preserved = {path: (repo / path).is_file() and (repo / path).read_text(encoding="utf-8") == SEED_FILES[path] for path in PROTECTED_PATHS}
    expected_present = {path: (repo / path).is_file() for path in EXPECTED_PRESENT}
    expected_absent = {path: not (repo / path).exists() for path in EXPECTED_ABSENT}
    manifest_text = (repo / "docs/workspace_manifest.json").read_text(encoding="utf-8", errors="replace") if (repo / "docs/workspace_manifest.json").is_file() else ""
    manifest_terms = {term: term in manifest_text for term in REQUIRED_MANIFEST_TERMS}
    tool_counts: dict[str, int] = {}
    denied_errors: list[str] = []
    for row in _tool_matrix(events):
        tool_id = str(row.get("tool_id") or "")
        tool_counts[tool_id] = tool_counts.get(tool_id, 0) + 1
        if row.get("event_type") == "ToolCallDenied":
            denied_errors.extend(str(error) for error in row.get("errors", []))
    violations: list[str] = []
    for path, ok in protected_preserved.items():
        if not ok:
            violations.append(f"protected path changed or missing: {path}")
    for path, ok in expected_present.items():
        if not ok:
            violations.append(f"expected path missing: {path}")
    for path, ok in expected_absent.items():
        if not ok:
            violations.append(f"expected path still present: {path}")
    for term, ok in manifest_terms.items():
        if not ok:
            violations.append(f"manifest missing term: {term}")
    if tool_counts.get("file_management.copy_file", 0) < 1:
        violations.append("copy_file was not used")
    if tool_counts.get("file_management.move_file", 0) < 1:
        violations.append("move_file was not used")
    if tool_counts.get("file_management.delete_file", 0) < 1:
        violations.append("delete_file was not used")
    return {
        "passed": not violations and bool(events),
        "protected_preserved": protected_preserved,
        "expected_present": expected_present,
        "expected_absent": expected_absent,
        "manifest_terms": manifest_terms,
        "tool_counts": tool_counts,
        "denied_errors": denied_errors,
        "violations": violations,
    }


def _tool_matrix(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = event.get("event_type")
        if event_type not in {"ToolCallCompleted", "ToolCallDenied"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        rows.append(
            {
                "index": index,
                "event_type": event_type,
                "tool_id": payload.get("tool_id"),
                "status": payload.get("status"),
                "arguments": payload.get("arguments"),
                "errors": (payload.get("payload") or {}).get("errors", []) if isinstance(payload.get("payload"), dict) else [],
            }
        )
    return rows


def _count_events(event_order: list[str], *event_types: str) -> int:
    return sum(1 for event_type in event_order if event_type in event_types)


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(getattr(provider, "provider_id", type(provider).__name__))


def _costs(provider: Any) -> dict[str, Any]:
    candidates = [
        ("provider.usage_snapshot", provider),
        ("client.usage_snapshot", getattr(provider, "client", None)),
        ("delegate.usage_snapshot", getattr(provider, "delegate", None)),
        ("delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None)),
    ]
    for source, candidate in candidates:
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


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
