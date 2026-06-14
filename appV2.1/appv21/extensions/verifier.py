"""Verification extension for AppV2.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class VerifierExtension:
    def verify(self, *, root_path: str | Path, verification_intent: dict[str, Any]) -> dict[str, Any]:
        root = Path(root_path).resolve()
        manifest_path = _safe_path(root, str(verification_intent.get("manifest_path") or "docs/workspace_manifest.json"))
        checks: list[dict[str, Any]] = []
        if manifest_path is None:
            return {"status": "failed", "checks": [{"name": "manifest_path_inside_root", "passed": False}]}
        checks.append({"name": "manifest_path_inside_root", "passed": True})
        if not manifest_path.is_file():
            return {"status": "failed", "checks": [{"name": "manifest_exists", "passed": False}]}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checks.append({"name": "manifest_exists", "passed": True})
        expected_moves = [
            {"source": move["source"], "destination": move["destination"]}
            for move in verification_intent.get("moves") or []
        ]
        manifest_moves = [
            {"source": move.get("source"), "destination": move.get("destination")}
            for move in manifest.get("moves") or []
            if isinstance(move, dict)
        ]
        checks.append(
            {
                "name": "manifest_moves_match_intent",
                "passed": sorted(manifest_moves, key=lambda item: (item["source"], item["destination"]))
                == sorted(expected_moves, key=lambda item: (item["source"], item["destination"])),
            }
        )
        expected_held = sorted(str(item) for item in verification_intent.get("held") or [])
        manifest_held = sorted(str(item) for item in manifest.get("held") or [])
        checks.append({"name": "manifest_held_match_intent", "passed": manifest_held == expected_held})
        for move in verification_intent.get("moves") or []:
            source = _safe_path(root, move["source"])
            destination = _safe_path(root, move["destination"])
            checks.append(
                {
                    "name": "move_pair",
                    "source": move["source"],
                    "destination": move["destination"],
                    "passed": source is not None and destination is not None and (not source.exists()) and destination.exists(),
                }
            )
        for held in verification_intent.get("held") or []:
            held_path = _safe_path(root, held)
            checks.append({"name": "held_file_preserved", "path": held, "passed": held_path is not None and held_path.exists()})
        passed = all(check.get("passed") is True for check in checks)
        return {"status": "passed" if passed else "failed", "manifest": manifest, "checks": checks}


def _safe_path(root: Path, path: str) -> Path | None:
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
