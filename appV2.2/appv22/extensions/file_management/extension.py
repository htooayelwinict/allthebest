from __future__ import annotations

import json
import re
from typing import Any

from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL
from appv22.extensions.file_management.tools import register_file_management_tools


class FileManagementExtension:
    extension_id = "file_management"

    def skill_cards(self):
        return [FILE_MANAGEMENT_SKILL]

    def register_tools(self, registry) -> None:
        register_file_management_tools(registry)

    def tool_result_guidance(self, result: dict[str, Any]) -> str:
        tool_id = result.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id.startswith("file_management."):
            return ""
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        suggested_path = payload.get("suggested_path")
        if isinstance(suggested_path, str) and any("existing_file_requires_overwrite" in str(error) for error in errors):
            return (
                f"{tool_id} reported an existing target and suggested {suggested_path!r}; "
                "use the suggested safe alternate path unless the user explicitly requested overwrite."
            )
        if any("protected_path" in str(error) for error in errors):
            return (
                f"{tool_id} reported a protected path; do not retry that path, "
                "and continue using non-protected workspace evidence."
            )
        return ""

    def finalize_guidance(self, state) -> str:
        goal = state.request.user_goal.lower()
        completed_results = [
            result
            for result in state.tool_results.values()
            if isinstance(result, dict)
            and result.get("status") == "completed"
            and isinstance(result.get("tool_id"), str)
            and result["tool_id"].startswith("file_management.")
        ]
        if _requires_file_creation(goal) and not any(_is_file_change_result(result) for result in completed_results):
            read_paths = _completed_read_paths(completed_results)
            if read_paths:
                target_path = _suggest_creation_path(goal)
                return (
                    "A file creation goal has completed source reads but no workspace file has been written; "
                    "the next decision must be a tool_call to file_management.write_file "
                    f"with path {target_path!r}, overwrite=false, and content composed from read evidence: "
                    f"{', '.join(read_paths)}. Preserve exact requested facts and exclude obsolete or do-not-use facts."
                )
        if "record" not in goal:
            return ""
        separate_record_required = _requires_separate_record(goal)
        if not any(_is_file_change_result(result) for result in completed_results):
            return ""
        record_results = [
            result
            for result in completed_results
            if _is_record_write_result(result, separate_record_required=separate_record_required)
        ]
        if not record_results:
            record_path = _suggest_record_path(state)
            changed_paths = _changed_paths(completed_results)
            return (
                "A file-work record was requested and file changes already occurred; "
                "the next decision must be a tool_call to file_management.write_file "
                f"with path {record_path!r}, overwrite=false, and content listing changed paths: "
                f"{', '.join(changed_paths) or 'none'}."
            )
        if not separate_record_required:
            return ""
        unresolved_winners = _manifest_unresolved_winners(state, completed_results, record_results)
        if unresolved_winners:
            details = ", ".join(
                f"{source} -> {destination}" if destination else source
                for source, destination in unresolved_winners
            )
            first_source, first_destination = unresolved_winners[0]
            destination_instruction = (
                f" and destination {first_destination!r}"
                if first_destination
                else " and the destination claimed in the manifest"
            )
            return (
                "The requested file-work manifest names unresolved winning sources that were not actually moved: "
                f"{details}. The next decision must be a tool_call to file_management.move_file "
                f"with source {first_source!r}{destination_instruction}; do not emit finalize or compact. "
                "After that tool call completes, continue resolving any remaining winners."
            )
        snapshot_unresolved_winners = _snapshot_unresolved_winners(state, completed_results)
        if snapshot_unresolved_winners:
            details = ", ".join(
                f"{source} -> {destination}" if destination else source
                for source, destination in snapshot_unresolved_winners
            )
            first_source, first_destination = snapshot_unresolved_winners[0]
            destination_instruction = (
                f" and destination {first_destination!r}"
                if first_destination
                else " and the destination identified by snapshot evidence"
            )
            return (
                "snapshot evidence contains unresolved winning sources that were not actually moved: "
                f"{details}. The next decision must be a tool_call to file_management.move_file "
                f"with source {first_source!r}{destination_instruction}; do not emit finalize or compact. "
                "After that tool call completes, continue resolving any remaining winners."
            )
        unresolved_deletions = _manifest_unresolved_deletions(state, completed_results, record_results)
        if unresolved_deletions:
            details = ", ".join(unresolved_deletions)
            first_path = unresolved_deletions[0]
            return (
                "The requested file-work manifest names unresolved deletions that were not actually deleted: "
                f"{details}. The next decision must be a tool_call to file_management.delete_file "
                f"with path {first_path!r}; do not emit finalize or compact. "
                "After that tool call completes, continue resolving any remaining deletions."
            )
        missing_paths = _changed_paths_missing_from_records(state, completed_results, record_results)
        if missing_paths:
            record_path = _record_result_path(record_results[-1]) or _suggest_record_path(state)
            return (
                "The requested file-work record is missing changed paths: "
                f"{', '.join(missing_paths)}. The next decision must be a tool_call to "
                "file_management.write_file "
                f"with path {record_path!r}, overwrite=true, and content including the missing changed paths; "
                "do not emit finalize or compact."
            )
        return ""


