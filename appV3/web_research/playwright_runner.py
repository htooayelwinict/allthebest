from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import PACKAGE_JSON, PLAYWRIGHT_CONFIG_TS, PLAYWRIGHT_READY_MARKER, RESULT_MARKER
from .schemas import PlaywrightResult, ToolArgs
from .validators import validate_typescript


@dataclass
class RuntimePaths:
    run_id: str
    workspace: Path
    tests_dir: Path
    package_json: Path
    playwright_config: Path
    system_prompt: Path
    transcript: Path
    stdout_log: Path
    stderr_log: Path


def build_runtime_paths(runtime_root: Path, run_id: str) -> RuntimePaths:
    workspace = runtime_root / run_id
    return RuntimePaths(
        run_id=run_id,
        workspace=workspace,
        tests_dir=workspace / "tests",
        package_json=workspace / "package.json",
        playwright_config=workspace / "playwright.config.ts",
        system_prompt=workspace / "system_prompt.txt",
        transcript=workspace / "agent_transcript.json",
        stdout_log=workspace / "runner_stdout.txt",
        stderr_log=workspace / "runner_stderr.txt",
    )


class PlaywrightToolRunner:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        install_dependencies: bool = True,
        install_browsers: bool = True,
        setup_timeout: int = 180,
    ) -> None:
        self.paths = paths
        self.install_dependencies = install_dependencies
        self.install_browsers = install_browsers
        self.setup_timeout = setup_timeout
        self._ready = False

    def ensure_project(self) -> None:
        if self._ready:
            return
        if shutil.which("node") is None:
            raise RuntimeError("node is required to run Playwright TypeScript.")
        if shutil.which("npm") is None:
            raise RuntimeError("npm is required to run Playwright TypeScript.")

        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        self.paths.tests_dir.mkdir(parents=True, exist_ok=True)
        if not self.paths.package_json.exists():
            self.paths.package_json.write_text(PACKAGE_JSON, encoding="utf-8")
        if not self.paths.playwright_config.exists():
            self.paths.playwright_config.write_text(PLAYWRIGHT_CONFIG_TS, encoding="utf-8")

        node_module = self.paths.workspace / "node_modules" / "@playwright" / "test"
        if self.install_dependencies and not node_module.exists():
            self._run_setup_command(["npm", "install"], "npm install")
        if self.install_browsers and not (self.paths.workspace / PLAYWRIGHT_READY_MARKER).exists():
            self._run_setup_command(["npx", "playwright", "install", "chromium"], "playwright install chromium")
            (self.paths.workspace / PLAYWRIGHT_READY_MARKER).write_text("ok\n", encoding="utf-8")
        self._ready = True

    def run_playwright_code(self, args: ToolArgs) -> PlaywrightResult:
        workspace_path = self.paths.workspace.resolve()
        workspace = str(workspace_path)
        try:
            self.ensure_project()
        except Exception as exc:
            return PlaywrightResult(
                ok=False,
                execution_ok=False,
                data_ok=False,
                warnings=["setup_failed"],
                exit_code=None,
                stdout="",
                stderr=f"Playwright setup failed: {exc}",
                timed_out=False,
                test_file=None,
                saved=bool(args.save_as),
                workspace=workspace,
                setup_error=True,
            )

        issues = validate_typescript(args.code)
        if issues:
            return PlaywrightResult(
                ok=False,
                execution_ok=False,
                data_ok=False,
                warnings=["code_validation_blocked"],
                exit_code=None,
                stdout="",
                stderr="TypeScript validation failed:\n" + "\n".join(f"- {issue}" for issue in issues),
                timed_out=False,
                test_file=None,
                saved=bool(args.save_as),
                workspace=workspace,
            )

        filename = build_test_filename(args.save_as)
        script_path = (self.paths.tests_dir / filename).resolve()
        try:
            script_path.relative_to(workspace_path)
        except ValueError:
            return PlaywrightResult(
                ok=False,
                execution_ok=False,
                data_ok=False,
                warnings=["path_escape_blocked"],
                exit_code=None,
                stdout="",
                stderr=f"test path escapes workspace: {script_path}",
                timed_out=False,
                test_file=None,
                saved=bool(args.save_as),
                workspace=workspace,
            )

        script_path.write_text(args.code, encoding="utf-8")
        relative_file = str(script_path.relative_to(workspace_path))
        try:
            result = subprocess.run(
                ["npx", "playwright", "test", str(script_path), "--project=chromium"],
                cwd=self.paths.workspace,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
            )
            stdout = strip_ansi(result.stdout or "")
            stderr = strip_ansi(result.stderr or "")
            parsed = extract_marked_json(stdout)
            warnings = collect_warnings(stdout, stderr, exit_code=result.returncode, parsed_json=parsed)
            execution_ok = result.returncode == 0
            data_ok = parsed is not None and not any(
                warning in warnings
                for warning in (
                    "empty_collection_detected",
                    "no_results_detected",
                    "missing_result_marker",
                    "json_parse_failure_detected",
                    "api_auth_error_detected",
                )
            )
            return PlaywrightResult(
                ok=execution_ok and data_ok,
                execution_ok=execution_ok,
                data_ok=data_ok,
                warnings=warnings,
                exit_code=result.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                test_file=relative_file,
                saved=bool(args.save_as),
                workspace=workspace,
                parsed_json=parsed,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = strip_ansi((exc.stdout or "") if isinstance(exc.stdout, str) else "")
            return PlaywrightResult(
                ok=False,
                execution_ok=False,
                data_ok=False,
                warnings=["execution_timeout"],
                exit_code=None,
                stdout=stdout,
                stderr=f"Script timed out after {args.timeout}s",
                timed_out=True,
                test_file=relative_file,
                saved=bool(args.save_as),
                workspace=workspace,
            )
        finally:
            append_text(self.paths.stdout_log, f"\n\n# {relative_file}\n")
            if not args.save_as:
                script_path.unlink(missing_ok=True)

    def _run_setup_command(self, command: list[str], label: str) -> None:
        result = subprocess.run(
            command,
            cwd=self.paths.workspace,
            text=True,
            capture_output=True,
            timeout=self.setup_timeout,
            check=False,
        )
        append_text(self.paths.stdout_log, f"\n\n$ {' '.join(command)}\n{result.stdout}")
        append_text(self.paths.stderr_log, f"\n\n$ {' '.join(command)}\n{result.stderr}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise RuntimeError(f"{label} failed with exit code {result.returncode}: {detail[:500]}")


def build_test_filename(save_as: str | None) -> str:
    if save_as:
        stem = Path(save_as).stem.replace(".spec", "")
        stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", stem).strip("._-")
        if stem:
            return f"{stem}.spec.ts"
    return f"pw_{uuid.uuid4().hex[:8]}.spec.ts"


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)


def extract_marked_json(stdout: str) -> Any:
    for line in stdout.splitlines():
        if RESULT_MARKER not in line:
            continue
        raw = line.split(RESULT_MARKER, 1)[1].strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def collect_warnings(stdout: str, stderr: str, *, exit_code: int | None, parsed_json: Any) -> list[str]:
    warnings: list[str] = []
    combined = f"{stdout}\n{stderr}".lower()
    if exit_code not in (0, None):
        warnings.append("non_zero_exit_code")
    if RESULT_MARKER.lower() not in combined:
        warnings.append("missing_result_marker")
    elif parsed_json is None:
        warnings.append("json_parse_failure_detected")
    if re.search(r"\bstatus\s*:\s*(4\d\d|5\d\d)\b", combined):
        warnings.append("http_4xx_or_5xx_detected")
    if "apikeyinvalid" in combined or "api key is invalid" in combined:
        warnings.append("api_auth_error_detected")
    if re.search(r"(?m)^\s*\[\s*\]\s*$", stdout) or re.search(r":\s*\[\s*\]", stdout):
        warnings.append("empty_collection_detected")
    if re.search(r"\btotal\s+\w+\s+collected\s*:\s*0\b", combined):
        warnings.append("no_results_detected")
    return warnings


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
