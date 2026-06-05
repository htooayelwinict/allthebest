"""Run a live decompressor -> planner -> worker probe against a mock repo."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import traceback
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime
from app.runtime_matrix import RuntimeMatrixLogger
from app.worker_kernel.runtime import WorkerKernelRuntime


DEFAULT_PROMPT = (
    "In the repo rooted at live_worker_mock_repo, inspect the payment retry bug. "
    "Read the code and tests, fix the issue so retries for the same order reuse the same idempotency key, "
    "keep the change minimal, and verify with the repo tests. Summarize what changed and any remaining risk."
)
WEBHOOK_FULFILLMENT_PROMPT = (
    "In the repo rooted at live_worker_webhook_repo, debug the fulfillment webhook idempotency failure. "
    "Read the README, source, and tests. Research current webhook/idempotency guidance if useful, then apply a "
    "minimal scoped code fix so duplicate delivery of the same event_id does not double-reserve inventory or send "
    "duplicate notifications. Preserve retries for previously failed events. Keep writes limited to the source files "
    "that actually need mutation, run the repo tests, and produce a concise risk/verification summary."
)
FILE_WORKSPACE_MANAGEMENT_PROMPT = (
    "In the repo rooted at live_worker_workspace_repo, clean up the workspace "
    "layout for a release prep. Move markdown notes from notes/drafts, notes/raw, "
    "reports, and tmp into docs while preserving file contents; move logs and json artifacts "
    "from artifacts/tmp and misc into artifacts/logs; create/update docs/workspace_manifest.json "
    "listing moved_documents, moved_logs, and moved_json_artifacts as file names, plus total_artifacts. "
    "Keep edits limited to file moves/creation and leave source code untouched. "
    "Run tests, then summarize exactly what you moved and any risk of data loss."
)
FILE_POLICY_ARCHIVE_PROMPT = (
    "In the repo rooted at live_worker_policy_archive_repo, I need you to prep a messy policy handoff "
    "workspace for an internal compliance review. Please inspect the README and current folders first. "
    "Move eligible markdown policy/client notes into records/policies, json evidence packets into "
    "records/evidence, log files into records/logs, and csv exports into records/exports. Leave any file "
    "marked hold, keep, or do_not_move exactly where it is, even if it looks like a document. Create or update "
    "records/archive_index.json with exact keys moved_documents, moved_evidence, moved_logs, moved_exports, "
    "held_items, and total_moved; list file names, not paths, in those arrays. Keep source code and tests "
    "untouched, avoid deleting content, run tests, and tell me what moved plus anything you intentionally left alone."
)
GREENFIELD_CALCULATOR_API_PROMPT = (
    "In the repo rooted at live_worker_greenfield_calculator_api, I need you to create a small calculator API "
    "from scratch that is ready to deploy. The repository is intentionally empty except for git metadata, so first "
    "inspect the workspace and choose the simplest production-friendly stack that is available locally. Create the "
    "API source code, tests, README, dependency or project metadata, and a Dockerfile or equivalent deploy notes. "
    "The API should support add, subtract, multiply, and divide operations, handle invalid input cleanly, include "
    "basic health/readiness behavior, and have focused tests that can run in this repo. Keep the implementation "
    "small, avoid unnecessary frameworks if the environment does not need them, verify with tests, and summarize "
    "how to run and deploy it."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=[
            "payment_retry",
            "webhook_fulfillment",
            "file_workspace_cleanup",
            "file_policy_archive_reorg",
            "greenfield_calculator_api",
        ],
        default="payment_retry",
    )
    parser.add_argument("--repo", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--worker-model", default="qwen/qwen3.7-max")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--out-dir", default="plan")
    parser.add_argument("--max-parallel-instances", default="3")
    parser.add_argument("--worker-timeout", default="90")
    parser.add_argument("--tool-timeout", default="20")
    parser.add_argument("--max-tokens", default="2400")
    parser.add_argument("--matrix-poll-interval", type=float, default=1.0)
    parser.add_argument("--web-search-provider", default="brave")
    parser.add_argument("--web-search-api-key", default="")
    parser.add_argument("--web-search-max-results", default="5")
    args = parser.parse_args()

    workspace = Path.cwd()
    scenario = _scenario_config(args.scenario)
    repo_name = args.repo or scenario["repo"]
    prompt = args.prompt or scenario["prompt"]
    repo_path = (workspace / repo_name).resolve()
    out_dir = (workspace / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"live-worker-{_slug(args.worker_model)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    _ensure_mock_repo(repo_path, scenario=args.scenario)
    _configure_worker_env(args)
    trace = RuntimeMatrixLogger()
    reporter = _LiveMatrixReporter(trace=trace, poll_interval_seconds=args.matrix_poll_interval)
    reporter.start()
    baseline: dict[str, object] = _probe_not_run_result("before")
    after: dict[str, object] = _probe_not_run_result("after")
    envelope = None
    plan = None
    result = None
    run_error: dict[str, object] | None = None

    try:
        try:
            baseline = _run_pytest(repo_path, "before")
            print(f"PHASE baseline_pytest returncode={baseline['returncode']}", flush=True)

            decompressor = DecompressorRuntime.from_env(args.dotenv)
            planner = PlannerRuntime.from_env(args.dotenv, fallback_on_error=False)
            worker = WorkerKernelRuntime.from_env(
                args.dotenv,
                planner_runtime=planner,
                root_path=str(repo_path),
                fallback_to_stub_workers=False,
            )

            print("PHASE decompressor start", flush=True)
            envelope = decompressor.run(prompt, trace=trace)
            print(f"PHASE decompressor done request_id={envelope.request_id}", flush=True)

            print("PHASE planner start", flush=True)
            plan = planner.run(envelope, trace=trace)
            print(f"PHASE planner done plan_id={plan.plan_id} steps={len(plan.steps)}", flush=True)

            print("PHASE worker start", flush=True)
            result = worker.run(plan, envelope=envelope, trace=trace)
            print(f"PHASE worker done status={result.status}", flush=True)

            after = _run_pytest(repo_path, "after")
            print(f"PHASE after_pytest returncode={after['returncode']}", flush=True)
        except Exception as exc:
            run_error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(f"PHASE probe_error type={type(exc).__name__} message={exc}", flush=True)
    finally:
        reporter.stop()
        reporter.join(timeout=2)
        reporter.flush_new_rows()

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "scenario": args.scenario,
        "prompt": prompt,
        "repo_path": str(repo_path),
        "worker_env": _worker_env_snapshot(),
        "runtime_matrix": trace.snapshot(),
        "baseline_pytest": baseline,
        "envelope": envelope.model_dump(mode="json") if envelope is not None else None,
        "plan": plan.model_dump(mode="json") if plan is not None else None,
        "result": result.model_dump(mode="json") if result is not None else None,
        "after_pytest": after,
        "git_status": _run(["git", "status", "--short"], cwd=repo_path),
        "final_files": _snapshot_repo_files(repo_path),
        "probe_error": run_error,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"OUTPUT_PATH={out_path}")
    print(
        json.dumps(
            {
                "baseline_returncode": baseline["returncode"],
                "result_status": result.status if result is not None else "probe_error",
                "after_returncode": after["returncode"],
                "step_count": len(plan.steps) if plan is not None else 0,
                "runtime_matrix_rows": trace.snapshot()["row_count"],
            },
            sort_keys=True,
        )
    )
    return 0


class _LiveMatrixReporter(threading.Thread):
    def __init__(self, *, trace: RuntimeMatrixLogger, poll_interval_seconds: float) -> None:
        super().__init__(daemon=True)
        self._trace = trace
        self._poll_interval_seconds = max(0.2, poll_interval_seconds)
        self._stop_event = threading.Event()
        self._last_seq = 0

    def stop(self) -> None:
        self._stop_event.set()

    def flush_new_rows(self) -> None:
        snapshot = self._trace.snapshot()
        for row in snapshot["rows"]:
            seq = int(row.get("seq") or 0)
            if seq <= self._last_seq:
                continue
            print(_format_matrix_row(row), flush=True)
            self._last_seq = seq

    def run(self) -> None:
        while not self._stop_event.wait(self._poll_interval_seconds):
            self.flush_new_rows()


def _format_matrix_row(row: dict[str, object]) -> str:
    details = row.get("details")
    details_text = ""
    if isinstance(details, dict) and details:
        rendered = json.dumps(details, sort_keys=True)
        if len(rendered) > 220:
            rendered = f"{rendered[:217]}..."
        details_text = f" details={rendered}"

    fields = [
        f"seq={row.get('seq')}",
        f"component={row.get('component')}",
        f"stage={row.get('stage')}",
        f"event={row.get('event')}",
        f"status={row.get('status')}",
    ]
    for key in ("request_id", "plan_id", "run_id", "step_id", "attempt_id", "worker_type"):
        value = row.get(key)
        if value:
            fields.append(f"{key}={value}")
    return "MATRIX " + " ".join(fields) + details_text


def _configure_worker_env(args: argparse.Namespace) -> None:
    os.environ["WORKER_LLM_ENABLED"] = "true"
    os.environ["WORKER_LLM_MODEL"] = args.worker_model
    os.environ["WORKER_LLM_PROVIDER_SORT"] = "latency"
    os.environ["WORKER_LLM_TIMEOUT_SECONDS"] = args.worker_timeout
    os.environ["WORKER_LLM_TEMPERATURE"] = "0"
    os.environ["WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["WORKER_LLM_MAX_TOKENS"] = args.max_tokens
    os.environ["WORKER_MAX_PARALLEL_INSTANCES"] = args.max_parallel_instances
    os.environ["WORKER_TOOL_TIMEOUT_SECONDS"] = args.tool_timeout
    os.environ["WORKER_MAX_FILE_BYTES"] = "200000"
    os.environ["WORKER_WEB_SEARCH_PROVIDER"] = args.web_search_provider
    if args.web_search_api_key:
        os.environ["WORKER_WEB_SEARCH_API_KEY"] = args.web_search_api_key
    os.environ["WORKER_WEB_SEARCH_MAX_RESULTS"] = args.web_search_max_results


def _worker_env_snapshot() -> dict[str, str | None]:
    keys = [
        "WORKER_LLM_ENABLED",
        "WORKER_LLM_MODEL",
        "WORKER_LLM_PROVIDER_SORT",
        "WORKER_LLM_TIMEOUT_SECONDS",
        "WORKER_LLM_MAX_TOKENS",
        "WORKER_MAX_PARALLEL_INSTANCES",
        "WORKER_TOOL_TIMEOUT_SECONDS",
        "WORKER_WEB_SEARCH_PROVIDER",
        "WORKER_WEB_SEARCH_API_KEY",
        "WORKER_WEB_SEARCH_MAX_RESULTS",
    ]
    snapshot = {key: os.environ.get(key) for key in keys}
    if snapshot.get("WORKER_WEB_SEARCH_API_KEY"):
        snapshot["WORKER_WEB_SEARCH_API_KEY"] = "***redacted***"
    return snapshot


def _scenario_config(scenario: str) -> dict[str, str]:
    if scenario == "webhook_fulfillment":
        return {"repo": "live_worker_webhook_repo", "prompt": WEBHOOK_FULFILLMENT_PROMPT}
    if scenario == "file_workspace_cleanup":
        return {
            "repo": "live_worker_workspace_repo",
            "prompt": FILE_WORKSPACE_MANAGEMENT_PROMPT,
        }
    if scenario == "file_policy_archive_reorg":
        return {
            "repo": "live_worker_policy_archive_repo",
            "prompt": FILE_POLICY_ARCHIVE_PROMPT,
        }
    if scenario == "greenfield_calculator_api":
        return {
            "repo": "live_worker_greenfield_calculator_api",
            "prompt": GREENFIELD_CALCULATOR_API_PROMPT,
        }
    return {"repo": "live_worker_mock_repo", "prompt": DEFAULT_PROMPT}


def _ensure_mock_repo(repo_path: Path, *, scenario: str) -> None:
    if scenario == "webhook_fulfillment":
        _ensure_webhook_fulfillment_repo(repo_path)
        return
    if scenario == "file_workspace_cleanup":
        _ensure_file_workspace_repo(repo_path)
        return
    if scenario == "file_policy_archive_reorg":
        _ensure_policy_archive_repo(repo_path)
        return
    if scenario == "greenfield_calculator_api":
        _ensure_greenfield_repo(repo_path)
        return

    _reset_seeded_probe_repo(repo_path)
    (repo_path / "src").mkdir(parents=True, exist_ok=True)
    (repo_path / "tests").mkdir(parents=True, exist_ok=True)
    _remove_generated_runtime_dirs(repo_path)
    (repo_path / "README.md").write_text(
        "# Live Worker Mock Repo\n\nThis mock repo simulates a payment retry idempotency bug.\n",
        encoding="utf-8",
    )
    (repo_path / "src" / "__init__.py").write_text(
        '"""Mock payment package for live worker testing."""\n',
        encoding="utf-8",
    )
    (repo_path / "src" / "checkout.py").write_text(
        '''"""Simple payment retry helpers for worker-runtime live testing."""

from __future__ import annotations


def build_charge_headers(order_id: str, retry_count: int) -> dict[str, str]:
    """Return headers for a payment charge request.

    Current behavior is intentionally wrong for retries: the idempotency key
    changes when `retry_count` changes, which makes retries look like fresh
    charges to an upstream processor.
    """

    return {
        "Idempotency-Key": f"charge:{order_id}:retry:{retry_count}",
        "X-Retry-Count": str(retry_count),
    }
''',
        encoding="utf-8",
    )
    (repo_path / "tests" / "test_checkout.py").write_text(
        '''from src.checkout import build_charge_headers


def test_first_attempt_keeps_retry_count_header() -> None:
    headers = build_charge_headers("order-123", 0)
    assert headers["X-Retry-Count"] == "0"


def test_retries_reuse_same_idempotency_key() -> None:
    first = build_charge_headers("order-123", 0)
    retry = build_charge_headers("order-123", 1)

    assert retry["Idempotency-Key"] == first["Idempotency-Key"]
''',
        encoding="utf-8",
    )
    _ensure_probe_git_baseline(repo_path)


def _ensure_greenfield_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    real_files = [
        path
        for path in repo_path.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and not str(path.relative_to(repo_path)).startswith(".pytest_cache/")
        and "__pycache__" not in path.parts
    ]
    if real_files:
        examples = ", ".join(str(path.relative_to(repo_path)) for path in real_files[:5])
        raise SystemExit(
            f"greenfield scenario requires an empty repo target; {repo_path} already has files: {examples}. "
            "Use --repo with a fresh directory name."
        )
    _remove_generated_runtime_dirs(repo_path)
    if not (repo_path / ".git").exists():
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)


def _ensure_webhook_fulfillment_repo(repo_path: Path) -> None:
    _reset_seeded_probe_repo(repo_path)
    (repo_path / "src" / "fulfillment").mkdir(parents=True, exist_ok=True)
    (repo_path / "tests").mkdir(parents=True, exist_ok=True)
    _remove_generated_runtime_dirs(repo_path)
    (repo_path / "README.md").write_text(
        """# Fulfillment Webhook Service

