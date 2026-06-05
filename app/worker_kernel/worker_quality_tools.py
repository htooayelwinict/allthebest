"""Deterministic helper logic for worker-quality tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas import Task


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


def classify_file_management_candidates(
    *,
    root: Path,
    task: Task,
    path: str = ".",
) -> dict[str, Any]:
    """Classify common file-management candidates with deterministic evidence."""

    start = _resolve(root, path or ".")
    if not start.exists():
        return {
            "status": "failed",
            "path": _display(root, start),
            "candidates": [],
            "held_items": [],
            "unknown_items": [],
            "error": "path_not_found",
        }

    text_context = _task_text(task)
    required_keys = _required_json_keys(task)
    destination_map = _destination_map(text_context=text_context, required_keys=required_keys)
    total_key = _total_key(required_keys)
    files = _iter_files(start)
    candidates: list[dict[str, Any]] = []
    held_items: list[dict[str, Any]] = []
    unknown_items: list[dict[str, Any]] = []

    for file_path in files:
        relative = _display(root, file_path)
        extension = file_path.suffix.lower()
        marker = _hold_marker(root=root, path=file_path)
        if marker:
            held_items.append(
                {
                    "path": relative,
                    "reason": f"held by marker: {marker}",
                    "evidence": [{"kind": "hold_marker", "value": marker}],
                }
            )
            continue

        manifest_key = _manifest_key_for_extension(extension=extension, required_keys=required_keys)
        if manifest_key is None:
            if _looks_like_workspace_artifact(extension):
                unknown_items.append(
                    {
                        "path": relative,
                        "extension": extension,
                        "reason": "extension has no explicit manifest/category rule",
                        "evidence": [{"kind": "extension", "value": extension}],
                    }
                )
            continue

        destination_dir = destination_map.get(manifest_key)
        if not destination_dir:
            unknown_items.append(
                {
                    "path": relative,
                    "extension": extension,
                    "manifest_key": manifest_key,
                    "reason": "no destination rule inferred for manifest key",
                    "evidence": [{"kind": "manifest_key", "value": manifest_key}],
                }
            )
            continue

        destination = f"{destination_dir.rstrip('/')}/{file_path.name}"
        if _normalize_path(relative) == _normalize_path(destination):
            held_items.append(
                {
                    "path": relative,
                    "reason": "already in inferred destination",
                    "evidence": [{"kind": "destination", "value": destination_dir}],
                }
            )
            continue

        candidates.append(
            {
                "source": relative,
                "destination": destination,
                "category": _category_for_key(manifest_key),
                "manifest_key": manifest_key,
                "basename": file_path.name,
                "reason": f"{extension or 'no-extension'} file matches {manifest_key}",
                "evidence": [
                    {"kind": "extension", "value": extension},
                    {"kind": "destination", "value": destination_dir},
                ],
            }
        )

    payload_seed: dict[str, Any] = {key: [] for key in _count_keys(required_keys)}
    for candidate in candidates:
        key = str(candidate["manifest_key"])
        payload_seed.setdefault(key, []).append(candidate["basename"])
    if total_key:
        payload_seed[total_key] = sum(len(value) for value in payload_seed.values() if isinstance(value, list))
    if "held_items" in required_keys:
        payload_seed["held_items"] = [Path(str(item["path"])).name for item in held_items]

    return {
        "status": "completed",
        "path": _display(root, start),
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


def verify_file_state_against_manifest(
    *,
    root: Path,
    task: Task,
    manifest_path: str,
) -> dict[str, Any]:
    """Verify file-management state and manifest consistency."""

    manifest = _resolve(root, manifest_path)
    required_keys = _required_json_keys(task)
    move_pairs = _move_pairs_from_task(task)
    held_paths = _held_paths_from_task(task)
    if not manifest.exists():
        return {
            "status": "failed",
            "manifest_path": _display(root, manifest),
            "manifest_exists": False,
            "errors": [{"code": "manifest_missing", "path": _display(root, manifest)}],
        }

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "status": "failed",
            "manifest_path": _display(root, manifest),
            "manifest_exists": True,
            "errors": [{"code": "manifest_invalid_json", "message": str(exc)}],
        }
    if not isinstance(payload, dict):
        return {
            "status": "failed",
            "manifest_path": _display(root, manifest),
            "manifest_exists": True,
            "errors": [{"code": "manifest_payload_not_object"}],
        }

    missing_fields = [key for key in required_keys if key not in payload]
    total_key = _total_key(required_keys) or _infer_total_key_from_payload(payload)
    count_keys = _count_keys(required_keys) or _infer_count_keys_from_payload(payload, total_key=total_key)
    counted_total = sum(len(payload.get(key) or []) for key in count_keys if isinstance(payload.get(key), list))
    total_value = payload.get(total_key) if total_key else None
    counts_match = total_key is None or total_value == counted_total

    move_checks: list[dict[str, Any]] = []
    for pair in move_pairs:
        source = str(pair.get("source") or "")
        destination = str(pair.get("destination") or "")
        source_path = _resolve(root, source)
        destination_path = _resolve(root, destination)
        manifest_key = _manifest_key_for_extension(
            extension=destination_path.suffix.lower(),
            required_keys=required_keys,
        )
        manifest_contains = True
        if manifest_key and isinstance(payload.get(manifest_key), list):
            values = {str(value) for value in payload[manifest_key]}
            manifest_contains = destination in values or destination_path.name in values
        passed = not source_path.exists() and destination_path.exists() and manifest_contains
        move_checks.append(
            {
                "source": source,
                "destination": destination,
                "source_exists": source_path.exists(),
                "destination_exists": destination_path.exists(),
                "manifest_key": manifest_key,
                "manifest_contains": manifest_contains,
                "passed": passed,
            }
        )

    held_checks = [
        {
            "path": path,
            "exists": _resolve(root, path).exists(),
            "passed": _resolve(root, path).exists(),
        }
        for path in held_paths
    ]
    errors: list[dict[str, Any]] = []
    if missing_fields:
        errors.append({"code": "manifest_missing_fields", "missing_fields": missing_fields})
    if not counts_match:
        errors.append(
            {
                "code": "manifest_count_mismatch",
                "total_key": total_key,
                "total_value": total_value,
                "counted_total": counted_total,
                "count_keys": count_keys,
            }
        )
    for check in move_checks:
        if not check["passed"]:
            errors.append({"code": "move_state_mismatch", **check})
    for check in held_checks:
        if not check["passed"]:
            errors.append({"code": "held_path_missing", **check})

    return {
        "status": "passed" if not errors else "failed",
        "manifest_path": _display(root, manifest),
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


def resume_from_kernel_memory(*, root: Path, task: Task) -> dict[str, Any]:
    """Return compact, tool-friendly retry memory guidance."""

    memory = task.metadata.get("kernel_memory")
    if not isinstance(memory, dict):
        return {
            "status": "no_memory",
            "already_completed_paths": [],
            "pending_required_write_paths": [],
            "denied_operations": [],
            "do_not_repeat_operations": [],
            "recommended_next_tools": [],
            "path_state": {},
        }

    operations = [
        operation
        for operation in memory.get("successful_write_operations") or []
        if isinstance(operation, dict)
    ]
    completed_paths = _dedupe(
        str(path)
        for operation in operations
        for path in operation.get("paths") or []
        if path
    )
    pending_paths = _dedupe(str(path) for path in memory.get("pending_required_write_paths") or [] if path)
    denials = [item for item in memory.get("denied_operations") or [] if isinstance(item, dict)]
    relevant_paths = _dedupe([*completed_paths, *pending_paths])
    path_state = {
        path: {
            "exists": _resolve(root, path).exists(),
            "is_file": _resolve(root, path).is_file(),
            "is_dir": _resolve(root, path).is_dir(),
        }
        for path in relevant_paths
    }
    return {
        "status": "completed",
        "step_id": memory.get("step_id"),
        "attempt_count": memory.get("attempt_count", 0),
        "already_completed_paths": completed_paths,
        "pending_required_write_paths": pending_paths,
        "denied_operations": denials,
        "do_not_repeat_operations": [
            {
                "tool_name": operation.get("tool_name"),
                "action": operation.get("action"),
                "paths": operation.get("paths") or [],
                "reason": "successful or already done in prior attempt",
            }
            for operation in operations
            if operation.get("status") in {"applied", "already_done"}
        ],
        "recommended_next_tools": _recommended_resume_tools(pending_paths=pending_paths, denials=denials),
        "retry_guidance": memory.get("retry_guidance") or [],
        "path_state": path_state,
    }


def normalize_required_verification_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a command tool result into a test_results-shaped payload."""

    returncode = int(result.get("returncode", 0) or 0)
    command = result.get("command")
    command_result = {
        "command": command,
        "returncode": returncode,
        "stdout": str(result.get("stdout") or "")[-4000:],
        "stderr": str(result.get("stderr") or "")[-4000:],
        "detected_command_source": result.get("detected_command_source"),
    }
    failed_commands = [command_result] if returncode != 0 else []
    return {
        "status": "passed" if returncode == 0 else "failed",
        "commands": [command_result],
        "failed_commands": failed_commands,
        "returncode": returncode,
        "source_tool": "run_required_verification",
    }


