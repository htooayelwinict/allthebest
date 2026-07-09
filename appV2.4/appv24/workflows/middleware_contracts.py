"""Workflow middleware runtime contracts.

Middlewares are package-level runtime interfaces for cross-cutting behavior.
They are intentionally separate from workflow step schemas and connector action
contracts so custom middleware can be appended without changing orchestration or
connector interfaces.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from appv24.workflows.errors import WorkflowIssue, sorted_issues
from appv24.workflows.extension_qa import validate_extension_qa_manifest
from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class MiddlewareEnvRef:
    env_name: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class MiddlewareContract:
    middleware_id: str
    adapter: str
    source_ref: str
    env: tuple[MiddlewareEnvRef, ...] = ()
    state_requirements: tuple[str, ...] = ()
    audit_requirements: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()
    prompt_summary: str = ""


@dataclass(frozen=True)
class MiddlewareDefinition:
    middleware_id: str
    adapter: str
    source_ref: str
    config: Mapping[str, Any]
    env: tuple[MiddlewareEnvRef, ...] = ()
    state: tuple[str, ...] = ()
    audit: tuple[str, ...] = ()
    applies_to: tuple[str, ...] = ()


def builtin_middleware_contract_catalog() -> dict[str, MiddlewareContract]:
    contracts = (
        MiddlewareContract(
            middleware_id="audit.log",
            adapter="builtin",
            source_ref="catalog://audit.log@v1",
            env=(MiddlewareEnvRef("APPV24_WORKFLOW_AUDIT_LOG_PATH", "workflow audit log path"),),
            audit_requirements=("middleware:audit.log",),
            applies_to=("local", "live-read", "live-draft", "live-production"),
            prompt_summary="audit.log records middleware envelope events to an operator-controlled audit log",
        ),
    )
    return {contract.middleware_id: contract for contract in contracts}


def package_middleware_ids(documents: PackageDocuments) -> tuple[str, ...]:
    return tuple(definition.middleware_id for definition in package_middleware_definitions(documents))


def package_middleware_definitions(documents: PackageDocuments) -> tuple[MiddlewareDefinition, ...]:
    definitions: list[MiddlewareDefinition] = []
    for entry in _middleware_entries(documents):
        middleware_id = entry.get("id")
        adapter = entry.get("adapter")
        source = entry.get("source")
        if not isinstance(middleware_id, str) or not middleware_id.strip():
            continue
        if not isinstance(adapter, str) or not adapter.strip():
            continue
        config = entry.get("config")
        definitions.append(
            MiddlewareDefinition(
                middleware_id=middleware_id,
                adapter=adapter,
                source_ref=source if isinstance(source, str) else "",
                config=config if isinstance(config, Mapping) else {},
                env=_declared_env_refs(entry),
                state=_string_list(entry.get("state")),
                audit=_string_list(entry.get("audit")),
                applies_to=_string_list(entry.get("applies_to")),
            )
        )
    return tuple(definitions)


def middleware_env_refs(documents: PackageDocuments) -> tuple[tuple[str, MiddlewareEnvRef], ...]:
    refs: list[tuple[str, MiddlewareEnvRef]] = []
    catalog = builtin_middleware_contract_catalog()
    for definition in package_middleware_definitions(documents):
        contract = catalog.get(definition.middleware_id)
        if contract is not None:
            refs.extend((definition.middleware_id, env_ref) for env_ref in contract.env)
        refs.extend((definition.middleware_id, env_ref) for env_ref in definition.env)
    deduped: dict[tuple[str, str], tuple[str, MiddlewareEnvRef]] = {}
    for middleware_id, env_ref in refs:
        deduped.setdefault((middleware_id, env_ref.env_name), (middleware_id, env_ref))
    return tuple(value for _, value in sorted(deduped.items()))


def middleware_env_requirements(documents: PackageDocuments) -> tuple[str, ...]:
    return tuple(dict.fromkeys(env_ref.env_name for _, env_ref in middleware_env_refs(documents) if env_ref.required))


def middleware_state_requirements(documents: PackageDocuments) -> tuple[str, ...]:
    catalog = builtin_middleware_contract_catalog()
    requirements: list[str] = []
    for definition in package_middleware_definitions(documents):
        contract = catalog.get(definition.middleware_id)
        if contract is not None:
            requirements.extend(contract.state_requirements)
        requirements.extend(definition.state)
    return tuple(dict.fromkeys(requirements))


def middleware_audit_requirements(documents: PackageDocuments) -> tuple[str, ...]:
    catalog = builtin_middleware_contract_catalog()
    requirements: list[str] = []
    for definition in package_middleware_definitions(documents):
        contract = catalog.get(definition.middleware_id)
        if contract is not None:
            requirements.extend(contract.audit_requirements)
        requirements.extend(definition.audit)
    return tuple(dict.fromkeys(requirements))


def validate_middleware_contracts(documents: PackageDocuments) -> list[WorkflowIssue]:
    catalog = builtin_middleware_contract_catalog()
    issues: list[WorkflowIssue] = []
    for index, entry in enumerate(_middleware_entries(documents)):
        middleware_id = entry.get("id")
        adapter = entry.get("adapter")
        source = entry.get("source")
        path = ("middlewares", index)
        if not isinstance(middleware_id, str) or not middleware_id.strip():
            continue
        if not isinstance(adapter, str):
            continue
        if not isinstance(source, str) or not source.strip():
            issues.append(
                WorkflowIssue(
                    "middleware_source_missing",
                    "middlewares.yaml",
                    (*path, "source"),
                    f"{middleware_id} requires a versioned source reference",
                    expected="source such as catalog://audit.log@v1 or local://...@sha256:<digest>",
                )
            )

        contract = catalog.get(middleware_id)
        if adapter == "builtin":
            if contract is None:
                issues.append(
                    WorkflowIssue(
                        "unknown_middleware_contract",
                        "middlewares.yaml",
                        path,
                        f"{middleware_id} is not in the built-in middleware contract catalog",
                        expected=", ".join(sorted(catalog)),
                        repair_hint="Use a built-in middleware id or set adapter to external, local, or mcp for custom middleware.",
                    )
                )
            elif source != contract.source_ref:
                issues.append(
                    WorkflowIssue(
                        "middleware_contract_source_mismatch",
                        "middlewares.yaml",
                        (*path, "source"),
                        f"{middleware_id} source must match middleware contract {contract.source_ref}",
                        expected=contract.source_ref,
                    )
                )
        elif adapter not in {"external", "local", "mcp"}:
            issues.append(
                WorkflowIssue(
                    "middleware_adapter",
                    "middlewares.yaml",
                    (*path, "adapter"),
                    f"{middleware_id} uses unsupported middleware adapter {adapter}",
                    expected="builtin, external, local, or mcp",
                )
            )
        elif source == "":
            issues.append(
                WorkflowIssue(
                    "middleware_source_missing",
                    "middlewares.yaml",
                    (*path, "source"),
                    f"{middleware_id} custom middleware requires a source reference",
                    expected="versioned local, external, or mcp source reference",
                )
            )

        for env_index, env_ref in enumerate(_declared_env_refs(entry)):
            if not env_ref.env_name.startswith("APPV24_"):
                issues.append(
                    WorkflowIssue(
                        "middleware_env_namespace",
                        "middlewares.yaml",
                        (*path, "env", env_index, "name"),
                        f"{middleware_id} middleware env must use the APPV24_ namespace: {env_ref.env_name}",
                        expected="APPV24_<NAME>",
                        repair_hint="Declare middleware runtime config using APPV24_ env names so readiness/export can report it safely.",
                    )
                )
        issues.extend(_extension_qa_issues(entry, middleware_id, path))
    return list(sorted_issues(issues))


def _extension_qa_issues(
    entry: Mapping[str, Any],
    middleware_id: str,
    path: tuple[str | int, ...],
) -> list[WorkflowIssue]:
    if not _requires_extension_qa(entry):
        return []
    qa = entry.get("qa")
    qa_path = (*path, "qa")
    if not isinstance(qa, Mapping):
        return [
            WorkflowIssue(
                "extension_qa_missing",
                "middlewares.yaml",
                qa_path,
                f"{middleware_id} requires passed extension QA evidence",
                expected="qa manifest with status: passed",
                repair_hint=(
                    "Generate the middleware with the coding profile, run QA, "
                    "and attach its appv24.extension_qa/v1 manifest."
                ),
            )
        ]
    report = validate_extension_qa_manifest(
        qa,
        expected_kind="middleware",
        expected_extension_id=middleware_id,
    )
    return [
        WorkflowIssue(
            issue.code,
            "middlewares.yaml",
            (*qa_path, *issue.path),
            f"{middleware_id} extension QA failed: {issue.message}",
            expected=issue.expected,
            repair_hint=issue.repair_hint,
        )
        for issue in report.issues
    ]


def _requires_extension_qa(entry: Mapping[str, Any]) -> bool:
    hints = entry.get("workflow_hints")
    if not isinstance(hints, Mapping):
        return False
    return hints.get("qa_required") is True or hints.get("generated_by") == "coding_profile"


def _middleware_entries(documents: PackageDocuments) -> tuple[Mapping[str, Any], ...]:
    middlewares = documents.middlewares
    raw_entries = middlewares.get("middlewares") if isinstance(middlewares, Mapping) else None
    if not isinstance(raw_entries, list):
        return ()
    return tuple(entry for entry in raw_entries if isinstance(entry, Mapping))


def _declared_env_refs(entry: Mapping[str, Any]) -> tuple[MiddlewareEnvRef, ...]:
    raw_env = entry.get("env")
    if not isinstance(raw_env, list):
        return ()
    refs: list[MiddlewareEnvRef] = []
    for raw_ref in raw_env:
        if not isinstance(raw_ref, Mapping):
            continue
        name = raw_ref.get("name")
        if not isinstance(name, str) or not name:
            continue
        purpose = raw_ref.get("purpose")
        refs.append(
            MiddlewareEnvRef(
                env_name=name,
                purpose=str(purpose) if isinstance(purpose, str) and purpose else "middleware runtime config",
                required=raw_ref.get("required") is not False,
            )
        )
    return tuple(refs)


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)