This mock service receives payment-provider webhook events after checkout. A `charge.succeeded`
event reserves inventory and sends a fulfillment notification. Providers may redeliver the same
event, so `event_id` must be idempotent: the same event can be retried safely, but it must not
reserve inventory or notify fulfillment twice.

Important behavior:

- Duplicate delivery of the same successful event must return `duplicate_ignored`.
- A new event with a different `event_id` may reserve inventory normally.
- If an event fails before side effects complete, it should not be marked processed.
- Keep fixes small and prefer changing `src/fulfillment/events.py` only unless evidence proves otherwise.
""",
        encoding="utf-8",
    )
    (repo_path / "src" / "fulfillment" / "__init__.py").write_text(
        '"""Fulfillment webhook mock package."""\n',
        encoding="utf-8",
    )
    (repo_path / "src" / "__init__.py").write_text(
        '"""Source package for fulfillment webhook tests."""\n',
        encoding="utf-8",
    )
    (repo_path / "src" / "fulfillment" / "inventory.py").write_text(
        '''"""Inventory ledger for fulfillment webhook tests."""

from __future__ import annotations


class InventoryLedger:
    def __init__(self, stock: dict[str, int]) -> None:
        self.stock = dict(stock)
        self.audit_log: list[dict[str, object]] = []

    def reserve(self, *, sku: str, quantity: int, event_id: str) -> None:
        if sku not in self.stock:
            raise KeyError(f"unknown sku: {sku}")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.stock[sku] < quantity:
            raise ValueError(f"insufficient stock for {sku}")
        self.stock[sku] -= quantity
        self.audit_log.append({"event_id": event_id, "sku": sku, "quantity": quantity})
''',
        encoding="utf-8",
    )
    (repo_path / "src" / "fulfillment" / "notifications.py").write_text(
        '''"""Notification sink for fulfillment webhook tests."""

from __future__ import annotations


class NotificationSink:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_reserved(self, *, order_id: str, event_id: str, sku: str, quantity: int) -> None:
        self.messages.append(
            {
                "kind": "inventory_reserved",
                "order_id": order_id,
                "event_id": event_id,
                "sku": sku,
                "quantity": quantity,
            }
        )
''',
        encoding="utf-8",
    )
    (repo_path / "src" / "fulfillment" / "events.py").write_text(
        '''"""Webhook event processing for fulfillment."""

from __future__ import annotations

from typing import Any

from .inventory import InventoryLedger
from .notifications import NotificationSink


def process_webhook_event(
    event: dict[str, Any],
    *,
    ledger: InventoryLedger,
    notifier: NotificationSink,
    processed_events: set[str],
) -> str:
    """Process a payment-provider webhook event.

    BUG: duplicate event IDs are not checked before side effects, so provider
    redelivery can reserve inventory and notify fulfillment more than once.
    """

    event_id = str(event["event_id"])
    event_type = str(event["type"])
    if event_type != "charge.succeeded":
        return "ignored"

    payload = event["data"]
    sku = str(payload["sku"])
    quantity = int(payload["quantity"])
    order_id = str(payload["order_id"])

    ledger.reserve(sku=sku, quantity=quantity, event_id=event_id)
    notifier.send_reserved(order_id=order_id, event_id=event_id, sku=sku, quantity=quantity)
    processed_events.add(event_id)
    return "reserved"
''',
        encoding="utf-8",
    )
    (repo_path / "tests" / "test_webhook_idempotency.py").write_text(
        '''from src.fulfillment.events import process_webhook_event
from src.fulfillment.inventory import InventoryLedger
from src.fulfillment.notifications import NotificationSink


def _event(event_id: str, *, sku: str = "sku-chair", quantity: int = 2) -> dict:
    return {
        "event_id": event_id,
        "type": "charge.succeeded",
        "data": {
            "order_id": "order-1001",
            "sku": sku,
            "quantity": quantity,
        },
    }


def test_duplicate_webhook_delivery_is_idempotent() -> None:
    ledger = InventoryLedger({"sku-chair": 5})
    notifier = NotificationSink()
    processed_events: set[str] = set()

    first = process_webhook_event(_event("evt-1"), ledger=ledger, notifier=notifier, processed_events=processed_events)
    duplicate = process_webhook_event(_event("evt-1"), ledger=ledger, notifier=notifier, processed_events=processed_events)

    assert first == "reserved"
    assert duplicate == "duplicate_ignored"
    assert ledger.stock["sku-chair"] == 3
    assert len(ledger.audit_log) == 1
    assert len(notifier.messages) == 1
    assert processed_events == {"evt-1"}


def test_distinct_events_still_reserve_inventory() -> None:
    ledger = InventoryLedger({"sku-chair": 5})
    notifier = NotificationSink()
    processed_events: set[str] = set()

    process_webhook_event(_event("evt-1"), ledger=ledger, notifier=notifier, processed_events=processed_events)
    process_webhook_event(_event("evt-2"), ledger=ledger, notifier=notifier, processed_events=processed_events)

    assert ledger.stock["sku-chair"] == 1
    assert len(ledger.audit_log) == 2
    assert len(notifier.messages) == 2
    assert processed_events == {"evt-1", "evt-2"}


def test_failed_event_is_not_marked_processed_before_retry() -> None:
    ledger = InventoryLedger({"sku-chair": 5})
    notifier = NotificationSink()
    processed_events: set[str] = set()

    try:
        process_webhook_event(_event("evt-missing", sku="sku-missing"), ledger=ledger, notifier=notifier, processed_events=processed_events)
    except KeyError:
        pass

    assert processed_events == set()
    assert notifier.messages == []
    assert ledger.stock["sku-chair"] == 5
''',
        encoding="utf-8",
    )
    _ensure_probe_git_baseline(repo_path)


def _ensure_file_workspace_repo(repo_path: Path) -> None:
    _reset_seeded_probe_repo(repo_path)
    _remove_generated_runtime_dirs(repo_path)

    (repo_path / "notes" / "drafts").mkdir(parents=True, exist_ok=True)
    (repo_path / "notes" / "raw").mkdir(parents=True, exist_ok=True)
    (repo_path / "reports").mkdir(parents=True, exist_ok=True)
    (repo_path / "tmp").mkdir(parents=True, exist_ok=True)
    (repo_path / "artifacts" / "tmp").mkdir(parents=True, exist_ok=True)
    (repo_path / "artifacts" / "logs").mkdir(parents=True, exist_ok=True)
    (repo_path / "docs").mkdir(parents=True, exist_ok=True)
    (repo_path / "tests").mkdir(parents=True, exist_ok=True)

    (repo_path / "README.md").write_text(
        "# Live Worker Workspace Repo\n\nSeed repo for file-management probe tasks.\n",
        encoding="utf-8",
    )
    (repo_path / "notes" / "drafts" / "task_notes.md").write_text(
        "# Task Notes\n\nDraft notes for cleanup tasks.\n",
        encoding="utf-8",
    )
    (repo_path / "notes" / "raw" / "plan_notes.md").write_text(
        "# Plan Notes\n\nUnorganized notes and planning context.\n",
        encoding="utf-8",
    )
    (repo_path / "reports" / "q1_summary.md").write_text(
        "# Q1 Report\n\nA report that belongs under docs for the workspace release.\n",
        encoding="utf-8",
    )
    (repo_path / "tmp" / "tmp_report.md").write_text(
        "# Temporary Report\n\nThis file should be moved into docs.\n",
        encoding="utf-8",
    )
    (repo_path / "artifacts" / "tmp" / "old_build.log").write_text(
        "2026-06-04 build step: ok\n",
        encoding="utf-8",
    )
    (repo_path / "artifacts" / "tmp" / "error_dump.json").write_text(
        '[{"component":"worker","event":"failure","code":"E0001"}]\n',
        encoding="utf-8",
    )
    (repo_path / "notes" / "raw" / "old_blob.txt").write_text(
        "not a markdown artifact; keep as-is\n",
        encoding="utf-8",
    )

    (repo_path / "tests" / "test_workspace_cleanup.py").write_text(
        '''from pathlib import Path
import json


def test_workspace_files_are_cleaned_up() -> None:
    base = Path(__file__).resolve().parent.parent

    assert (base / "docs" / "task_notes.md").exists()
    assert (base / "docs" / "plan_notes.md").exists()
    assert (base / "docs" / "q1_summary.md").exists()
    assert (base / "docs" / "tmp_report.md").exists()

    assert not (base / "notes" / "drafts" / "task_notes.md").exists()
    assert not (base / "notes" / "raw" / "plan_notes.md").exists()
    assert not (base / "reports" / "q1_summary.md").exists()
    assert not (base / "tmp" / "tmp_report.md").exists()

    assert (base / "artifacts" / "logs" / "old_build.log").exists()
    assert (base / "artifacts" / "logs" / "error_dump.json").exists()
    assert not (base / "artifacts" / "tmp" / "old_build.log").exists()
    assert not (base / "artifacts" / "tmp" / "error_dump.json").exists()


def test_workspace_manifest_lists_moved_artifacts() -> None:
    manifest_path = Path(__file__).resolve().parent.parent / "docs" / "workspace_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    moved_documents = payload.get("moved_documents")
    moved_logs = payload.get("moved_logs")
    moved_json = payload.get("moved_json_artifacts")

    assert isinstance(moved_documents, list)
    assert isinstance(moved_logs, list)
    assert isinstance(moved_json, list)

    assert "task_notes.md" in moved_documents
    assert "plan_notes.md" in moved_documents
    assert "q1_summary.md" in moved_documents
    assert "tmp_report.md" in moved_documents

    assert "old_build.log" in moved_logs
    assert "error_dump.json" in moved_json

    assert payload.get("total_artifacts", 0) >= 6
'''
        ,
        encoding="utf-8",
    )
    _ensure_probe_git_baseline(repo_path)


def _ensure_policy_archive_repo(repo_path: Path) -> None:
    _reset_seeded_probe_repo(repo_path)
    _remove_generated_runtime_dirs(repo_path)

    for directory in (
        "incoming/client_notes",
        "incoming/policies",
        "incoming/evidence",
        "exports",
        "logs/raw",
        "records/policies",
        "records/evidence",
        "records/logs",
        "records/exports",
        "src",
        "tests",
    ):
        (repo_path / directory).mkdir(parents=True, exist_ok=True)

    (repo_path / "README.md").write_text(
        """# Policy Archive Handoff Probe

