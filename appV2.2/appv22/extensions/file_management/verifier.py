from __future__ import annotations

import json
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import MANIFEST_PATH, _outside
from appv22.extensions.file_management.schemas import WORKSPACE_MANIFEST_SCHEMA


class WorkspaceManifestVerifier:
    capability_id = "file_management.manifest_verifier"

    def verify(self, *, root_path, verification_intent: dict) -> dict:
        root = Path(root_path).resolve()
        relative = verification_intent.get("manifest_path", MANIFEST_PATH)
        checks: list[dict[str, object]] = []
        if _outside(root, str(relative)):
            return {
                "status": "failed",
                "checks": [{"name": "manifest_path_inside_root", "passed": False}],
                "manifest": {},
            }

        manifest_path = root / str(relative)
        exists = manifest_path.is_file()
        checks.append({"name": "manifest_exists", "passed": exists})
        manifest = {}
        if exists:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                checks.append({"name": "manifest_json_valid", "passed": isinstance(manifest, dict)})
            except json.JSONDecodeError:
                checks.append({"name": "manifest_json_valid", "passed": False})
                manifest = {}

        for key in WORKSPACE_MANIFEST_SCHEMA["required"]:
            checks.append({"name": f"manifest_has_{key}", "passed": key in manifest})
        return {
            "status": "passed" if all(bool(check["passed"]) for check in checks) else "failed",
            "checks": checks,
            "manifest": manifest,
        }
