from __future__ import annotations

from pathlib import Path

PROTECTED_PREFIXES = (".git/", "tests/", "src/", "assets/", "secrets/", "docs/")
PROTECTED_NAMES = ("README.md",)
PROTECTED_NAME_PREFIXES = ("keep", "do_not_move", "old_blob")
MANIFEST_PATH = "docs/workspace_manifest.json"


class FileMoveMutationPolicy:
    capability_id = "file_management.safe_file_moves"

    def validate(self, operations: list[dict], *, root_path) -> list[str]:
        errors: list[str] = []
        root = Path(root_path).resolve()
        for operation in operations:
            action = operation.get("action")
            if action == "move":
                source = str(operation.get("source", ""))
                destination = str(operation.get("destination", ""))
                if _outside(root, source) or _outside(root, destination):
                    errors.append(f"path_outside_root:{source}->{destination}")
                if _protected(source):
                    errors.append(f"protected_source_path:{source}")
                if destination and not _outside(root, destination) and (root / destination).exists():
                    errors.append(f"destination_exists:{destination}")
            elif action == "write":
                path = str(operation.get("path", ""))
                if _outside(root, path):
                    errors.append(f"path_outside_root:{path}->{path}")
                if path != MANIFEST_PATH:
                    errors.append(f"unsupported_write_path:{operation.get('path')}")
            else:
                errors.append(f"unsupported_operation:{action}")
        return errors


def _outside(root: Path, relative: str) -> bool:
    if not relative:
        return True
    try:
        (root / relative).resolve().relative_to(root)
    except ValueError:
        return True
    return False


def _protected(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    name = Path(normalized).name.lower()
    return (
        normalized in PROTECTED_NAMES
        or normalized.startswith(PROTECTED_PREFIXES)
        or name.startswith(PROTECTED_NAME_PREFIXES)
    )