This repo simulates a messy internal compliance handoff workspace. The handoff
rules are intentionally specific:

- Move eligible markdown policy/client notes into `records/policies/`.
- Move JSON evidence packets into `records/evidence/`.
- Move `.log` files into `records/logs/`.
- Move CSV exports into `records/exports/`.
- Leave any file marked `hold`, `keep`, or `do_not_move` in place.
- `records/archive_index.json` must contain exact keys:
  `moved_documents`, `moved_evidence`, `moved_logs`, `moved_exports`,
  `held_items`, and `total_moved`.
- Archive index arrays contain file names only, not paths.
- Source code and tests are not part of the archive cleanup.
""",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "client_notes" / "client_alpha_notes.md").write_text(
        "# Client Alpha Notes\n\nReady for compliance handoff.\n",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "client_notes" / "client_beta_followup.md").write_text(
        "# Client Beta Followup\n\nReady for compliance handoff.\n",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "client_notes" / "client_gamma_hold.md").write_text(
        "# Client Gamma Notes\n\nhold: true\nReason: legal review pending.\n",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "policies" / "retention_policy.md").write_text(
        "# Retention Policy\n\nApproved policy notes.\n",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "policies" / "draft_policy_do_not_move.md").write_text(
        "# Draft Policy\n\ndo_not_move: pending owner approval.\n",
        encoding="utf-8",
    )
    (repo_path / "incoming" / "evidence" / "access_review.json").write_text(
        '{"kind":"access_review","status":"ready"}\n',
        encoding="utf-8",
    )
    (repo_path / "incoming" / "evidence" / "vendor_exception.json").write_text(
        '{"kind":"vendor_exception","status":"ready"}\n',
        encoding="utf-8",
    )
    (repo_path / "incoming" / "evidence" / "keep_local.json").write_text(
        '{"keep": true, "reason": "contains draft annotations"}\n',
        encoding="utf-8",
    )
    (repo_path / "logs" / "raw" / "audit_2026_06.log").write_text(
        "2026-06-05 policy handoff audit event\n",
        encoding="utf-8",
    )
    (repo_path / "logs" / "raw" / "debug_keep.log").write_text(
        "keep: local debug scratch\n",
        encoding="utf-8",
    )
    (repo_path / "exports" / "policy_matrix.csv").write_text(
        "policy,status\nretention,approved\n",
        encoding="utf-8",
    )
    (repo_path / "exports" / "owner_map.csv").write_text(
        "owner,policy\ncompliance,retention\n",
        encoding="utf-8",
    )
    (repo_path / "src" / "__init__.py").write_text(
        '"""Source package that must stay untouched by file archive probes."""\n',
        encoding="utf-8",
    )
    (repo_path / "src" / "archive_guard.py").write_text(
        '''"""Tiny guard module for probe tests."""

SOURCE_CODE_SENTINEL = "do-not-edit-source"
''',
        encoding="utf-8",
    )
    (repo_path / "tests" / "test_policy_archive.py").write_text(
        '''from pathlib import Path
import json


BASE = Path(__file__).resolve().parent.parent


def test_policy_archive_moves_eligible_files_only() -> None:
    expected_policy_docs = {
        "client_alpha_notes.md",
        "client_beta_followup.md",
        "retention_policy.md",
    }
    for name in expected_policy_docs:
        assert (BASE / "records" / "policies" / name).exists()

    assert (BASE / "records" / "evidence" / "access_review.json").exists()
    assert (BASE / "records" / "evidence" / "vendor_exception.json").exists()
    assert (BASE / "records" / "logs" / "audit_2026_06.log").exists()
    assert (BASE / "records" / "exports" / "policy_matrix.csv").exists()
    assert (BASE / "records" / "exports" / "owner_map.csv").exists()

    assert not (BASE / "incoming" / "client_notes" / "client_alpha_notes.md").exists()
    assert not (BASE / "incoming" / "client_notes" / "client_beta_followup.md").exists()
    assert not (BASE / "incoming" / "policies" / "retention_policy.md").exists()
    assert not (BASE / "incoming" / "evidence" / "access_review.json").exists()
    assert not (BASE / "logs" / "raw" / "audit_2026_06.log").exists()
    assert not (BASE / "exports" / "policy_matrix.csv").exists()


def test_policy_archive_preserves_hold_and_keep_items() -> None:
    assert (BASE / "incoming" / "client_notes" / "client_gamma_hold.md").exists()
    assert (BASE / "incoming" / "policies" / "draft_policy_do_not_move.md").exists()
    assert (BASE / "incoming" / "evidence" / "keep_local.json").exists()
    assert (BASE / "logs" / "raw" / "debug_keep.log").exists()
    assert 'SOURCE_CODE_SENTINEL = "do-not-edit-source"' in (
        BASE / "src" / "archive_guard.py"
    ).read_text(encoding="utf-8")


def test_archive_index_schema_and_counts() -> None:
    payload = json.loads((BASE / "records" / "archive_index.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "moved_documents",
        "moved_evidence",
        "moved_logs",
        "moved_exports",
        "held_items",
        "total_moved",
    }
    assert sorted(payload["moved_documents"]) == [
        "client_alpha_notes.md",
        "client_beta_followup.md",
        "retention_policy.md",
    ]
    assert sorted(payload["moved_evidence"]) == ["access_review.json", "vendor_exception.json"]
    assert payload["moved_logs"] == ["audit_2026_06.log"]
    assert sorted(payload["moved_exports"]) == ["owner_map.csv", "policy_matrix.csv"]
    assert sorted(payload["held_items"]) == [
        "client_gamma_hold.md",
        "debug_keep.log",
        "draft_policy_do_not_move.md",
        "keep_local.json",
    ]
    assert payload["total_moved"] == 8
    for values in payload.values():
        if isinstance(values, list):
            assert all("/" not in value for value in values)
'''
        ,
        encoding="utf-8",
    )
    _ensure_probe_git_baseline(repo_path)


def _reset_seeded_probe_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    children = list(repo_path.iterdir())
    if not children:
        return
    if not repo_path.name.startswith("live_worker_"):
        raise SystemExit(
            f"refusing to refresh non-probe repo {repo_path}; use a live_worker_* repo name for seeded scenarios"
        )
    for child in children:
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _ensure_probe_git_baseline(repo_path: Path) -> None:
    if not (repo_path / ".git").exists():
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--short"], cwd=repo_path, capture_output=True, text=True, check=True)
    if not status.stdout.strip():
        return
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Live Worker Probe",
            "-c",
            "user.email=live-worker-probe@example.invalid",
            "commit",
            "-m",
            "seed live worker probe repo",
            "--no-verify",
        ],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )


def _remove_generated_runtime_dirs(repo_path: Path) -> None:
    for path in list(repo_path.rglob("__pycache__")) + [repo_path / ".pytest_cache"]:
        if path.exists():
            shutil.rmtree(path)


def _snapshot_repo_files(repo_path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not repo_path.exists():
        return files
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name.endswith((".pyc", ".pyo")):
            continue
        try:
            relative = str(path.relative_to(repo_path))
        except ValueError:
            continue
        if relative.startswith(".pytest_cache/"):
            continue
        files[relative] = path.read_text(encoding="utf-8", errors="replace")
    return files


def _run_pytest(repo_path: Path, label: str) -> dict[str, object]:
    if not repo_path.exists():
        return {
            "label": label,
            "command": [sys.executable, "-m", "pytest", "-q"],
            "returncode": 127,
            "stdout": "",
            "stderr": f"repo path does not exist: {repo_path}",
        }
    command = [sys.executable, "-m", "pytest", "-q"]
    if (repo_path / "pyproject.toml").exists():
        command = ["uv", "run", "pytest", "-q"]
        pytest_extra = _pyproject_pytest_extra(repo_path)
        if pytest_extra:
            command = ["uv", "run", "--extra", pytest_extra, "pytest", "-q"]
    completed = subprocess.run(
        command,
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "label": label,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _pyproject_pytest_extra(repo_path: Path) -> str | None:
    pyproject = repo_path / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    optional = data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(optional, dict):
        return None
    extras_with_pytest = [
        str(extra)
        for extra, dependencies in optional.items()
        if isinstance(dependencies, list)
        and any(str(dependency).split(";", 1)[0].strip().lower().startswith("pytest") for dependency in dependencies)
    ]
    for preferred in ("dev", "test", "tests", "testing"):
        if preferred in extras_with_pytest:
            return preferred
    return sorted(extras_with_pytest)[0] if extras_with_pytest else None


def _run(command: list[str], *, cwd: Path) -> dict[str, object]:
    if not cwd.exists():
        return {
            "command": command,
            "returncode": 127,
            "stdout": "",
            "stderr": f"cwd does not exist: {cwd}",
        }
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def _probe_not_run_result(label: str) -> dict[str, object]:
    return {
        "label": label,
        "command": [],
        "returncode": None,
        "stdout": "",
        "stderr": "not run",
    }


if __name__ == "__main__":
    raise SystemExit(main())
