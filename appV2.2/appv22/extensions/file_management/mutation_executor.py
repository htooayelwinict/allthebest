from __future__ import annotations

import json
import shutil
from pathlib import Path

from appv22.extensions.file_management.mutation_policy import FileMoveMutationPolicy


class FileMutationExecutor:
    capability_id = "file_management.file_mutation_executor"

    def apply(self, operations: list[dict], *, root_path) -> dict:
        root = Path(root_path).resolve()
        errors = FileMoveMutationPolicy().validate(operations, root_path=root)
        if errors:
            return {"status": "denied", "touched_paths": [], "errors": errors}

        touched: list[str] = []
        for operation in operations:
            action = operation["action"]
            if action == "move":
                source_name = str(operation["source"])
                destination_name = str(operation["destination"])
                source = root / source_name
                destination = root / destination_name
                if not source.is_file():
                    return {
                        "status": "failed",
                        "touched_paths": sorted(set(touched)),
                        "errors": [f"missing_source:{source_name}"],
                    }
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                touched.extend([source_name, destination_name])
            elif action == "write":
                path_name = str(operation["path"])
                path = root / path_name
                path.parent.mkdir(parents=True, exist_ok=True)
                content = operation.get("content", "")
                if not isinstance(content, str):
                    content = json.dumps(content, indent=2, sort_keys=True)
                path.write_text(content, encoding="utf-8")
                touched.append(path_name)
        return {"status": "applied", "touched_paths": sorted(set(touched)), "errors": []}
