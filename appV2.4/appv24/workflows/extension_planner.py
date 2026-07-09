"""Build-time PRD prompts for workflow connector and middleware extensions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Literal

from appv24.workflows.errors import WorkflowIssue
from appv24.workflows.loader import PackageDocuments


ExtensionKind = Literal["connector", "middleware"]
CatalogRegistration = Literal["package", "reusable"]
ARTIFACT_BUNDLE_VERSION = "appv24.workflow-extension-artifact-bundle/v1"
ARTIFACT_BUNDLE_BEGIN = "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_BEGIN"
ARTIFACT_BUNDLE_END = "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_END"


@dataclass(frozen=True)
class WorkflowExtensionBuildRequest:
    kind: ExtensionKind
    extension_id: str
    business_goal: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    scopes: tuple[str, ...] = ()
    env_requirements: tuple[str, ...] = ()
    side_effect_class: str = "read"
    approval_class: str = "none"
    idempotency_key_fields: tuple[str, ...] = ()
    dry_run_fixtures: tuple[str, ...] = ()
    qa_commands: tuple[str, ...] = ()
    catalog_registration: CatalogRegistration = "package"


def build_connector_extension_request(
    documents: PackageDocuments,
    action_ref: str,
    *,
    catalog_registration: CatalogRegistration = "package",
) -> WorkflowExtensionBuildRequest:
    """Build a coder-profile PRD request from a package-owned connector action."""

    connector_id, separator, action_id = action_ref.partition(".")
    if not connector_id or separator != "." or not action_id:
        raise ValueError(f"connector action ref must be connector.action: {action_ref}")

    for connector in _connector_entries(documents.connectors):
        if _string(connector.get("id")) != connector_id:
            continue
        connector_hints = _mapping(connector.get("workflow_hints"))
        for action in _action_entries(connector):
            if _string(action.get("id")) != action_id:
                continue
            hints = {**connector_hints, **_mapping(action.get("workflow_hints"))}
            return WorkflowExtensionBuildRequest(
                kind="connector",
                extension_id=action_ref,
                business_goal=(
                    _string(hints.get("business_goal"))
                    or _string(action.get("description"))
                    or _string(connector.get("description"))
                    or f"Build connector action {action_ref}."
                ),
                input_schema=_input_schema_from_hints(hints),
                output_schema=_output_schema_from_action(action, hints),
                scopes=_string_tuple(action.get("required_scopes")),
                env_requirements=_required_env_names(connector, action),
                side_effect_class=_side_effect_class(action),
                approval_class=_approval_class(action),
                idempotency_key_fields=_idempotency_key_fields(action),
                dry_run_fixtures=tuple(sorted(documents.dry_run_fixtures)),
                qa_commands=_string_tuple(hints.get("qa_commands")),
                catalog_registration=catalog_registration,
            )

    raise ValueError(f"connector action not found in package documents: {action_ref}")


def build_middleware_extension_request(
    documents: PackageDocuments,
    middleware_id: str,
    *,
    catalog_registration: CatalogRegistration = "package",
) -> WorkflowExtensionBuildRequest:
    """Build a coder-profile PRD request from a package-owned middleware spec."""

    for entry in _middleware_entries(documents.middlewares):
        if _string(entry.get("id")) != middleware_id:
            continue
        hints = _mapping(entry.get("workflow_hints"))
        return WorkflowExtensionBuildRequest(
            kind="middleware",
            extension_id=middleware_id,
            business_goal=(
                _string(hints.get("business_goal"))
                or _string(entry.get("description"))
                or f"Build middleware {middleware_id}."
            ),
            input_schema=_schema_from_hints(hints, "input_schema"),
            output_schema=_schema_from_hints(hints, "output_schema"),
            scopes=_string_tuple(entry.get("audit")),
            env_requirements=_required_env_names(entry),
            side_effect_class=_string(entry.get("side_effect_class")) or "read",
            approval_class=_approval_class(entry),
            dry_run_fixtures=tuple(sorted(documents.dry_run_fixtures)),
            qa_commands=_string_tuple(hints.get("qa_commands")),
            catalog_registration=catalog_registration,
        )

    raise ValueError(f"middleware not found in package documents: {middleware_id}")


def build_extension_requests_for_validation_issues(
    documents: PackageDocuments,
    issues: Iterable[WorkflowIssue],
    *,
    catalog_registration: CatalogRegistration = "package",
) -> tuple[WorkflowExtensionBuildRequest, ...]:
    """Return coder-profile PRD requests for extension QA validation failures."""

    requests: list[WorkflowExtensionBuildRequest] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        if not issue.code.startswith("extension_qa"):
            continue
        request = _request_for_validation_issue(
            documents,
            issue,
            catalog_registration=catalog_registration,
        )
        if request is None:
            continue
        key = (request.kind, request.extension_id)
        if key in seen:
            continue
        seen.add(key)
        requests.append(request)
    return tuple(requests)


def render_extension_prd_prompt(
    request: WorkflowExtensionBuildRequest,
    *,
    qa_return_manifest_path: str | None = None,
) -> str:
    """Render a coder-profile prompt without executing the coding profile."""

    kind = request.kind
    boundary = "connector port/adapter interface" if kind == "connector" else "middleware runtime interface"
    lines = [
        f"# appv24 {kind} extension PRD: {request.extension_id}",
        "",
        f"Business goal: {request.business_goal}",
        f"Adapter boundary: {boundary}",
        "",
        "## Runtime Boundary",
        "",
        "- Runtime must not call the coding profile.",
        "- Runtime must consume only the pinned validated artifact.",
        f"- Do not add workflow-runtime special cases for this {kind}.",
        "- Keep connector or middleware semantics behind its own interface.",
        "- Do not print, persist, or fixture raw secret values.",
        "",
        "## Package Workspace",
        "",
        "- You are running with --cwd set to the workflow package directory.",
        "- Do not inspect or modify files outside that package directory.",
        "- Use workflow.yaml, connectors.yaml, permissions.yaml, and dry_run_fixtures/*.yaml as the interface contract.",
        f"- Keep implementation artifacts package-local under {_artifact_path_hint(request)}.",
        "- Do not change workflow.yaml semantics to avoid implementing this extension.",
        "",
        "## Artifact Bundle Return Channel",
        "",
        "- Prefer this bundle return over direct package file writes.",
        "- Do not write source files directly; return source text in bundle.files.",
        "- Print exactly one JSON object between these marker lines:",
        f"  - {ARTIFACT_BUNDLE_BEGIN}",
        f"  - {ARTIFACT_BUNDLE_END}",
        "- The workflow parent validates paths, writes package-local artifacts, writes returned QA, and applies QA.",
        "- Bundle file paths must be relative package paths under the extension artifact root.",
        "- Bundle content must be UTF-8 text and must not contain raw secrets.",
        "",
        "Artifact bundle shape:",
        "```json",
        _artifact_bundle_shape(request),
        "```",
        "",
        "## Interface Contract",
        "",
        f"Extension ID: {request.extension_id}",
        f"Side-effect class: {request.side_effect_class}",
        f"Approval class: {request.approval_class}",
        f"Scopes: {_join_or_none(request.scopes)}",
        f"Environment/config names: {_join_or_none(request.env_requirements)}",
        f"Idempotency key fields: {_join_or_none(request.idempotency_key_fields)}",
        f"Dry-run fixtures: {_join_or_none(request.dry_run_fixtures)}",
        f"Catalog registration: {_catalog_registration_label(request.catalog_registration)}",
        _catalog_registration_instruction(request.catalog_registration),
        "",
        "Input schema:",
        "```json",
        _json(request.input_schema),
        "```",
        "",
        "Output schema:",
        "```json",
        _json(request.output_schema),
        "```",
        "",
        "## QA Contract",
        "",
        "- Write failing tests before implementation.",
        "- Prove schema validation, dry-run fixtures, and side-effect policy before live use.",
        "- Prove secret/config names are reported without raw values.",
        "- Prove the workflow package can reference this extension by interface only.",
        "- Return an appv24.extension_qa/v1 manifest with status: passed, test commands, side-effect policy result, runtime boundary result, adapter contract result, and fingerprints for spec, implementation, tests, and fixtures.",
        "- QA fingerprints must be sha256:<64 lowercase hex characters>; do not return placeholders such as sha256:spec.",
        "- Set qa.adapter_contract: passed only after the connector transport or middleware executor satisfies the appv24 runtime interface.",
        "- For connector extensions, include contract.required_scopes matching the connector action required_scopes.",
        "- For connector extensions, include contract.allowed_outputs matching the connector action outputs.allowed top-level fields.",
        "- Put the QA manifest under bundle.qa_manifest when using the artifact bundle return.",
        "- Write the QA manifest file to the exact return path before exiting. Use this only as a fallback if you do not return the artifact bundle.",
        "- Do not finish by only describing the QA manifest.",
        "- Do not write status: passed unless the tests ran and the adapter contract is satisfied.",
        "",
        "Required QA manifest shape:",
        "```yaml",
        _qa_manifest_shape(request),
        "```",
    ]
    if qa_return_manifest_path:
        lines.extend(
            (
                f"- Return QA manifest path: {qa_return_manifest_path}",
                "- The user-facing /workflow-approve command reads this returned-QA inbox.",
            )
        )
    if request.qa_commands:
        lines.extend(("", "Required QA commands:"))
        lines.extend(f"- {command}" for command in request.qa_commands)
    return "\n".join(lines)


def _artifact_path_hint(request: WorkflowExtensionBuildRequest) -> str:
    if request.kind == "connector":
        connector_id, _, action_id = request.extension_id.partition(".")
        if connector_id and action_id:
            return f"connectors/{connector_id}/{action_id}.py and connectors/{connector_id}/test_{action_id}.py"
        return "connectors/<connector>/<action>.py and matching tests"
    return f"middlewares/{request.extension_id}/ and matching tests"


def _qa_manifest_shape(request: WorkflowExtensionBuildRequest) -> str:
    lines = [
        "version: appv24.extension_qa/v1",
        f"kind: {request.kind}",
        f"extension_id: {request.extension_id}",
        "source: local://<package-local-artifact>@sha256:<real-implementation-fingerprint>",
        "status: passed",
        "qa:",
        "  schema_validation: passed",
        "  tests:",
        "    - command: <exact command that passed>",
        "      status: passed",
        "  side_effect_policy: passed",
        "  runtime_boundary: passed",
        "  adapter_contract: passed",
        "contract:",
        f"  required_scopes: {_yaml_inline_list(request.scopes)}",
        f"  allowed_outputs: {_yaml_inline_list(_allowed_output_fields(request.output_schema))}",
        "fingerprints:",
        "  spec: sha256:<64 lowercase hex characters>",
        "  implementation: sha256:<64 lowercase hex characters>",
        "  tests: sha256:<64 lowercase hex characters>",
        "  fixtures: sha256:<64 lowercase hex characters>",
    ]
    return "\n".join(lines)


def _artifact_bundle_shape(request: WorkflowExtensionBuildRequest) -> str:
    return json.dumps(
        {
            "version": ARTIFACT_BUNDLE_VERSION,
            "kind": request.kind,
            "extension_id": request.extension_id,
            "files": [
                {
                    "path": _artifact_bundle_file_path(request),
                    "content": "<complete UTF-8 source or test file content>",
                }
            ],
            "qa_manifest": {
                "version": "appv24.extension_qa/v1",
                "kind": request.kind,
                "extension_id": request.extension_id,
                "source": "local://<package-local-artifact>@sha256:<real-implementation-fingerprint>",
                "status": "passed",
                "qa": {
                    "schema_validation": "passed",
                    "tests": [{"command": "<exact command that passed>", "status": "passed"}],
                    "side_effect_policy": "passed",
                    "runtime_boundary": "passed",
                    "adapter_contract": "passed",
                },
                "contract": {
                    "required_scopes": list(request.scopes),
                    "allowed_outputs": list(_allowed_output_fields(request.output_schema)),
                },
                "fingerprints": {
                    "spec": "sha256:<64 lowercase hex characters>",
                    "implementation": "sha256:<64 lowercase hex characters>",
                    "tests": "sha256:<64 lowercase hex characters>",
                    "fixtures": "sha256:<64 lowercase hex characters>",
                },
            },
        },
        indent=2,
        sort_keys=True,
    )


def _artifact_bundle_file_path(request: WorkflowExtensionBuildRequest) -> str:
    if request.kind == "connector":
        connector_id, _, action_id = request.extension_id.partition(".")
        if connector_id and action_id:
            return f"connectors/{connector_id}/{action_id}.py"
        return "connectors/<connector>/<action>.py"
    return f"middlewares/{request.extension_id.replace('.', '-')}/adapter.py"


def _allowed_output_fields(output_schema: Mapping[str, object]) -> tuple[str, ...]:
    properties = output_schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    return tuple(str(key) for key in properties if isinstance(key, str) and key)


def _yaml_inline_list(values: tuple[str, ...]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(values) + "]"


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _catalog_registration_label(value: CatalogRegistration) -> str:
    return "reusable-catalog-candidate" if value == "reusable" else "package-only"


def _catalog_registration_instruction(value: CatalogRegistration) -> str:
    if value == "reusable":
        return "Include stable catalog label, group, source, and QA evidence for later reuse."
    return "Do not add this extension to the reusable catalog unless the user promotes it later."


def _json(value: Mapping[str, object]) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _request_for_validation_issue(
    documents: PackageDocuments,
    issue: WorkflowIssue,
    *,
    catalog_registration: CatalogRegistration,
) -> WorkflowExtensionBuildRequest | None:
    connector_action_ref = _connector_action_ref_from_issue(issue)
    if connector_action_ref is not None:
        try:
            return build_connector_extension_request(
                documents,
                connector_action_ref,
                catalog_registration=catalog_registration,
            )
        except ValueError:
            return None

    middleware_id = _middleware_id_from_issue(documents, issue)
    if middleware_id is not None:
        try:
            return build_middleware_extension_request(
                documents,
                middleware_id,
                catalog_registration=catalog_registration,
            )
        except ValueError:
            return None
    return None


def _connector_action_ref_from_issue(issue: WorkflowIssue) -> str | None:
    if issue.file != "connectors.yaml":
        return None
    path = issue.path
    if len(path) < 4 or path[0] != "connectors" or path[2] != "actions":
        return None
    connector_id = path[1]
    action_id = path[3]
    if not isinstance(connector_id, str) or not isinstance(action_id, str):
        return None
    return f"{connector_id}.{action_id}"


def _middleware_id_from_issue(documents: PackageDocuments, issue: WorkflowIssue) -> str | None:
    if issue.file != "middlewares.yaml":
        return None
    path = issue.path
    if len(path) < 2 or path[0] != "middlewares":
        return None
    middleware_key = path[1]
    if isinstance(middleware_key, str):
        return middleware_key
    if not isinstance(middleware_key, int):
        return None
    entries = _middleware_entries(documents.middlewares)
    if middleware_key < 0 or middleware_key >= len(entries):
        return None
    return _string(entries[middleware_key].get("id"))


def _input_schema_from_hints(hints: Mapping[str, Any]) -> Mapping[str, object]:
    raw_schema = _mapping(hints.get("input_schema"))
    if raw_schema:
        return raw_schema
    fields = _string_tuple(hints.get("input_fields"))
    required_fields = _string_tuple(hints.get("required_input_fields"))
    return _schema_from_fields((*fields, *required_fields), required_fields=required_fields)


def _output_schema_from_action(action: Mapping[str, Any], hints: Mapping[str, Any]) -> Mapping[str, object]:
    raw_schema = _mapping(hints.get("output_schema"))
    if raw_schema:
        return raw_schema
    outputs = _mapping(action.get("outputs"))
    return _schema_from_fields(_string_tuple(outputs.get("allowed")))


def _schema_from_hints(hints: Mapping[str, Any], key: str) -> Mapping[str, object]:
    raw_schema = _mapping(hints.get(key))
    return raw_schema if raw_schema else {"type": "object", "additionalProperties": True}


def _schema_from_fields(
    fields: tuple[str, ...],
    *,
    required_fields: tuple[str, ...] = (),
) -> Mapping[str, object]:
    unique_fields = _unique_strings(fields)
    if not unique_fields:
        return {"type": "object", "additionalProperties": True}
    schema: dict[str, object] = {
        "type": "object",
        "properties": {field: {} for field in unique_fields},
    }
    unique_required_fields = tuple(field for field in _unique_strings(required_fields) if field in unique_fields)
    if unique_required_fields:
        schema["required"] = list(unique_required_fields)
    return schema


def _connector_entries(connectors_document: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_connectors = connectors_document.get("connectors") if isinstance(connectors_document, Mapping) else None
    if not isinstance(raw_connectors, list):
        return ()
    return tuple(connector for connector in raw_connectors if isinstance(connector, Mapping))


def _action_entries(connector: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_actions = connector.get("actions")
    if not isinstance(raw_actions, list):
        return ()
    return tuple(action for action in raw_actions if isinstance(action, Mapping))


def _middleware_entries(middlewares_document: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_middlewares = middlewares_document.get("middlewares") if isinstance(middlewares_document, Mapping) else None
    if not isinstance(raw_middlewares, list):
        return ()
    return tuple(entry for entry in raw_middlewares if isinstance(entry, Mapping))


def _required_env_names(*entries: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for entry in entries:
        raw_env = entry.get("env")
        if not isinstance(raw_env, list):
            continue
        for env_ref in raw_env:
            if not isinstance(env_ref, Mapping):
                continue
            name = _string(env_ref.get("name"))
            if name is None or env_ref.get("required") is False:
                continue
            names.append(name)
    return tuple(sorted(_unique_strings(tuple(names))))


def _idempotency_key_fields(action: Mapping[str, Any]) -> tuple[str, ...]:
    idempotency = _mapping(action.get("idempotency"))
    return _string_tuple(idempotency.get("key_fields"))


def _side_effect_class(action: Mapping[str, Any]) -> str:
    declared = _string(action.get("side_effect_class"))
    if declared is not None:
        return declared
    return "read" if action.get("side_effect") is False else "create"


def _approval_class(entry: Mapping[str, Any]) -> str:
    declared = _string(entry.get("approval_class"))
    if declared is not None:
        return declared
    return "required" if entry.get("approval_required") is True else "none"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return tuple(unique)


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
