from __future__ import annotations

WORKSPACE_MANIFEST_SCHEMA = {
    "schema_id": "file_management.workspace_manifest",
    "type": "object",
    "required": ["generated_by", "moves", "held", "collisions"],
    "properties": {
        "generated_by": {"type": "string"},
        "moves": {"type": "array"},
        "held": {"type": "array"},
        "collisions": {"type": "array"},
    },
}