def _task_text(task: Task) -> str:
    parts = [
        task.instruction,
        task.metadata.get("objective"),
        task.metadata.get("normalized_input"),
        task.metadata.get("user_goal"),
        task.metadata.get("success_criteria"),
        task.metadata.get("constraints"),
        task.metadata.get("literal_contract"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _required_json_keys(task: Task) -> list[str]:
    keys = task.metadata.get("required_json_keys")
    if isinstance(keys, list):
        return [str(key) for key in keys if str(key)]
    return []


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
    return {
        key: explicit.get(key) or defaults[key]
        for key in defaults
        if key in required_keys or key in {"moved_documents", "moved_logs", "moved_json_artifacts"}
    }


def _first_path(text: str, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in text:
            return candidate
    return None


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
    return {
        "moved_documents": "markdown",
        "moved_logs": "log",
        "moved_evidence": "json_evidence",
        "moved_json_artifacts": "json_artifact",
        "moved_exports": "csv_export",
    }.get(key, "other")


def _looks_like_workspace_artifact(extension: str) -> bool:
    return extension in {".md", ".markdown", ".log", ".json", ".csv"}


def _hold_marker(*, root: Path, path: Path) -> str | None:
    relative = _display(root, path).lower()
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
    files: list[Path] = []
    for path in sorted(start.rglob("*")):
        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _move_pairs_from_task(task: Task) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for artifact in task.input_artifacts:
        pairs.extend(_move_pairs_from_value(artifact.content))
    return pairs


def _move_pairs_from_value(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    pairs: list[dict[str, Any]] = []
    raw_pairs = value.get("move_pairs")
    if isinstance(raw_pairs, list):
        for item in raw_pairs:
            if isinstance(item, dict) and item.get("source") and item.get("destination"):
                pairs.append({"source": str(item["source"]), "destination": str(item["destination"])})
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict) and item.get("source") and item.get("destination"):
                pairs.append({"source": str(item["source"]), "destination": str(item["destination"])})
    return pairs


def _held_paths_from_task(task: Task) -> list[str]:
    paths: list[str] = []
    for artifact in task.input_artifacts:
        content = artifact.content
        if not isinstance(content, dict):
            continue
        for key in ("held_items", "excluded_from_moves", "excluded_paths"):
            raw = content.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, dict) and item.get("path"):
                    paths.append(str(item["path"]))
                elif isinstance(item, str):
                    paths.append(item)
    return _dedupe(paths)


def _count_keys(required_keys: list[str]) -> list[str]:
    return [key for key in required_keys if not key.startswith("total_") and key not in {"held_items"}]


def _total_key(required_keys: list[str]) -> str | None:
    for key in required_keys:
        if key.startswith("total_"):
            return key
    return None


def _infer_total_key_from_payload(payload: dict[str, Any]) -> str | None:
    for key, value in payload.items():
        if key.startswith("total_") and isinstance(value, int):
            return str(key)
    return None


def _infer_count_keys_from_payload(payload: dict[str, Any], *, total_key: str | None) -> list[str]:
    return [str(key) for key, value in payload.items() if key != total_key and isinstance(value, list)]


def _recommended_resume_tools(*, pending_paths: list[str], denials: list[dict[str, Any]]) -> list[str]:
    tools: list[str] = []
    if pending_paths:
        tools.extend(["read_file", "apply_file_operations"])
    if any(path.endswith(".json") for path in pending_paths):
        tools.insert(0, "write_json_manifest")
    if denials:
        tools.append("resume_from_kernel_memory")
    return _dedupe(tools)


def _resolve(root: Path, value: str) -> Path:
    root = root.resolve()
    path = (root / str(value or ".")).resolve()
    path.relative_to(root)
    return path


def _display(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


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
