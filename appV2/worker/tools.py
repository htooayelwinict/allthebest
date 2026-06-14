"""Permission-gated file/code tools for the AppV2 worker loop.

This tool layer intentionally borrows the production lessons from the V1
WorkerToolbox: high-signal observations, idempotent file operations, manifest
helpers, bounded command execution, and repairable denial payloads.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from appV2.schemas import FileOperation, PhaseStep
from appV2.worker.policy_gate import PolicyGate, TOOL_GROUPS_BY_NAME


IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
HOLD_MARKERS = ("do_not_move", "do-not-move", "dont_move", "don't move", "hold", "keep")


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot execute successfully."""


class ToolRegistry:
    def __init__(self, *, root_path: str | Path, timeout_seconds: float = 15.0, max_file_bytes: int = 200_000) -> None:
        self.root = Path(root_path).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.policy_gate = PolicyGate(root_path=self.root)

    def available_tools(self, phase: PhaseStep) -> list[dict[str, Any]]:
        return [
            _tool_spec(name=name, group=group)
            for name, group in sorted(TOOL_GROUPS_BY_NAME.items())
            if group in phase.allowed_tool_groups
        ]

    def execute(
        self,
        *,
        phase: PhaseStep,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        arguments = arguments or {}
        if tool_name == "repo_snapshot":
            return self._repo_snapshot(arguments)
        if tool_name == "list_dir":
            return self._list_dir(arguments)
        if tool_name == "read_file":
            return self._read_file(arguments)
        if tool_name == "read_many_files":
            return self._read_many_files(arguments)
        if tool_name == "file_search":
            return self._file_search(arguments)
        if tool_name == "text_search":
            return self._text_search(arguments)
        if tool_name == "json_query":
            return self._json_query(arguments)
        if tool_name == "git_status":
            return self._run_checked(["git", "status", "--short"], allowed_returncodes=None)
        if tool_name == "git_diff":
            path = str(arguments.get("path") or "")
            return self._run_checked(["git", "diff", "--", *([path] if path else [])], allowed_returncodes=None)
        if tool_name == "diff_summary":
            return self._diff_summary(arguments)
        if tool_name == "mutation_scope_check":
            return self._mutation_scope_check(phase)
        if tool_name == "classify_file_management_candidates":
            return self._classify_file_management_candidates(phase, arguments)
        if tool_name == "write_file":
            return self.apply_operations(phase=phase, operations=[FileOperation(action="write", **arguments)])
        if tool_name == "write_many_files":
            operations = [
                FileOperation(action="write", path=str(item.get("path") or ""), content=str(item.get("content") or ""), overwrite=bool(item.get("overwrite", True)))
                for item in _object_list(arguments.get("files"))
            ]
            if not operations:
                raise ToolExecutionError("write_many_files requires a non-empty files array")
            return self.apply_operations(phase=phase, operations=operations)
        if tool_name == "write_json_manifest":
            return self._write_json_manifest(phase, arguments)
        if tool_name == "replace_in_file":
            return self.apply_operations(phase=phase, operations=[FileOperation(action="replace", **arguments)])
        if tool_name == "move_file":
            return self.apply_operations(phase=phase, operations=[FileOperation(action="move", **arguments)])
        if tool_name == "delete_file":
            return self.apply_operations(phase=phase, operations=[FileOperation(action="delete", **arguments)])
        if tool_name == "apply_file_operations":
            return self.apply_operations(phase=phase, operations=_coerce_file_operations(arguments.get("operations")))
        if tool_name == "runtime_capabilities":
            return self._runtime_capabilities()
        if tool_name == "run_readonly_command":
            return self._run_readonly_command(arguments)
        if tool_name == "run_project_tests":
            return self._run_project_tests(arguments)
        if tool_name == "run_focused_tests":
            return self._run_focused_tests(arguments)
        if tool_name == "run_required_verification":
            return self._run_required_verification(arguments)
        if tool_name == "verify_file_state":
            return self._verify_file_state(arguments)
        if tool_name == "verify_file_state_against_manifest":
            return self._verify_file_state_against_manifest(phase, arguments)
        if tool_name == "scope_audit":
            return self._mutation_scope_check(phase)
        raise ToolExecutionError(f"unknown tool: {tool_name}")

    def apply_operations(self, *, phase: PhaseStep, operations: list[FileOperation]) -> dict[str, Any]:
        if not operations:
            raise ToolExecutionError("file operation batch is empty")
        if len(operations) > 50:
            raise ToolExecutionError("file operation batch supports at most 50 operations")
        decision = self.policy_gate.validate_mutation(phase=phase, operations=operations)
        if not decision.allowed:
            return _denied_result(decision.code, decision.message, repairable=decision.repairable, metadata=decision.metadata)

        operational_denial = self._preflight_operational_denials(operations)
        if operational_denial:
            return operational_denial

        touched: list[str] = []
        ledger: list[dict[str, Any]] = []
        for index, operation in enumerate(operations, start=1):
            entry = self._apply_operation(index=index, operation=operation)
            ledger.append(entry)
            touched.extend(str(path) for path in entry.get("paths", []) if path)
        applied = [entry for entry in ledger if entry.get("status") == "applied"]
        return {
            "status": "completed",
            "operation_count": len(ledger),
            "applied_count": len(applied),
            "already_done_count": len([entry for entry in ledger if entry.get("status") == "already_done"]),
            "skipped_count": len([entry for entry in ledger if entry.get("status") == "skipped"]),
            "touched_paths": sorted(set(touched)),
            "changed_paths": sorted({path for entry in applied for path in entry.get("paths", [])}),
            "operations": ledger,
        }

    def _preflight_operational_denials(self, operations: list[FileOperation]) -> dict[str, Any] | None:
        rejected_paths: list[str] = []
        messages: list[str] = []
        for operation in operations:
            if operation.action == "move":
                source = self._safe_path(operation.source)
                destination = self._safe_path(operation.destination)
                if source == destination:
                    continue
                if not source.is_file() and not destination.is_file():
                    rejected_paths.append(self._display(source))
                    messages.append(f"move source is not a file: {self._display(source)}")
                if source.is_file() and destination.exists() and not operation.overwrite:
                    rejected_paths.append(self._display(destination))
                    messages.append(f"move destination exists: {self._display(destination)}; set overwrite=true or skip")
            elif operation.action == "write":
                path = self._safe_path(operation.path)
                if path.exists() and not path.is_file():
                    rejected_paths.append(self._display(path))
                    messages.append(f"write target is not a file: {self._display(path)}")
                if path.exists() and not operation.overwrite:
                    rejected_paths.append(self._display(path))
                    messages.append(f"write target exists: {self._display(path)}; set overwrite=true or skip")
            elif operation.action == "replace":
                path = self._safe_path(operation.path)
                old = operation.old or ""
                if not old:
                    rejected_paths.append(self._display(path))
                    messages.append("replace operation requires a non-empty old value")
                elif not path.is_file():
                    rejected_paths.append(self._display(path))
                    messages.append(f"replace target is not a file: {self._display(path)}")
                elif old not in path.read_text(encoding="utf-8", errors="replace"):
                    rejected_paths.append(self._display(path))
                    messages.append(f"replace old value was not found: {self._display(path)}")
            elif operation.action == "delete":
                path = self._safe_path(operation.path)
                if path.exists() and not path.is_file():
                    rejected_paths.append(self._display(path))
                    messages.append(f"delete target is not a file: {self._display(path)}")
            elif operation.action == "mkdir":
                path = self._safe_path(operation.path)
                if path.exists() and not path.is_dir():
                    rejected_paths.append(self._display(path))
                    messages.append(f"mkdir target is not a directory: {self._display(path)}")
        if not rejected_paths:
            return None
        return _denied_result(
            "file_operation_batch_denied",
            "; ".join(messages),
            repairable=True,
            metadata={"rejected_paths": sorted(set(rejected_paths)), "touched_paths": _operation_display_paths(self, operations)},
        )

    def _apply_operation(self, *, index: int, operation: FileOperation) -> dict[str, Any]:
        if operation.action == "write":
            path = self._safe_path(operation.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = operation.content or ""
            path.write_text(content, encoding="utf-8")
            return {"index": index, "action": "write", "status": "applied", "paths": [self._display(path)], "summary": f"wrote {len(content.encode('utf-8'))} bytes"}
        if operation.action == "replace":
            path = self._safe_path(operation.path)
            content = path.read_text(encoding="utf-8", errors="replace")
            old = operation.old or ""
            new = operation.new or ""
            path.write_text(content.replace(old, new, 1), encoding="utf-8")
            return {"index": index, "action": "replace", "status": "applied", "paths": [self._display(path)], "summary": "replaced one occurrence"}
        if operation.action == "move":
            source = self._safe_path(operation.source)
            destination = self._safe_path(operation.destination)
            paths = [self._display(source), self._display(destination)]
            if source == destination:
                return {"index": index, "action": "move", "status": "skipped", "paths": paths, "summary": "source equals destination"}
            if not source.is_file() and destination.is_file():
                return {"index": index, "action": "move", "status": "already_done", "paths": paths, "summary": "source missing and destination already exists"}
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            return {"index": index, "action": "move", "status": "applied", "paths": paths, "summary": "file moved"}
        if operation.action == "delete":
            path = self._safe_path(operation.path)
            if not path.exists():
                return {"index": index, "action": "delete", "status": "already_done", "paths": [self._display(path)], "summary": "file already absent"}
            path.unlink()
            return {"index": index, "action": "delete", "status": "applied", "paths": [self._display(path)], "summary": "file deleted"}
        path = self._safe_path(operation.path)
        path.mkdir(parents=True, exist_ok=True)
        return {"index": index, "action": "mkdir", "status": "applied", "paths": [self._display(path)], "summary": "directory created or already existed"}

    def _repo_snapshot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._safe_path(arguments.get("path") or ".")
        if not start.exists():
            return {"path": self._display(start), "exists": False, "directories": [], "files": [], "is_empty": True, "test_candidates": [], "config_files": [], "git_status": {}, "error": "not_found"}
        if not start.is_dir():
            relative = self._display(start)
            return {"path": relative, "exists": True, "directories": [], "files": [relative], "file_count": 1, "is_empty": False, "test_candidates": [relative] if _looks_like_test_path(relative) else [], "config_files": [], "git_status": {}, "error": "not_directory"}
        files: list[str] = []
        directories: set[str] = set()
        test_candidates: list[str] = []
        config_files: list[str] = []
        for path in sorted(start.rglob("*")):
            if self._is_ignored_path(path):
                continue
            relative = self._display(path)
            if path.is_dir():
                directories.add(relative)
                continue
            files.append(relative)
            if _looks_like_test_path(relative):
                test_candidates.append(relative)
            if Path(relative).name in {"README.md", "pyproject.toml", "package.json", "requirements.txt", "Makefile"}:
                config_files.append(relative)
            if len(files) >= 300:
                break
        try:
            git_status = self._run_checked(["git", "status", "--short"], allowed_returncodes=None)
        except ToolExecutionError as exc:
            git_status = {"stdout": "", "stderr": str(exc), "returncode": 128, "status": "failed"}
        return {
            "root": str(self.root),
            "path": self._display(start),
            "exists": True,
            "directories": sorted(directories)[:100],
            "files": files[:300],
            "file_count": len(files[:300]),
            "is_empty": not files and not directories,
            "test_candidates": test_candidates[:50],
            "config_files": config_files[:30],
            "git_status": git_status,
        }

    def _list_dir(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._safe_path(arguments.get("path") or ".")
        if not path.exists():
            return {"path": self._display(path), "exists": False, "entries": [], "children": [], "error": "not_found"}
        if not path.is_dir():
            return {"path": self._display(path), "exists": True, "entries": [], "children": [], "error": "not_directory"}
        entries = [
            {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            for child in sorted(path.iterdir(), key=lambda item: item.name)
            if not self._is_ignored_path(child)
        ][:200]
        return {"path": self._display(path), "exists": True, "entries": entries, "children": [entry["name"] for entry in entries]}

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._safe_path(arguments.get("path"))
        if not path.exists():
            return {"path": self._display(path), "exists": False, "content": "", "truncated": False, "error": "not_found"}
        if not path.is_file():
            return {"path": self._display(path), "exists": True, "content": "", "truncated": False, "error": "not_file"}
        content = path.read_text(encoding="utf-8", errors="replace")[: self.max_file_bytes]
        return {"path": self._display(path), "exists": True, "content": content, "truncated": path.stat().st_size > len(content)}

    def _read_many_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = _string_list(arguments.get("paths"))
        if not paths:
            raise ToolExecutionError("read_many_files requires at least one path")
        per_file_limit = max(1, self.max_file_bytes // max(1, min(len(paths), 20)))
        files = []
        for raw_path in paths[:20]:
            result = self._read_file({"path": raw_path})
            if result.get("content"):
                result["content"] = str(result["content"])[:per_file_limit]
            files.append(result)
        return {"files": files}

    def _file_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        base = self._safe_path(arguments.get("path") or ".")
        pattern = str(arguments.get("pattern") or "*")
        matches = []
        for path in sorted(base.rglob(pattern)):
            if self._is_ignored_path(path):
                continue
            if path.is_file():
                matches.append(self._display(path))
            if len(matches) >= 200:
                break
        return {"pattern": pattern, "matches": matches}

    def _text_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        base = self._safe_path(arguments.get("path") or ".")
        pattern = str(arguments.get("pattern") or "")
        if not pattern:
            raise ToolExecutionError("text_search requires a non-empty pattern")
        relative_base = self._display(base)
        try:
            result = self._run_checked(["rg", "-n", pattern, relative_base], allowed_returncodes=None)
            matches = _parse_rg_matches(result.get("stdout", ""))
            result["matches"] = matches[:100]
            result["status"] = "passed" if result.get("returncode") in {0, 1} else "failed"
            return result
        except ToolExecutionError:
            matches: list[dict[str, Any]] = []
            for path in sorted(base.rglob("*")):
                if not path.is_file() or self._is_ignored_path(path) or path.stat().st_size > self.max_file_bytes:
                    continue
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    if pattern in line:
                        matches.append({"path": self._display(path), "line": line_no, "text": line[:240]})
                if len(matches) >= 100:
                    break
            return {"matches": matches[:100], "stdout": "", "stderr": "", "returncode": 0 if matches else 1, "status": "passed" if matches else "failed"}

    def _json_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._safe_path(arguments.get("path"))
        data = json.loads(path.read_text(encoding="utf-8"))
        value: Any = data
        query = str(arguments.get("query") or "")
        for part in [part for part in query.strip(".").split(".") if part]:
            if isinstance(value, list):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise ToolExecutionError(f"json_query cannot descend into {type(value).__name__}")
        return {"path": self._display(path), "query": query, "value": value}

    def _write_json_manifest(self, phase: PhaseStep, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        payload = arguments.get("payload")
        if not isinstance(payload, dict):
            raise ToolExecutionError("write_json_manifest requires payload to be an object")
        required_keys = _required_json_keys(phase, arguments)
        if not required_keys:
            required_keys = sorted(str(key) for key in payload)
        missing = [key for key in required_keys if key not in payload]
        if missing:
            return _denied_result("manifest_missing_required_keys", "write_json_manifest payload is missing required keys: " + ", ".join(missing), repairable=True, metadata={"path": path, "missing_keys": missing})
        total_key = str(arguments.get("total_key") or "").strip() or _infer_manifest_total_key(required_keys=required_keys, payload=payload)
        count_keys = _string_list(arguments.get("count_keys")) or _infer_manifest_count_keys(required_keys=required_keys, payload=payload, total_key=total_key)
        non_list_count_keys = [key for key in count_keys if not isinstance(payload.get(key), list)]
        if non_list_count_keys:
            return _denied_result("manifest_count_key_not_list", "manifest count keys must contain list values: " + ", ".join(non_list_count_keys), repairable=True, metadata={"path": path, "count_keys": non_list_count_keys})
        counted_total = sum(len(payload.get(key) or []) for key in count_keys)
        counts_match = True
        if total_key:
            total_value = payload.get(total_key)
            if not isinstance(total_value, int):
                return _denied_result("manifest_total_not_integer", f"manifest total key {total_key} must be an integer", repairable=True, metadata={"path": path, "total_key": total_key})
            counts_match = total_value == counted_total
            if not counts_match:
                return _denied_result("manifest_total_mismatch", f"manifest {total_key}={total_value} does not match counted items {counted_total} from keys: {', '.join(count_keys)}", repairable=True, metadata={"path": path, "total_key": total_key, "count_keys": count_keys})
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        result = self.apply_operations(phase=phase, operations=[FileOperation(action="write", path=path, content=content, overwrite=True)])
        if result.get("status") == "denied":
            return result
        return {
            **result,
            "path": path,
            "manifest_path": path,
            "payload": payload,
            "required_keys": required_keys,
            "fields_present": sorted(str(key) for key in payload),
            "missing_fields": [],
            "counts_match": counts_match,
            "total_key": total_key or None,
            "total_value": payload.get(total_key) if total_key else None,
            "total_artifacts": payload.get(total_key) if total_key else None,
            "count_keys": count_keys,
            "counted_total": counted_total,
            "bytes_written": len(content.encode("utf-8")),
        }

    def _run_readonly_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_command = arguments.get("command")
        if isinstance(raw_command, str):
            command = shlex.split(raw_command)
        elif isinstance(raw_command, list):
            command = [str(part) for part in raw_command]
        else:
            raise ToolExecutionError("run_readonly_command requires command as string or list")
        env_overrides, command = _normalize_readonly_command(command)
        if not _is_allowlisted(command):
            raise ToolExecutionError(f"command is not allowlisted: {' '.join(command)}")
        command = self._canonical_readonly_command(command)
        result = self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)
        if command[:2] == ["uv", "run"]:
            result["detected_command_source"] = self._project_test_command_source(command)
        return result

    def _run_project_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = [self._display(self._safe_path(path)) for path in _string_list(arguments.get("paths")) if path]
        command = self._project_pytest_command(paths or ["-q"])
        env_overrides = {"PYTHONPATH": "."} if command[0] != "uv" else None
        result = self._run_checked(command, allowed_returncodes=None, env_overrides=env_overrides)
        result["detected_command_source"] = self._project_test_command_source(command)
        return result

    def _run_focused_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        paths = [self._display(self._safe_path(path)) for path in _string_list(arguments.get("paths")) if path]
        return self._run_checked([sys.executable, "-m", "pytest", *(paths or ["-q"])], allowed_returncodes=None, env_overrides={"PYTHONPATH": "."})

    def _run_required_verification(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._run_project_tests(arguments)
        return _normalize_required_verification_result(result)

    def _verify_file_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        required = _string_list(arguments.get("required_paths"))
        forbidden = _string_list(arguments.get("forbidden_paths"))
        missing = [path for path in required if not self._safe_path(path).exists()]
        present_forbidden = [path for path in forbidden if self._safe_path(path).exists()]
        status = "passed" if not missing and not present_forbidden else "failed"
        return {"status": status, "missing": missing, "present_forbidden": present_forbidden, "required_paths": required, "forbidden_paths": forbidden}

    def _verify_file_state_against_manifest(self, phase: PhaseStep, arguments: dict[str, Any]) -> dict[str, Any]:
        manifest_path = str(arguments.get("manifest_path") or arguments.get("path") or "")
        manifest = self._safe_path(manifest_path)
        required_keys = _required_json_keys(phase, arguments)
        move_pairs = _move_pairs_from_args_or_phase(phase, arguments)
        held_paths = _string_list(arguments.get("held_paths")) or _string_list(phase.policy.get("held_paths"))
        if not manifest.exists():
            return {"status": "failed", "manifest_path": self._display(manifest), "manifest_exists": False, "errors": [{"code": "manifest_missing", "path": self._display(manifest)}]}
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"status": "failed", "manifest_path": self._display(manifest), "manifest_exists": True, "errors": [{"code": "manifest_invalid_json", "message": str(exc)}]}
        if not isinstance(payload, dict):
            return {"status": "failed", "manifest_path": self._display(manifest), "manifest_exists": True, "errors": [{"code": "manifest_payload_not_object"}]}
        missing_fields = [key for key in required_keys if key not in payload]
        total_key = _total_key(required_keys) or _infer_total_key_from_payload(payload)
        count_keys = _count_keys(required_keys) or _infer_count_keys_from_payload(payload, total_key=total_key)
        counted_total = sum(len(payload.get(key) or []) for key in count_keys if isinstance(payload.get(key), list))
        total_value = payload.get(total_key) if total_key else None
        counts_match = total_key is None or total_value == counted_total
        errors: list[dict[str, Any]] = []
        if missing_fields:
            errors.append({"code": "manifest_missing_fields", "missing_fields": missing_fields})
        if not counts_match:
            errors.append({"code": "manifest_count_mismatch", "total_key": total_key, "total_value": total_value, "counted_total": counted_total, "count_keys": count_keys})
        move_checks = []
        for pair in move_pairs:
            source = str(pair.get("source") or "")
            destination = str(pair.get("destination") or "")
            source_path = self._safe_path(source)
            destination_path = self._safe_path(destination)
            manifest_key = _manifest_key_for_extension(extension=destination_path.suffix.lower(), required_keys=required_keys)
            manifest_contains = True
            if manifest_key and isinstance(payload.get(manifest_key), list):
                values = {str(value) for value in payload[manifest_key]}
                manifest_contains = destination in values or destination_path.name in values
            passed = not source_path.exists() and destination_path.exists() and manifest_contains
            check = {"source": source, "destination": destination, "source_exists": source_path.exists(), "destination_exists": destination_path.exists(), "manifest_key": manifest_key, "manifest_contains": manifest_contains, "passed": passed}
            move_checks.append(check)
            if not passed:
                errors.append({"code": "move_state_mismatch", **check})
        held_checks = [{"path": path, "exists": self._safe_path(path).exists(), "passed": self._safe_path(path).exists()} for path in held_paths]
        for check in held_checks:
            if not check["passed"]:
                errors.append({"code": "held_path_missing", **check})
        return {
            "status": "passed" if not errors else "failed",
            "manifest_path": self._display(manifest),
            "manifest_exists": True,
            "required_keys": required_keys,
            "fields_present": sorted(str(key) for key in payload),
            "missing_fields": missing_fields,
            "count_keys": count_keys,
            "total_key": total_key,
            "total_value": total_value,
            "counted_total": counted_total,
            "counts_match": counts_match,
            "move_checks": move_checks,
            "held_checks": held_checks,
            "errors": errors,
        }

    def _classify_file_management_candidates(self, phase: PhaseStep, arguments: dict[str, Any]) -> dict[str, Any]:
        start = self._safe_path(arguments.get("path") or ".")
        if not start.exists():
            return {"status": "failed", "path": self._display(start), "candidates": [], "held_items": [], "unknown_items": [], "error": "path_not_found"}
        required_keys = _required_json_keys(phase, arguments)
        text_context = _phase_text(phase).lower()
        destination_map = _destination_map(text_context=text_context, required_keys=required_keys)
        candidates: list[dict[str, Any]] = []
        held_items: list[dict[str, Any]] = []
        unknown_items: list[dict[str, Any]] = []
        for file_path in _iter_files(start):
            relative = self._display(file_path)
            marker = _hold_marker(file_path, root=self.root)
            if marker:
                held_items.append({"path": relative, "reason": f"held by marker: {marker}", "evidence": [{"kind": "hold_marker", "value": marker}]})
                continue
            extension = file_path.suffix.lower()
            manifest_key = _manifest_key_for_extension(extension=extension, required_keys=required_keys)
            if manifest_key is None:
                if extension in {".md", ".markdown", ".log", ".json", ".csv"}:
                    unknown_items.append({"path": relative, "extension": extension, "reason": "extension has no explicit manifest/category rule", "evidence": [{"kind": "extension", "value": extension}]})
                continue
            destination_dir = destination_map.get(manifest_key)
            if not destination_dir:
                unknown_items.append({"path": relative, "extension": extension, "manifest_key": manifest_key, "reason": "no destination rule inferred for manifest key", "evidence": [{"kind": "manifest_key", "value": manifest_key}]})
                continue
            destination = f"{destination_dir.rstrip('/')}/{file_path.name}"
            if _normalize_path(relative) == _normalize_path(destination):
                held_items.append({"path": relative, "reason": "already in inferred destination", "evidence": [{"kind": "destination", "value": destination_dir}]})
                continue
            candidates.append({"source": relative, "destination": destination, "category": _category_for_key(manifest_key), "manifest_key": manifest_key, "basename": file_path.name, "reason": f"{extension or 'no-extension'} file matches {manifest_key}", "evidence": [{"kind": "extension", "value": extension}, {"kind": "destination", "value": destination_dir}]})
        total_key = _total_key(required_keys)
        payload_seed: dict[str, Any] = {key: [] for key in _count_keys(required_keys)}
        for candidate in candidates:
            payload_seed.setdefault(str(candidate["manifest_key"]), []).append(candidate["basename"])
        if total_key:
            payload_seed[total_key] = sum(len(value) for value in payload_seed.values() if isinstance(value, list))
        if "held_items" in required_keys:
            payload_seed["held_items"] = [Path(str(item["path"])).name for item in held_items]
        return {
            "status": "completed",
            "path": self._display(start),
            "candidate_count": len(candidates),
            "held_count": len(held_items),
            "unknown_count": len(unknown_items),
            "manifest_keys": required_keys,
            "destination_map": destination_map,
            "manifest_payload_seed": payload_seed,
            "total_key": total_key,
            "candidates": candidates,
            "held_items": held_items,
            "unknown_items": unknown_items,
        }

    def _runtime_capabilities(self) -> dict[str, Any]:
        checks = {
            "python": [sys.executable, "--version"],
            "pytest": [sys.executable, "-m", "pytest", "--version"],
            "uv": ["uv", "--version"],
            "node": ["node", "--version"],
            "npm": ["npm", "--version"],
            "git": ["git", "--version"],
        }
        results: dict[str, Any] = {}
        for name, command in checks.items():
            try:
                result = self._run_checked(command, allowed_returncodes=None)
            except ToolExecutionError as exc:
                results[name] = {"available": False, "command": command, "error": str(exc)}
                continue
            output = (str(result.get("stdout") or "") or str(result.get("stderr") or "")).strip()
            results[name] = {"available": result.get("returncode") == 0, "command": command, "returncode": result.get("returncode"), "version": output.splitlines()[0] if output else ""}
        preferred = "python" if results.get("python", {}).get("available") else None
        return {"capabilities": results, "preferred_local_stack": preferred}

    def _diff_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("path") or "")
        command_suffix = ["--", path] if path else []
        diff = self._run_checked(["git", "diff", *command_suffix], allowed_returncodes=None)
        changed_files = self._changed_file_names(path=path)
        diff_text = str(diff.get("stdout", ""))
        untracked_diffs = [self._new_file_diff(changed_file) for changed_file in changed_files if changed_file not in diff_text and (self.root / changed_file).is_file()]
        return {"changed_files": changed_files, "diff": "\n".join(part for part in [diff_text, *untracked_diffs] if part), "returncode": diff.get("returncode")}

    def _mutation_scope_check(self, phase: PhaseStep) -> dict[str, Any]:
        changed_files = self._changed_file_names()
        policy = phase.mutation_policy
        if policy is None:
            return {"scope_available": False, "changed_files": changed_files, "in_scope": [], "out_of_scope": changed_files, "forbidden_changes": []}
        targets = {_normalize_path(path) for path in [*policy.allowed_paths, *policy.advisory_paths]}
        forbidden = {_normalize_path(path) for path in policy.forbidden_paths}
        in_scope = [path for path in changed_files if not targets or any(path == target or path.startswith(f"{target}/") for target in targets)]
        out_of_scope = [path for path in changed_files if path not in in_scope]
        forbidden_changes = [path for path in changed_files if path in forbidden]
        return {"scope_available": True, "allowed_paths": sorted(targets), "forbidden_paths": sorted(forbidden), "changed_files": changed_files, "in_scope": in_scope, "out_of_scope": out_of_scope, "forbidden_changes": forbidden_changes, "passed": not out_of_scope and not forbidden_changes}

    def _changed_file_names(self, *, path: str = "") -> list[str]:
        command_suffix = ["--", path] if path else []
        changed: list[str] = []
        try:
            names = self._run_checked(["git", "diff", "--name-only", *command_suffix], allowed_returncodes=None)
            changed.extend(line.strip() for line in str(names.get("stdout") or "").splitlines() if line.strip())
            status = self._run_checked(["git", "status", "--short", *command_suffix], allowed_returncodes=None)
            for line in str(status.get("stdout") or "").splitlines():
                parsed = _parse_git_status_path(line)
                if parsed and parsed not in changed:
                    changed.append(parsed)
        except ToolExecutionError:
            return []
        return sorted(set(changed))

    def _new_file_diff(self, relative_path: str) -> str:
        path = self.root / relative_path
        if not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")[: self.max_file_bytes]
        return "".join(difflib.unified_diff([], content.splitlines(keepends=True), fromfile="/dev/null", tofile=relative_path))

    def _run_checked(
        self,
        command: list[str],
        *,
        allowed_returncodes: set[int] | None = {0, 1},
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            env = {**os.environ, **env_overrides} if env_overrides else None
            completed = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=self.timeout_seconds, check=False, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(f"tool command timed out: {' '.join(command)}") from exc
        except OSError as exc:
            raise ToolExecutionError(f"tool command failed to start: {' '.join(command)}") from exc
        stdout = completed.stdout[: self.max_file_bytes]
        stderr = completed.stderr[: self.max_file_bytes]
        if allowed_returncodes is not None and completed.returncode not in allowed_returncodes:
            raise ToolExecutionError(f"tool command exited with code {completed.returncode}: {' '.join(command)}\n{stderr}")
        return {"command": command, "env": env_overrides or {}, "stdout": stdout[-4000:], "stderr": stderr[-4000:], "returncode": completed.returncode, "status": "passed" if completed.returncode == 0 else "failed"}

    def _canonical_readonly_command(self, command: list[str]) -> list[str]:
        if command and command[0] == "pytest":
            return [sys.executable, "-m", "pytest", *command[1:]]
        if command[:2] == ["uv", "run"]:
            return self._canonical_uv_pytest_command(command)
        return command

    def _canonical_uv_pytest_command(self, command: list[str]) -> list[str]:
        args = command[2:]
        if not _is_allowed_uv_pytest_command(args) or _uv_arguments_select_extra(args):
            return command
        extra = self._pyproject_pytest_extra()
        if not extra:
            return command
        insert_at = 2 + _uv_run_option_prefix_length(args)
        return [*command[:insert_at], "--extra", extra, *command[insert_at:]]

    def _project_pytest_command(self, pytest_args: list[str]) -> list[str]:
        if (self.root / "pyproject.toml").is_file():
            extra = self._pyproject_pytest_extra()
            if extra:
                return ["uv", "run", "--extra", extra, "pytest", *pytest_args]
            return ["uv", "run", "pytest", *pytest_args]
        return [sys.executable, "-m", "pytest", *pytest_args]

    def _project_test_command_source(self, command: list[str]) -> str:
        if command[:2] == ["uv", "run"]:
            args = command[2:]
            if "--all-extras" in args:
                return "pyproject_all_extras"
            if "--extra" in args:
                index = args.index("--extra")
                if index + 1 < len(args):
                    return f"pyproject_optional_{args[index + 1]}_extra"
            return "pyproject_uv"
        return "python_module_pytest"

    def _pyproject_pytest_extra(self) -> str | None:
        pyproject = self.root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        optional = data.get("project", {}).get("optional-dependencies", {})
        if not isinstance(optional, dict):
            return None
        for name, deps in optional.items():
            if isinstance(deps, list) and any("pytest" in str(dep).lower() for dep in deps):
                return str(name)
        return None

    def _safe_path(self, raw_path: str | None) -> Path:
        normalized = self.policy_gate.normalize_repo_path(raw_path or ".")
        if normalized is None:
            raise ToolExecutionError(f"path escapes repo root: {raw_path}")
        return (self.root / normalized).resolve()

    def _display(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix() or "."
        except ValueError:
            return path.as_posix()

    def _is_ignored_path(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.root)
        except ValueError:
            return True
        return any(part in IGNORED_DIR_NAMES for part in relative.parts)


_TOOL_DESCRIPTIONS = {
    "repo_snapshot": "Return compact repository inventory, common config files, git status, test candidates, and empty-repo signal.",
    "list_dir": "List direct children under a repository path with file/dir types.",
    "read_file": "Read one UTF-8 text file under repo root; missing files return structured not_found instead of crashing.",
    "read_many_files": "Read several UTF-8 files in one bounded call.",
    "file_search": "Find repository files by glob pattern.",
    "text_search": "Search repository text with rg when available and return parsed matches.",
    "json_query": "Read a JSON file and return a dotted path value.",
    "git_status": "Return git status --short.",
    "git_diff": "Return git diff for the repo or one path.",
    "diff_summary": "Return changed file names and bounded diff text, including untracked new files.",
    "mutation_scope_check": "Check changed files against phase mutation policy.",
    "classify_file_management_candidates": "Classify move/hold/unknown file-management candidates with manifest-key and destination evidence.",
    "write_file": "Write a full file inside phase mutation policy.",
    "write_many_files": "Write multiple full files inside phase mutation policy in one preflighted batch.",
    "write_json_manifest": "Primary tool for JSON manifests, indexes, inventories, and reports with exact keys/count reconciliation.",
    "apply_file_operations": "Preflight and apply move/write/replace/delete/mkdir operations with an idempotent operation ledger.",
    "replace_in_file": "Replace one exact text occurrence inside phase mutation policy.",
    "move_file": "Move one file when source and destination pass phase mutation policy.",
    "delete_file": "Delete one file inside phase mutation policy.",
    "runtime_capabilities": "Return availability/version checks for common runtimes and test tools.",
    "run_readonly_command": "Run an allowlisted readonly verification command without shell control operators.",
    "run_project_tests": "Run repository pytest using detected uv/dev extras when available.",
    "run_focused_tests": "Run pytest for selected repo-relative test paths.",
    "run_required_verification": "Run required verification and return artifact-ready test_results with command provenance.",
    "verify_file_state": "Verify required paths exist and forbidden paths are absent.",
    "verify_file_state_against_manifest": "Verify manifest keys/counts, moved destinations, source removal, and held-file preservation.",
    "scope_audit": "Alias for mutation_scope_check.",
}


def _tool_spec(*, name: str, group: str) -> dict[str, Any]:
    parameter_contract = _TOOL_PARAMETER_CONTRACTS.get(name, {})
    return {
        "name": name,
        "group": group,
        "description": _TOOL_DESCRIPTIONS.get(name, name),
        "parameters": parameter_contract.get("parameters", {}),
        "required_arguments": parameter_contract.get("required_arguments", []),
        "optional_arguments": parameter_contract.get("optional_arguments", []),
        "argument_rules": parameter_contract.get("argument_rules", []),
        "example_call": parameter_contract.get("example_call"),
        "result_highlights": parameter_contract.get("result_highlights", []),
    }


_TOOL_PARAMETER_CONTRACTS = {
    "repo_snapshot": {
        "parameters": {"path": "string"},
        "optional_arguments": ["path"],
        "argument_rules": ["Use a repo-relative directory path. Omit path or use '.' for the repo root."],
        "example_call": {"path": "."},
        "result_highlights": ["files", "directories", "is_empty", "test_candidates", "config_files"],
    },
    "list_dir": {
        "parameters": {"path": "string"},
        "optional_arguments": ["path"],
        "argument_rules": ["Use a repo-relative directory path. This lists one directory level."],
        "example_call": {"path": "docs"},
        "result_highlights": ["entries", "directories", "files"],
    },
    "read_file": {
        "parameters": {"path": "string"},
        "required_arguments": ["path"],
        "argument_rules": [
            "path must be one repo-relative file path string.",
            "Do not pass JSON schemas, arrays, or explanatory text in path.",
        ],
        "example_call": {"path": "README.md"},
        "result_highlights": ["path", "exists", "content", "size_bytes"],
    },
    "read_many_files": {
        "parameters": {"paths": "string_array"},
        "required_arguments": ["paths"],
        "argument_rules": [
            "paths must be a real JSON array of repo-relative file path strings.",
            "Use this only when you genuinely need multiple known files in one turn.",
        ],
        "example_call": {"paths": ["README.md", "docs/status.md"]},
        "result_highlights": ["files", "missing_paths"],
    },
    "file_search": {
        "parameters": {"path": "string", "pattern": "string"},
        "required_arguments": ["pattern"],
        "optional_arguments": ["path"],
        "argument_rules": ["Use path to narrow the search root whenever scope is known."],
        "example_call": {"path": ".", "pattern": "README*"},
        "result_highlights": ["matches"],
    },
    "text_search": {
        "parameters": {"path": "string", "pattern": "string"},
        "required_arguments": ["pattern"],
        "optional_arguments": ["path"],
        "argument_rules": ["pattern should be a literal string or regex-like search needle, not a paragraph."],
        "example_call": {"path": "src", "pattern": "Current status"},
        "result_highlights": ["matches", "match_count"],
    },
    "json_query": {
        "parameters": {"path": "string", "query": "string"},
        "required_arguments": ["path", "query"],
        "argument_rules": ["path must point to one JSON file. query should be a focused jq-style selection string."],
        "example_call": {"path": "manifest.json", "query": ".items | length"},
        "result_highlights": ["result"],
    },
    "git_diff": {
        "parameters": {"path": "string"},
        "optional_arguments": ["path"],
        "argument_rules": ["Use path only to narrow the diff to one file or subtree."],
        "example_call": {"path": "README.md"},
        "result_highlights": ["stdout"],
    },
    "diff_summary": {
        "parameters": {"path": "string"},
        "optional_arguments": ["path"],
        "argument_rules": ["Use after mutation when a compact diff view is enough."],
        "example_call": {"path": "README.md"},
        "result_highlights": ["summary", "changed_files"],
    },
    "classify_file_management_candidates": {
        "parameters": {"path": "string", "required_keys": "string_array"},
        "optional_arguments": ["path", "required_keys"],
        "argument_rules": ["Use for archive/cleanup/reorg tasks when you need deterministic candidate classification before writes."],
        "example_call": {"path": ".", "required_keys": ["archive", "hold", "unknown"]},
        "result_highlights": ["candidates", "held_items", "unknowns", "destination_hints"],
    },
    "write_file": {
        "parameters": {"path": "string", "content": "string", "overwrite": "boolean"},
        "required_arguments": ["path", "content"],
        "optional_arguments": ["overwrite"],
        "argument_rules": ["Use one repo-relative file path.", "content must be the full final file content for that path."],
        "example_call": {"path": "README.md", "content": "# Title\n", "overwrite": True},
        "result_highlights": ["status", "touched_paths", "changed_paths", "operations"],
    },
    "write_many_files": {
        "parameters": {"files": "array<{path, content, overwrite}>"},
        "required_arguments": ["files"],
        "argument_rules": ["files must be a JSON array of objects with path and content.", "Keep batches small and policy-safe."],
        "example_call": {"files": [{"path": "docs/report.md", "content": "# Report\n", "overwrite": True}]},
        "result_highlights": ["status", "touched_paths", "changed_paths", "operations"],
    },
    "write_json_manifest": {
        "parameters": {"path": "string", "payload": "json_object", "required_keys": "string_array", "total_key": "string", "count_keys": "string_array"},
        "required_arguments": ["path", "payload"],
        "optional_arguments": ["required_keys", "total_key", "count_keys"],
        "argument_rules": ["payload must be a real JSON object, not a string.", "Use required_keys/count_keys when manifest structure matters."],
        "example_call": {"path": "archive/index.json", "payload": {"items": []}, "required_keys": ["items"]},
        "result_highlights": ["status", "touched_paths", "manifest_validation"],
    },
    "apply_file_operations": {
        "parameters": {"operations": "array<{action,path,source,destination,content,old,new,overwrite}>"},
        "required_arguments": ["operations"],
        "argument_rules": ["operations must be a JSON array.", "Each action must be one of write, replace, move, delete, mkdir."],
        "example_call": {"operations": [{"action": "move", "source": "notes/todo.md", "destination": "archive/todo.md", "overwrite": True}]},
        "result_highlights": ["status", "touched_paths", "changed_paths", "operations"],
    },
    "replace_in_file": {
        "parameters": {"path": "string", "old": "string", "new": "string"},
        "required_arguments": ["path", "old", "new"],
        "argument_rules": ["Use when one targeted replacement is safer than rewriting the whole file.", "old must be non-empty and present in the current file."],
        "example_call": {"path": "README.md", "old": "TODO", "new": "Done"},
        "result_highlights": ["status", "touched_paths", "operations"],
    },
    "move_file": {
        "parameters": {"source": "string", "destination": "string", "overwrite": "boolean"},
        "required_arguments": ["source", "destination"],
        "optional_arguments": ["overwrite"],
        "argument_rules": ["Use repo-relative source and destination file paths."],
        "example_call": {"source": "notes/a.md", "destination": "archive/a.md", "overwrite": True},
        "result_highlights": ["status", "touched_paths", "operations"],
    },
    "delete_file": {
        "parameters": {"path": "string"},
        "required_arguments": ["path"],
        "argument_rules": ["Delete only when the phase policy explicitly allows it."],
        "example_call": {"path": "tmp/obsolete.log"},
        "result_highlights": ["status", "touched_paths", "operations"],
    },
    "run_readonly_command": {
        "parameters": {"command": "string_or_string_array"},
        "required_arguments": ["command"],
        "argument_rules": ["Use only short readonly verification commands supported by the runtime allowlist."],
        "example_call": {"command": ["python", "-m", "pytest", "-q"]},
        "result_highlights": ["returncode", "stdout", "stderr", "command"],
    },
    "run_project_tests": {
        "parameters": {"paths": "string_or_string_array"},
        "optional_arguments": ["paths"],
        "argument_rules": ["Use for broad pytest verification when the repository is Python-based."],
        "example_call": {"paths": ["tests"]},
        "result_highlights": ["returncode", "stdout", "stderr", "command"],
    },
    "run_focused_tests": {
        "parameters": {"paths": "string_or_string_array"},
        "required_arguments": ["paths"],
        "argument_rules": ["Use repo-relative test paths or file paths only."],
        "example_call": {"paths": ["tests/test_api.py"]},
        "result_highlights": ["returncode", "stdout", "stderr", "command"],
    },
    "run_required_verification": {
        "parameters": {"paths": "string_or_string_array"},
        "optional_arguments": ["paths"],
        "argument_rules": ["Use when the phase verification policy expects explicit test or command evidence."],
        "example_call": {"paths": ["tests/test_api.py"]},
        "result_highlights": ["status", "test_results", "command_evidence"],
    },
    "verify_file_state": {
        "parameters": {"required_paths": "string_array", "forbidden_paths": "string_array"},
        "optional_arguments": ["required_paths", "forbidden_paths"],
        "argument_rules": ["Use deterministic file-state checks when command execution is unnecessary."],
        "example_call": {"required_paths": ["archive/report.md"], "forbidden_paths": ["inbox/report.md"]},
        "result_highlights": ["status", "missing_paths", "unexpected_paths"],
    },
    "verify_file_state_against_manifest": {
        "parameters": {"manifest_path": "string", "required_keys": "string_array", "move_pairs": "array", "held_paths": "string_array"},
        "required_arguments": ["manifest_path"],
        "optional_arguments": ["required_keys", "move_pairs", "held_paths"],
        "argument_rules": ["Use for file-management tasks where manifest integrity is part of acceptance.", "move_pairs must contain expected source/destination objects."],
        "example_call": {"manifest_path": "archive/index.json", "required_keys": ["items"], "held_paths": ["notes/keep.md"]},
        "result_highlights": ["status", "manifest_checks", "move_checks", "held_checks"],
    },
}


def _denied_result(code: str, message: str, *, repairable: bool, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "denied", "code": code, "message": message, "repairable": repairable, "metadata": metadata or {}}


def _coerce_file_operations(value: Any) -> list[FileOperation]:
    operations = []
    for item in _object_list(value):
        action = str(item.get("action") or item.get("op") or "").strip().lower()
        if action in {"create_directory", "create_dir", "mkdir"}:
            action = "mkdir"
        if action not in {"write", "replace", "move", "delete", "mkdir"}:
            raise ToolExecutionError(f"unsupported file operation action: {action}")
        data = dict(item)
        data["action"] = action
        if action == "move":
            data["source"] = data.get("source") or data.get("from")
            data["destination"] = data.get("destination") or data.get("to") or data.get("target")
        if action in {"write", "replace", "delete", "mkdir"}:
            data["path"] = data.get("path") or data.get("file") or data.get("directory")
        if action == "write" and "overwrite" not in data:
            data["overwrite"] = True
        operations.append(FileOperation.model_validate(data))
    if not operations:
        raise ToolExecutionError("apply_file_operations requires a non-empty operations array")
    return operations


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _required_json_keys(phase: PhaseStep, arguments: dict[str, Any]) -> list[str]:
    for source in (arguments.get("required_keys"), phase.metadata.get("required_json_keys"), phase.policy.get("required_json_keys")):
        keys = _string_list(source)
        if keys:
            return keys
    literal_keys = []
    for literal in phase.metadata.get("literal_contract") or []:
        if isinstance(literal, dict) and literal.get("kind") == "json_key":
            literal_keys.append(str(literal.get("value")))
    return literal_keys


def _phase_text(phase: PhaseStep) -> str:
    parts = [
        phase.goal,
        " ".join(phase.instructions),
        " ".join(phase.acceptance_checks),
        json.dumps(phase.policy, sort_keys=True, default=str),
        json.dumps(phase.metadata, sort_keys=True, default=str),
    ]
    return " ".join(parts)


def _operation_display_paths(registry: ToolRegistry, operations: list[FileOperation]) -> list[str]:
    paths = []
    for operation in operations:
        for value in (operation.path, operation.source, operation.destination):
            if value:
                paths.append(registry._display(registry._safe_path(value)))
    return sorted(set(paths))


def _looks_like_test_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}"


def _parse_rg_matches(stdout: Any) -> list[dict[str, Any]]:
    matches = []
    for line in str(stdout or "").splitlines():
        path, sep, rest = line.partition(":")
        if not sep:
            continue
        line_no, sep, text = rest.partition(":")
        if not sep:
            continue
        try:
            line_number = int(line_no)
        except ValueError:
            line_number = 0
        matches.append({"path": path, "line": line_number, "text": text[:240]})
    return matches


def _normalize_readonly_command(command: list[str]) -> tuple[dict[str, str], list[str]]:
    if not command:
        raise ToolExecutionError("run_readonly_command requires a non-empty command")
    if command[0] == "env":
        command = command[1:]
        if command and command[0].startswith("-"):
            raise ToolExecutionError("env options are not allowed in readonly commands")
        return _split_env_assignments(command)
    if len(command) == 3 and command[0] == "sh" and command[1] == "-c":
        inner = command[2]
        if _contains_shell_control(inner):
            raise ToolExecutionError("shell control operators are not allowed in readonly commands")
        return _split_env_assignments(shlex.split(inner))
    return _split_env_assignments(command)


def _split_env_assignments(command: list[str]) -> tuple[dict[str, str], list[str]]:
    env_overrides: dict[str, str] = {}
    while command and "=" in command[0] and not command[0].startswith("="):
        key, value = command[0].split("=", 1)
        if key != "PYTHONPATH":
            raise ToolExecutionError(f"environment override is not allowlisted: {key}")
        env_overrides[key] = value
        command = command[1:]
    return env_overrides, command


def _contains_shell_control(command: str) -> bool:
    return any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$("))


def _is_allowlisted(command: list[str]) -> bool:
    if not command:
        return False
    if command[0] in {"rg", "grep", "jq"}:
        return True
    if command[0] == "git" and len(command) >= 2:
        return command[1] in {"status", "diff", "show", "log"}
    if command[:2] == ["uv", "run"]:
        return _is_allowed_uv_pytest_command(command[2:])
    if command[0] == "pytest":
        return True
    executable = Path(command[0]).name
    if executable in {"python", "python3"} and command[1:3] == ["-m", "pytest"]:
        return True
    return False


def _is_allowed_uv_pytest_command(args: list[str]) -> bool:
    if not args:
        return False
    allowed_uv_options = {"--extra", "--all-extras", "--with", "--group"}
    index = 0
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        if option not in allowed_uv_options:
            return False
        index += 1
        if option in {"--extra", "--with", "--group"}:
            if index >= len(args):
                return False
            index += 1
    return index < len(args) and args[index] == "pytest"


def _uv_arguments_select_extra(args: list[str]) -> bool:
    return "--extra" in args or "--all-extras" in args or "--with" in args or "--group" in args


def _uv_run_option_prefix_length(args: list[str]) -> int:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        index += 1
        if option in {"--extra", "--with", "--group"} and index < len(args):
            index += 1
    return index


def _normalize_required_verification_result(result: dict[str, Any]) -> dict[str, Any]:
    returncode = int(result.get("returncode", 0) or 0)
    command_result = {
        "command": result.get("command"),
        "returncode": returncode,
        "stdout": str(result.get("stdout") or "")[-4000:],
        "stderr": str(result.get("stderr") or "")[-4000:],
        "detected_command_source": result.get("detected_command_source"),
    }
    return {"status": "passed" if returncode == 0 else "failed", "commands": [command_result], "failed_commands": [command_result] if returncode != 0 else [], "returncode": returncode, "source_tool": "run_required_verification"}


def _infer_manifest_total_key(*, required_keys: list[str], payload: dict[str, Any]) -> str:
    for key in required_keys:
        if key.startswith("total_"):
            return key
    for key, value in payload.items():
        if str(key).startswith("total_") and isinstance(value, int):
            return str(key)
    return ""


def _infer_manifest_count_keys(*, required_keys: list[str], payload: dict[str, Any], total_key: str) -> list[str]:
    count_keys = [key for key in required_keys if key != total_key and not key.startswith("total_") and key not in {"held_items", "skipped", "ignored", "preserved", "excluded"}]
    if count_keys:
        return [key for key in count_keys if isinstance(payload.get(key), list)]
    return [str(key) for key, value in payload.items() if key != total_key and isinstance(value, list)]


def _count_keys(required_keys: list[str]) -> list[str]:
    return [key for key in required_keys if not key.startswith("total_") and key not in {"held_items", "skipped", "ignored", "preserved", "excluded"}]


def _total_key(required_keys: list[str]) -> str | None:
    return next((key for key in required_keys if key.startswith("total_")), None)


def _infer_total_key_from_payload(payload: dict[str, Any]) -> str | None:
    return next((str(key) for key, value in payload.items() if str(key).startswith("total_") and isinstance(value, int)), None)


def _infer_count_keys_from_payload(payload: dict[str, Any], *, total_key: str | None) -> list[str]:
    return [str(key) for key, value in payload.items() if key != total_key and isinstance(value, list)]


def _move_pairs_from_args_or_phase(phase: PhaseStep, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for source in (arguments.get("move_pairs"), phase.policy.get("move_pairs"), phase.metadata.get("move_pairs")):
        for item in _object_list(source):
            if item.get("source") and item.get("destination"):
                pairs.append({"source": str(item["source"]), "destination": str(item["destination"])})
    return pairs


def _destination_map(*, text_context: str, required_keys: list[str]) -> dict[str, str]:
    explicit = {
        "moved_documents": _first_path(text_context, ("records/policies", "docs", "documents")),
        "moved_evidence": _first_path(text_context, ("records/evidence", "artifacts/evidence", "evidence")),
        "moved_json_artifacts": _first_path(text_context, ("records/evidence", "artifacts/logs", "artifacts/json")),
        "moved_logs": _first_path(text_context, ("records/logs", "artifacts/logs", "logs")),
        "moved_exports": _first_path(text_context, ("records/exports", "exports")),
    }
    defaults = {
        "moved_documents": "docs",
        "moved_evidence": "records/evidence" if "moved_evidence" in required_keys else "artifacts/logs",
        "moved_json_artifacts": "artifacts/logs",
        "moved_logs": "artifacts/logs",
        "moved_exports": "records/exports" if "moved_exports" in required_keys else "exports",
    }
    return {key: explicit.get(key) or defaults[key] for key in defaults if key in required_keys or key in {"moved_documents", "moved_logs", "moved_json_artifacts"}}


def _first_path(text: str, candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in text), None)


def _manifest_key_for_extension(*, extension: str, required_keys: list[str]) -> str | None:
    if extension in {".md", ".markdown"}:
        return "moved_documents" if "moved_documents" in required_keys or not required_keys else None
    if extension == ".log":
        return "moved_logs" if "moved_logs" in required_keys or not required_keys else None
    if extension == ".json":
        if "moved_evidence" in required_keys:
            return "moved_evidence"
        if "moved_json_artifacts" in required_keys or not required_keys:
            return "moved_json_artifacts"
    if extension == ".csv" and "moved_exports" in required_keys:
        return "moved_exports"
    return None


def _category_for_key(key: str) -> str:
    return {"moved_documents": "markdown", "moved_logs": "log", "moved_evidence": "json_evidence", "moved_json_artifacts": "json_artifact", "moved_exports": "csv_export"}.get(key, "other")


def _hold_marker(path: Path, *, root: Path) -> str | None:
    relative = _display_path(root, path).lower()
    for marker in HOLD_MARKERS:
        if marker in relative:
            return marker
    if path.is_file() and path.stat().st_size <= 20_000:
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        for marker in HOLD_MARKERS:
            if marker in content:
                return marker
    return None


def _iter_files(start: Path) -> list[Path]:
    if start.is_file():
        return [start]
    files = []
    for path in sorted(start.rglob("*")):
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


def _parse_git_status_path(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    candidate = stripped[3:] if len(stripped) > 3 else stripped
    if " -> " in candidate:
        candidate = candidate.split(" -> ", 1)[1]
    return candidate.strip() or None


def _normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_path(str(value))
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
