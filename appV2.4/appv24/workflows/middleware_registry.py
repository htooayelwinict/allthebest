"""Read-only workflow middleware registry view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from appv24.workflows.loader import load_package_documents
from appv24.workflows.middleware_contracts import (
    MiddlewareDefinition,
    MiddlewareEnvRef,
    builtin_middleware_contract_catalog,
    package_middleware_definitions,
)
from appv24.workflows.middleware_runtime import MiddlewareExecutorFactory, has_middleware_executor


@dataclass(frozen=True)
class WorkflowMiddlewareRegistryEntry:
    middleware_id: str
    adapter: str
    source_ref: str
    cataloged: bool
    executor_available: bool
    env_ready: bool
    required_env: tuple[str, ...]
    config_keys: tuple[str, ...]
    state_requirements: tuple[str, ...]
    audit_requirements: tuple[str, ...]
    applies_to: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowMiddlewareRegistryReport:
    workflow_name: str
    package_path: Path
    middlewares: tuple[WorkflowMiddlewareRegistryEntry, ...]
    issues: tuple[str, ...]

    def to_text(self) -> str:
        lines = [
            "Workflow middleware registry:",
            f"workflow: {self.workflow_name or '<unknown>'}",
            f"package: {self.package_path}",
        ]
        if self.issues:
            lines.append("issues:")
            lines.extend(f"- {issue}" for issue in self.issues)
        if not self.middlewares:
            lines.append("middlewares: none")
            return "\n".join(lines)
        lines.append("middlewares:")
        for middleware in self.middlewares:
            catalog_status = "cataloged" if middleware.cataloged else "uncataloged"
            executor_status = "executor available" if middleware.executor_available else "executor missing"
            lines.append(f"- {middleware.middleware_id}: {catalog_status}, {executor_status}")
            lines.append(f"  adapter: {middleware.adapter}")
            lines.append(f"  source: {middleware.source_ref}")
            if middleware.required_env:
                lines.append(f"  env: {', '.join(middleware.required_env)}")
            if middleware.applies_to:
                lines.append(f"  applies to: {', '.join(middleware.applies_to)}")
            for issue in middleware.issues:
                lines.append(f"  issue: {issue}")
        return "\n".join(lines)


def inspect_workflow_middleware_registry(
    package_path: str | Path,
    *,
    env: Mapping[str, str],
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> WorkflowMiddlewareRegistryReport:
    """Return declared middleware interfaces joined with runtime availability."""

    documents = load_package_documents(package_path)
    entries: list[WorkflowMiddlewareRegistryEntry] = []
    issues: list[str] = []
    for definition in package_middleware_definitions(documents):
        entry = _entry(definition, env=env, middleware_factories=middleware_factories)
        entries.append(entry)
        issues.extend(entry.issues)
    return WorkflowMiddlewareRegistryReport(
        workflow_name=str(documents.workflow.get("name", "")),
        package_path=documents.root,
        middlewares=tuple(sorted(entries, key=lambda entry: entry.middleware_id)),
        issues=tuple(dict.fromkeys(issues)),
    )


def _entry(
    definition: MiddlewareDefinition,
    *,
    env: Mapping[str, str],
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] | None,
) -> WorkflowMiddlewareRegistryEntry:
    catalog = builtin_middleware_contract_catalog()
    contract = catalog.get(definition.middleware_id)
    env_refs = _env_refs(definition)
    required_env = tuple(env_ref.env_name for env_ref in env_refs if env_ref.required)
    missing_env = tuple(env_name for env_name in required_env if env_name not in env)
    executor_available = has_middleware_executor(definition, middleware_factories)
    issues: list[str] = []
    if missing_env:
        issues.extend(
            f"missing middleware env for {definition.middleware_id}; required env: {env_name}"
            for env_name in missing_env
        )
    if not executor_available:
        issues.append(f"middleware runtime not configured for {definition.middleware_id}")
    cataloged = contract is not None or definition.adapter in {"external", "local", "mcp"}
    state_requirements = definition.state
    audit_requirements = definition.audit
    applies_to = definition.applies_to
    if contract is not None:
        state_requirements = tuple(dict.fromkeys((*contract.state_requirements, *state_requirements)))
        audit_requirements = tuple(dict.fromkeys((*contract.audit_requirements, *audit_requirements)))
        applies_to = tuple(dict.fromkeys((*contract.applies_to, *applies_to)))
    return WorkflowMiddlewareRegistryEntry(
        middleware_id=definition.middleware_id,
        adapter=definition.adapter,
        source_ref=definition.source_ref,
        cataloged=cataloged,
        executor_available=executor_available,
        env_ready=not missing_env,
        required_env=required_env,
        config_keys=tuple(sorted(str(key) for key in definition.config)),
        state_requirements=state_requirements,
        audit_requirements=audit_requirements,
        applies_to=applies_to,
        issues=tuple(issues),
    )


def _env_refs(definition: MiddlewareDefinition) -> tuple[MiddlewareEnvRef, ...]:
    refs: list[MiddlewareEnvRef] = []
    contract = builtin_middleware_contract_catalog().get(definition.middleware_id)
    if contract is not None:
        refs.extend(contract.env)
    refs.extend(definition.env)
    deduped: dict[str, MiddlewareEnvRef] = {}
    for ref in refs:
        deduped.setdefault(ref.env_name, ref)
    return tuple(deduped.values())
