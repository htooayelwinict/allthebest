"""JSON Schema objects for appv24 workflow package files."""

from __future__ import annotations

from collections.abc import Mapping
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.loader import PackageDocuments

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
REQUIRED_PROPERTY_PATTERN = re.compile(r"'(?P<name>[^']+)' is a required property")
SIDE_EFFECT_CLASS_VALUES = ["read", "create", "update", "delete", "send", "publish", "provision"]
EXECUTION_MODE_VALUES = ["brokered", "manual", "design_only"]

WORKFLOW_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/workflow.schema.json",
    "type": "object",
    "required": ["version", "name", "triggers", "entrypoint", "steps"],
    "properties": {
        "version": {"const": "appv24.workflow/v1"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "triggers": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["schedule", "webhook", "manual", "event"]},
                    "schedule": {"type": "string"},
                    "event": {"type": "object"},
                },
                "additionalProperties": True,
            },
        },
        "entrypoint": {"type": "string", "minLength": 1},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["action", "terminal", "decision", "manual_approval"]},
                    "uses": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+$"},
                    "with": {"type": "object"},
                    "next": {"type": ["string", "null"]},
                    "branches": {"type": "object", "additionalProperties": {"type": "string"}},
                    "side_effect_class": {
                        "type": "string",
                        "enum": ["read", "create", "update", "delete", "send", "publish", "provision"]
                    },
                    "approval": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

CONNECTORS_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/connectors.schema.json",
    "type": "object",
    "required": ["version", "connectors"],
    "properties": {
        "version": {"const": "appv24.connectors/v1"},
        "connectors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "adapter", "actions"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "adapter": {
                        "type": "string",
                        "enum": [
                            "mcp",
                            "openapi",
                            "asyncapi",
                            "webhook",
                            "direct_api",
                            "iac",
                            "llm",
                            "manual",
                            "platform_export",
                        ]
                    },
                    "custom": {"type": "boolean"},
                    "source": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "env": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "purpose"],
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "purpose": {"type": "string", "minLength": 1},
                                "required": {"type": "boolean"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "actions": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "custom": {"type": "boolean"},
                                "source": {"type": "string", "minLength": 1},
                                "operationId": {"type": "string"},
                                "tool": {"type": "string"},
                                "side_effect": {"type": "boolean"},
                                "side_effect_class": {"type": "string", "enum": SIDE_EFFECT_CLASS_VALUES},
                                "execution_mode": {"type": "string", "enum": EXECUTION_MODE_VALUES},
                                "approval_required": {"type": "boolean"},
                                "dry_run_supported": {"type": "boolean"},
                                "idempotency": {
                                    "type": "object",
                                    "properties": {
                                        "key_fields": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                    },
                                    "additionalProperties": True,
                                },
                                "outputs": {
                                    "type": "object",
                                    "properties": {
                                        "allowed": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                    },
                                    "additionalProperties": True,
                                },
                                "required_scopes": {"type": "array", "items": {"type": "string"}},
                                "env": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["name", "purpose"],
                                        "properties": {
                                            "name": {"type": "string", "minLength": 1},
                                            "purpose": {"type": "string", "minLength": 1},
                                            "required": {"type": "boolean"},
                                        },
                                        "additionalProperties": True,
                                    },
                                },
                            },
                            "additionalProperties": True,
                        },
                    },
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

PERMISSIONS_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/permissions.schema.json",
    "type": "object",
    "required": ["version", "permissions"],
    "properties": {
        "version": {"const": "appv24.permissions/v1"},
        "approvals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "mode", "required_for"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "mode": {"type": "string", "enum": ["explicit"]},
                    "required_for": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": True,
            },
        },
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["connector", "actions"],
                "properties": {
                    "connector": {"type": "string", "minLength": 1},
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "scopes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

MIDDLEWARES_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://appv24.local/schemas/middlewares.schema.json",
    "type": "object",
    "required": ["version", "middlewares"],
    "properties": {
        "version": {"const": "appv24.middlewares/v1"},
        "middlewares": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "adapter", "source"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "adapter": {"type": "string", "enum": ["builtin", "external", "local", "mcp"]},
                    "source": {"type": "string", "minLength": 1},
                    "config": {"type": "object"},
                    "env": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "purpose"],
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "purpose": {"type": "string", "minLength": 1},
                                "required": {"type": "boolean"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "state": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "audit": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "applies_to": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["local", "live-read", "live-draft", "live-production"]},
                    },
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


def validate_schema_documents(documents: PackageDocuments) -> list[WorkflowIssue]:
    Draft202012Validator.check_schema(WORKFLOW_SCHEMA)
    Draft202012Validator.check_schema(CONNECTORS_SCHEMA)
    Draft202012Validator.check_schema(PERMISSIONS_SCHEMA)
    Draft202012Validator.check_schema(MIDDLEWARES_SCHEMA)

    return list(
        sorted_issues(
            [
                *_collect_schema_issues("workflow.yaml", WORKFLOW_SCHEMA, documents.workflow),
                *_collect_schema_issues("connectors.yaml", CONNECTORS_SCHEMA, documents.connectors),
                *_collect_schema_issues("permissions.yaml", PERMISSIONS_SCHEMA, documents.permissions),
                *(
                    _collect_schema_issues("middlewares.yaml", MIDDLEWARES_SCHEMA, documents.middlewares)
                    if documents.middlewares
                    else []
                ),
            ]
        )
    )


def _collect_schema_issues(
    filename: str, schema: Mapping[str, object], document: Mapping[str, object]
) -> list[WorkflowIssue]:
    return [_as_issue(filename, error) for error in Draft202012Validator(schema).iter_errors(document)]


def _as_issue(file: str, error: ValidationError) -> WorkflowIssue:
    return WorkflowIssue(
        code=_issue_code(error),
        file=file,
        path=_issue_path(error),
        message=_issue_message(error),
        expected=_issue_expected(error),
        repair_hint=_issue_repair_hint(file, error),
    )


def _issue_code(error: ValidationError) -> str:
    if _required_property_name(error) is not None:
        return "schema_required"
    if error.validator == "type":
        return "schema_type"
    return "schema"


def _issue_path(error: ValidationError) -> tuple[str | int, ...]:
    required_property = _required_property_name(error)
    if required_property is None:
        return tuple(error.path)
    return (*tuple(error.path), required_property)


def _issue_message(error: ValidationError) -> str:
    required_property = _required_property_name(error)
    if required_property is None:
        return error.message
    return f"missing required property '{required_property}' ({error.message})"


def _issue_expected(error: ValidationError) -> str | None:
    if _required_property_name(error) is not None:
        return "required property"
    if error.validator == "type":
        expected = error.validator_value
        if isinstance(expected, list):
            return " or ".join(str(value) for value in expected)
        return str(expected)
    return None


def _issue_repair_hint(file: str, error: ValidationError) -> str | None:
    path = _issue_path(error)
    if not path:
        return None
    rendered_path = ".".join(str(part) for part in path)
    if _required_property_name(error) is not None:
        return f"Add {file} {rendered_path}."
    if error.validator == "type":
        expected = _issue_expected(error)
        if expected:
            return f"Set {file} {rendered_path} to {expected}."
    return None


def _required_property_name(error: ValidationError) -> str | None:
    if error.validator != "required":
        return None
    match = REQUIRED_PROPERTY_PATTERN.search(error.message)
    if match is None:
        return None
    return match.group("name")