def _is_file_change_result(result: dict[str, Any]) -> bool:
    tool_id = result.get("tool_id")
    if tool_id in {
        "file_management.write_file",
        "file_management.move_file",
        "file_management.copy_file",
        "file_management.delete_file",
    }:
        return True
    return False


def _requires_file_creation(goal: str) -> bool:
    return any(
        marker in goal
        for marker in (
            "create",
            "make",
            "write",
            "draft",
            "produce",
            "handoff file",
            "brief file",
            "packet",
        )
    ) and any(marker in goal for marker in ("file", "brief", "handoff", "packet", "note"))


def _completed_read_paths(completed_results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for result in completed_results:
        if result.get("tool_id") != "file_management.read_file":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        path = payload.get("path")
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def _suggest_creation_path(goal: str) -> str:
    if "handoff" in goal:
        return "docs/handoff.md"
    if "packet" in goal:
        return "docs/delivery_packet.md"
    if "brief" in goal:
        return "docs/brief.md"
    return "docs/output.md"


def _requires_separate_record(goal: str) -> bool:
    return any(
        marker in goal
        for marker in (
            "keep a record",
            "record of",
            "record what",
            "record the",
            "manifest",
        )
    )


def _is_record_write_result(result: dict[str, Any], *, separate_record_required: bool) -> bool:
    if result.get("tool_id") != "file_management.write_file":
        return False
    if not separate_record_required:
        return True
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    path = str(payload.get("path", "")).lower()
    return any(marker in path for marker in ("manifest", "record", "log"))


def _record_result_path(result: dict[str, Any]) -> str | None:
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    value = payload.get("path")
    return value if isinstance(value, str) and value else None


def _changed_paths_missing_from_records(state, completed_results: list[dict[str, Any]], record_results: list[dict[str, Any]]) -> list[str]:
    changed_paths = _changed_paths(completed_results)
    record_text = _record_text(state, completed_results, record_results)
    return [path for path in changed_paths if path not in record_text and path not in _record_paths(completed_results, record_results)]


def _manifest_unresolved_winners(
    state,
    completed_results: list[dict[str, Any]],
    record_results: list[dict[str, Any]],
) -> list[tuple[str, str | None]]:
    moved_sources = _moved_or_copied_sources(completed_results)
    unresolved: list[tuple[str, str | None]] = []
    for manifest in _record_manifests(state, completed_results, record_results):
        destination_by_winner = _claimed_destinations_by_winner(manifest)
        collisions = manifest.get("collisions") if isinstance(manifest.get("collisions"), list) else []
        for collision in collisions:
            if not isinstance(collision, dict):
                continue
            winner = collision.get("winner")
            if not isinstance(winner, str) or not winner or winner in moved_sources:
                continue
            if not _source_still_exists(state, winner):
                continue
            unresolved.append((winner, destination_by_winner.get(winner)))
    return unresolved


def _snapshot_unresolved_winners(state, completed_results: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    moved_sources = _moved_or_copied_sources(completed_results)
    unresolved: list[tuple[str, str | None]] = []
    for snapshot in _repo_snapshot_payloads(completed_results):
        previews = snapshot.get("text_previews") if isinstance(snapshot.get("text_previews"), dict) else {}
        held_destinations = {
            destination
            for preview in previews.values()
            for destination in [_held_claimed_destination(str(preview))]
            if destination
        }
        if not held_destinations:
            continue
        for source, preview in previews.items():
            if not isinstance(source, str) or source in moved_sources:
                continue
            destination = _move_intent_destination(source, str(preview))
            if not destination or destination not in held_destinations:
                continue
            if not _source_still_exists(state, source):
                continue
            candidate = (source, destination)
            if candidate not in unresolved:
                unresolved.append(candidate)
    return unresolved


def _repo_snapshot_payloads(completed_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in completed_results:
        if result.get("tool_id") != "file_management.repo_snapshot" or result.get("status") != "completed":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        payloads.append(payload)
    return payloads


def _held_claimed_destination(preview: str) -> str | None:
    if "hold" not in preview.lower() or "claimed" not in preview.lower():
        return None
    match = re.search(r"because\s+(?P<destination>[\w./-]+)\s+is\s+claimed", preview, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group("destination").rstrip(".,;:")


def _move_intent_destination(source: str, preview: str) -> str | None:
    if "move" not in preview.lower():
        return None
    match = re.search(r"\binto\s+(?P<destination>[\w./-]+)", preview, flags=re.IGNORECASE)
    if not match:
        return None
    destination = match.group("destination").rstrip(".,;:")
    if "." not in destination.rsplit("/", 1)[-1]:
        filename = source.rsplit("/", 1)[-1]
        destination = f"{destination.rstrip('/')}/{filename}"
    return destination


def _manifest_unresolved_deletions(
    state,
    completed_results: list[dict[str, Any]],
    record_results: list[dict[str, Any]],
) -> list[str]:
    deleted_paths = _deleted_paths(completed_results)
    unresolved: list[str] = []
    for manifest in _record_manifests(state, completed_results, record_results):
        for entry in _manifest_deletion_entries(manifest):
            path = entry.get("path") if isinstance(entry, dict) else entry
            if not isinstance(path, str) or not path or path in deleted_paths:
                continue
            if not _source_still_exists(state, path):
                continue
            if path not in unresolved:
                unresolved.append(path)
    return unresolved


def _manifest_deletion_entries(manifest: dict[str, Any]) -> list[Any]:
    entries: list[Any] = []
    for key in ("deletions", "deleted", "deletes"):
        value = manifest.get(key)
        if isinstance(value, list):
            entries.extend(value)
    return entries


def _record_manifests(
    state,
    completed_results: list[dict[str, Any]],
    record_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for text in _record_texts(state, completed_results, record_results):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            manifests.append(value)
    return manifests


def _claimed_destinations_by_winner(manifest: dict[str, Any]) -> dict[str, str]:
    destinations: dict[str, str] = {}
    held = manifest.get("held") if isinstance(manifest.get("held"), list) else []
    for entry in held:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str):
            continue
        match = re.search(r"(?P<destination>\S+)\s+is claimed by\s+(?P<winner>\S+)", reason)
        if match:
            destinations[match.group("winner")] = match.group("destination")
    return destinations


def _moved_or_copied_sources(completed_results: list[dict[str, Any]]) -> set[str]:
    sources: set[str] = set()
    for result in completed_results:
        if result.get("tool_id") not in {"file_management.move_file", "file_management.copy_file"}:
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        source = payload.get("source")
        if isinstance(source, str):
            sources.add(source)
    return sources


def _deleted_paths(completed_results: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for result in completed_results:
        if result.get("tool_id") != "file_management.delete_file":
            continue
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        path = payload.get("path")
        if isinstance(path, str):
            paths.add(path)
    return paths


def _source_still_exists(state, source: str) -> bool:
    from pathlib import Path

    return (Path(state.request.root_path) / source).exists()


def _record_paths(completed_results: list[dict[str, Any]], record_results: list[dict[str, Any]]) -> list[str]:
    record_paths: list[str] = []
    for result in completed_results:
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if result in record_results:
            value = payload.get("path")
            if isinstance(value, str):
                record_paths.append(value)
    return record_paths


def _record_text(state, completed_results: list[dict[str, Any]], record_results: list[dict[str, Any]]) -> str:
    return "\n".join(_record_texts(state, completed_results, record_results))


def _record_texts(state, completed_results: list[dict[str, Any]], record_results: list[dict[str, Any]]) -> list[str]:
    record_texts: list[str] = []
    record_text = ""
    root = state.request.root_path
    for relative in _record_paths(completed_results, record_results):
        path = (state.request_path_root / relative) if hasattr(state, "request_path_root") else None
        if path is None:
            from pathlib import Path

            path = Path(root) / relative
        try:
            record_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        record_texts.append(record_text)
    return record_texts


def _changed_paths(completed_results: list[dict[str, Any]]) -> list[str]:
    changed_paths: list[str] = []
    for result in completed_results:
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        tool_id = result.get("tool_id")
        if tool_id in {"file_management.move_file", "file_management.copy_file"}:
            for key in ("source", "destination"):
                value = payload.get(key)
                if isinstance(value, str) and value not in changed_paths:
                    changed_paths.append(value)
        elif tool_id in {"file_management.delete_file", "file_management.write_file"}:
            value = payload.get("path")
            if isinstance(value, str) and value not in changed_paths:
                changed_paths.append(value)
    return changed_paths


def _suggest_record_path(state) -> str:
    from pathlib import Path

    root = Path(state.request.root_path)
    base = Path("docs/workspace_manifest.json")
    if not (root / base).exists():
        return base.as_posix()
    for index in range(1, 100):
        candidate = Path(f"docs/workspace_manifest-{index}.json")
        if not (root / candidate).exists():
            return candidate.as_posix()
    return "docs/workspace_manifest-new.json"
