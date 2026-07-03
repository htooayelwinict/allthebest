"""JSON Schema objects for appv24 workflow package files."""

from __future__ import annotations

from collections.abc import Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.loader import PackageDocuments

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

WORKFLOW_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "type": "object",
    "additionalProperties": True,
    "required": ["version", "name", "triggers", "entrypoint", "steps"],
    "properties": {
        "version": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "triggers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
        "entrypoint": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "uses": {"type": "string"},
                    "next": {"type": "string"},
                    "approval": {"type": "string"},
                    "side_effect_class": {"type": "string"},
                },
            },
        },
    },
}

CONNECTORS_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "type": "object",
    "additionalProperties": True,
    "required": ["version", "connectors"],
    "properties": {
        "version": {"type": "string"},
        "connectors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id", "adapter", "actions"],
                "properties": {
                    "id": {"type": "string"},
                    "adapter": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                            "properties": {
                                "id": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

PERMISSIONS_SCHEMA = {
    "$schema": SCHEMA_DIALECT,
    "type": "object",
    "additionalProperties": True,
    "required": ["version", "approvals", "permissions"],
    "properties": {
        "version": {"type": "string"},
        "approvals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id", "mode", "required_for"],
                "properties": {
                    "id": {"type": "string"},
                    "mode": {"type": "string"},
                    "required_for": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "permissions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["connector", "actions", "scopes"],
                "properties": {
                    "connector": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "scopes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


def validate_schema_documents(documents: PackageDocuments) -> list[WorkflowIssue]:
    Draft202012Validator.check_schema(WORKFLOW_SCHEMA)
    Draft202012Validator.check_schema(CONNECTORS_SCHEMA)
    Draft202012Validator.check_schema(PERMISSIONS_SCHEMA)

    return [
        *_collect_schema_issues("workflow.yaml", WORKFLOW_SCHEMA, documents.workflow),
        *_collect_schema_issues("connectors.yaml", CONNECTORS_SCHEMA, documents.connectors),
        *_collect_schema_issues("permissions.yaml", PERMISSIONS_SCHEMA, documents.permissions),
    ]


def _collect_schema_issues(
    filename: str, schema: Mapping[str, object], document: Mapping[str, object]
) -> list[WorkflowIssue]:
    return [_as_issue(filename, error) for error in Draft202012Validator(schema).iter_errors(document)]


def _as_issue(file: str, error: ValidationError) -> WorkflowIssue:
    return WorkflowIssue(
        code="schema",
        file=file,
        path=tuple(error.path),
        message=error.message,
    )
