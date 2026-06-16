from __future__ import annotations

REPO_SNAPSHOT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed"]},
        "files": {"type": "array", "items": {"type": "string"}},
        "directories": {"type": "array", "items": {"type": "string"}},
        "text_previews": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "files", "directories", "errors"],
}

READ_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "content"],
}

WRITE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "bytes_written": {"type": "integer"},
        "overwritten": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "bytes_written"],
}

MKDIR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "created": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "created"],
}

MOVE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "source": {"type": "string"},
        "destination": {"type": "string"},
        "overwritten": {"type": "boolean"},
        "suggested_path": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "source", "destination", "overwritten"],
}

COPY_FILE_OUTPUT_SCHEMA = MOVE_FILE_OUTPUT_SCHEMA

DELETE_FILE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "denied", "failed"]},
        "path": {"type": "string"},
        "deleted": {"type": "boolean"},
        "errors": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "path", "deleted"],
}
