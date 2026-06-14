"""Run a live AppV2 decomposer -> planner -> worker probe against seeded repos."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from appV2.decomposer.runtime import DecomposerRuntime
from appV2.planner.runtime import PhasePlannerRuntime
from appV2.runtime_matrix import RuntimeMatrixLogger
from appV2.worker.runtime import WorkerRuntime
from scripts.live_worker_runtime_probe import (
    FILE_POLICY_ARCHIVE_PROMPT,
    FILE_WORKSPACE_MANAGEMENT_PROMPT,
    _ensure_file_workspace_repo,
    _ensure_policy_archive_repo,
    _probe_not_run_result,
    _run,
    _run_pytest,
    _slug,
    _snapshot_repo_files,
)


DEFAULT_REPO = "live_worker_appv2_probe_repo"
DEFAULT_PROMPT = (
    "In the repo rooted at {repo}, inspect the workspace, make a small safe README update "
    "that records the current status, and verify the final file state."
)

SCENARIOS: dict[str, dict[str, object]] = {
    "readme_status_update": {
        "repo": DEFAULT_REPO,
        "prompt_template": DEFAULT_PROMPT,
        "seeder": "readme_status_update",
    },
    "file_workspace_cleanup": {
        "repo": "live_worker_appv2_workspace_repo",
        "prompt_template": FILE_WORKSPACE_MANAGEMENT_PROMPT,
        "prompt_repo_literal": "live_worker_workspace_repo",
        "seeder": _ensure_file_workspace_repo,
    },
    "file_policy_archive_reorg": {
        "repo": "live_worker_appv2_policy_archive_repo",
        "prompt_template": FILE_POLICY_ARCHIVE_PROMPT,
        "prompt_repo_literal": "live_worker_policy_archive_repo",
        "seeder": _ensure_policy_archive_repo,
    },
}


def main() -> int:
    args = _parse_args()
    workspace = Path.cwd()
    scenario = SCENARIOS[args.scenario]
    repo_name = args.repo or str(scenario["repo"])
    repo_path = (workspace / repo_name).resolve()
    prompt = args.prompt or _scenario_prompt(scenario=scenario, repo_name=repo_name)

    _ensure_mock_repo(repo_path=repo_path, scenario_name=args.scenario)
    _configure_worker_env(args)

    out_dir = (workspace / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"live-appv2-{args.scenario}-{_slug(args.worker_model)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    trace = RuntimeMatrixLogger()
    reporter = _LiveMatrixReporter(trace=trace, poll_interval_seconds=args.matrix_poll_interval)
    reporter.start()
    baseline: dict[str, object] = _probe_not_run_result("before")
    after: dict[str, object] = _probe_not_run_result("after")
    envelope = None
    phase_plan = None
    result = None
    run_error: dict[str, object] | None = None

    try:
        try:
            baseline = _run_pytest(repo_path, "before")
            print(f"PHASE baseline_pytest returncode={baseline['returncode']}", flush=True)

            decomposer = DecomposerRuntime.from_env(args.dotenv)
            planner = PhasePlannerRuntime.from_env(args.dotenv)
            worker = WorkerRuntime.from_env(args.dotenv, planner_runtime=planner, root_path=repo_path)

            print("PHASE decomposer start", flush=True)
            envelope = decomposer.run(prompt, trace=trace)
            print(f"PHASE decomposer done request_id={envelope.request_id}", flush=True)

            print("PHASE planner start", flush=True)
            phase_plan = planner.run(envelope, trace=trace)
            print(f"PHASE planner done plan_id={phase_plan.plan_id} phases={len(phase_plan.phases)}", flush=True)

            print("PHASE worker start", flush=True)
            result = worker.run(phase_plan, envelope=envelope, trace=trace)
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
        "phase_plan": phase_plan.model_dump(mode="json") if phase_plan is not None else None,
        "result": result.model_dump(mode="json") if result is not None else None,
        "after_pytest": after,
        "git_status": _run(["git", "status", "--short"], cwd=repo_path),
        "final_files": _snapshot_repo_files(repo_path),
        "probe_error": run_error,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"OUTPUT_PATH={out_path}")
    print(
        json.dumps(
            {
                "baseline_returncode": baseline["returncode"],
                "result_status": result.status if result is not None else "probe_error",
                "after_returncode": after["returncode"],
                "phase_count": len(phase_plan.phases) if phase_plan is not None else 0,
                "runtime_matrix_rows": trace.snapshot()["row_count"],
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS.keys()),
        default="readme_status_update",
    )
    parser.add_argument("--repo", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--worker-model", default=os.environ.get("APPV2_WORKER_LLM_MODEL", "xiaomi/mimo-v2.5-pro"))
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--out-dir", default="plan")
    parser.add_argument("--worker-timeout", default="90")
    parser.add_argument("--max-tokens", default="2400")
    parser.add_argument("--matrix-poll-interval", type=float, default=1.0)
    return parser.parse_args()


def _scenario_prompt(*, scenario: dict[str, object], repo_name: str) -> str:
    template = str(scenario["prompt_template"])
    literal = str(scenario.get("prompt_repo_literal") or scenario["repo"])
    if "{repo}" in template:
        return template.format(repo=repo_name)
    return template.replace(literal, repo_name)


def _ensure_mock_repo(*, repo_path: Path, scenario_name: str) -> None:
    seeder = SCENARIOS[scenario_name]["seeder"]
    if seeder == "readme_status_update":
        repo_path.mkdir(parents=True, exist_ok=True)
        readme_path = repo_path / "README.md"
        if not readme_path.exists():
            readme_path.write_text("# AppV2 live probe repo\n", encoding="utf-8")
        return
    assert callable(seeder)
    seeder(repo_path)


def _configure_worker_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_MODEL"] = args.worker_model
    os.environ["APPV2_WORKER_LLM_PROVIDER_SORT"] = "latency"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = "0"
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


def _worker_env_snapshot() -> dict[str, str | None]:
    return {
        "APPV2_WORKER_LLM_ENABLED": os.environ.get("APPV2_WORKER_LLM_ENABLED"),
        "APPV2_WORKER_LLM_MODEL": os.environ.get("APPV2_WORKER_LLM_MODEL"),
        "APPV2_WORKER_LLM_PROVIDER_SORT": os.environ.get("APPV2_WORKER_LLM_PROVIDER_SORT"),
        "APPV2_WORKER_LLM_TIMEOUT_SECONDS": os.environ.get("APPV2_WORKER_LLM_TIMEOUT_SECONDS"),
        "APPV2_WORKER_LLM_MAX_TOKENS": os.environ.get("APPV2_WORKER_LLM_MAX_TOKENS"),
    }


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


if __name__ == "__main__":
    raise SystemExit(main())
