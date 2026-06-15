from __future__ import annotations

from pathlib import Path

from appv22.extensions.file_management.mutation_policy import _outside
from appv22.tools.definitions import ToolDefinition


def register_file_management_tools(registry) -> None:
    registry.register(
        ToolDefinition(
            "file_management.repo_snapshot",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {
                "type": "object",
                "properties": {
                    "files": {"type": "array"},
                    "directories": {"type": "array"},
                },
                "required": ["files", "directories"],
            },
            "runtime_observed",
            "Return workspace files and directories relative to the root.",
        ),
        repo_snapshot,
    )
    registry.register(
        ToolDefinition(
            "file_management.read_file",
            "observe",
            "low",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            "runtime_observed",
            "Read a workspace file by relative path.",
        ),
        read_file,
    )


def repo_snapshot(_args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    files: list[str] = []
    directories: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_file():
            files.append(relative)
        elif path.is_dir():
            directories.append(relative)
    return {"status": "completed", "files": sorted(files), "directories": sorted(directories)}


def read_file(args: dict, context: dict) -> dict:
    root = Path(context["root_path"]).resolve()
    relative = str(args.get("path", ""))
    if _outside(root, relative):
        return {"status": "denied", "errors": [f"path_outside_root:{relative}"]}
    path = root / relative
    if not path.is_file():
        return {"status": "failed", "errors": [f"missing_file:{relative}"]}
    return {"status": "completed", "path": relative, "content": path.read_text(encoding="utf-8")}
