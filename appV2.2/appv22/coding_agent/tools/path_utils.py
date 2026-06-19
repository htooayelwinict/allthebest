"""Path helpers. Port of pi tools/path-utils.ts (subset)."""

from __future__ import annotations

import os


def resolve_to_cwd(path: str, cwd: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd, path))


def format_path_relative_to_cwd(path: str, cwd: str) -> str:
    try:
        rel = os.path.relpath(path, cwd)
    except ValueError:
        return path
    return rel if not rel.startswith("..") else path
