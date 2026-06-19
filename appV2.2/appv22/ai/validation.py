"""Tool-argument validation. Port of pi/packages/ai/src/utils/validation.ts (subset)."""

from __future__ import annotations

from typing import Any


class ToolValidationError(ValueError):
    """Raised when tool-call arguments do not match the tool's JSON schema."""


def validate_tool_arguments(tool: Any, tool_call: Any) -> dict[str, Any]:
    """Validate tool_call.arguments against tool.parameters; return parsed args.

    Mirrors pi's validateToolArguments: raises on invalid, returns the value on
    success. `tool.parameters` is a JSON-schema dict.
    """
    args = getattr(tool_call, "arguments", None)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolValidationError(f"Tool {getattr(tool, 'name', '?')} arguments must be an object")
    schema = getattr(tool, "parameters", None) or {}
    _validate_value(args, schema, path=getattr(tool, "name", "args"))
    return args


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path}: expected object")
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise ToolValidationError(f"{path}: missing required property '{key}'")
        properties = schema.get("properties") or {}
        for key, sub_value in value.items():
            if key in properties:
                _validate_value(sub_value, properties[key], f"{path}.{key}")
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise ToolValidationError(f"{path}: expected array")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]")
        return
    if schema_type == "string" and not isinstance(value, str):
        raise ToolValidationError(f"{path}: expected string")
    if schema_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ToolValidationError(f"{path}: expected integer")
    if schema_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ToolValidationError(f"{path}: expected number")
    if schema_type == "boolean" and not isinstance(value, bool):
        raise ToolValidationError(f"{path}: expected boolean")
