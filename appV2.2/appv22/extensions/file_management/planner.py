from __future__ import annotations

import json
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import MANIFEST_PATH, _protected


class FileCleanupPlanner:
    capability_id = "file_management.cleanup_planner"

    def plan(self, state) -> dict:
        snapshot_ref = state.world_refs.get("world://repo_snapshot/latest", {})
        snapshot = snapshot_ref.get("payload", {})
        files = sorted(path for path in snapshot.get("files", []) if isinstance(path, str))
        existing = set(files)
        destinations: dict[str, str] = {}
        moves: list[dict[str, str]] = []
        held: list[str] = []
        collisions: list[dict[str, str]] = []
        operations: list[dict] = []

        for source in files:
            if _protected(source):
                continue
            destination = _destination(source)
            if destination is None:
                continue
            conflict = destinations.get(destination)
            if destination in existing or conflict is not None:
                held.append(source)
                collisions.append(
                    {
                        "source": source,
                        "destination": destination,
                        "conflicts_with": conflict or destination,
                    }
                )
                continue
            destinations[destination] = source
            move = {"source": source, "destination": destination}
            moves.append(move)
            operations.append({"action": "move", **move})

        manifest = {
            "generated_by": "appv22",
            "moves": moves,
            "held": held,
            "collisions": collisions,
        }
        operations.append(
            {
                "action": "write",
                "path": MANIFEST_PATH,
                "content": json.dumps(manifest, indent=2, sort_keys=True),
            }
        )
        return {
            "planner_id": self.capability_id,
            "mutation_policy_id": "file_management.safe_file_moves",
            "mutation_executor_id": "file_management.file_mutation_executor",
            "verifier_id": "file_management.manifest_verifier",
            "mutation_intent": {
                "operation_batch_id": "workspace_cleanup",
                "operations": operations,
            },
            "verification_intent": {
                "manifest_path": MANIFEST_PATH,
                "moves": moves,
                "held": held,
                "collisions": collisions,
            },
        }


def _destination(source: str) -> str | None:
    path = Path(source)
    suffix = path.suffix.lower()
    if suffix == ".md":
        return f"docs/{path.name}"
    if suffix in {".log", ".json"}:
        return f"artifacts/logs/{path.name}"
    return None
