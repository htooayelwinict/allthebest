"""Runtime middleware hooks for appv24 workflow package execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from appv24.workflows.loader import PackageDocuments
from appv24.workflows.middleware_contracts import (
    MiddlewareDefinition,
    middleware_env_refs,
    package_middleware_definitions,
)


@dataclass(frozen=True)
class MiddlewareRunContext:
    run_id: str
    workflow_name: str
    package_fingerprint: str
    mode: str


class MiddlewareExecutor:
    middleware_id: str

    def emit(self, hook: str, context: MiddlewareRunContext, details: Mapping[str, Any]) -> None:
        raise NotImplementedError


MiddlewareExecutorFactory = Callable[[MiddlewareDefinition, Mapping[str, str]], MiddlewareExecutor]


class AuditLogMiddleware(MiddlewareExecutor):
    middleware_id = "audit.log"

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, hook: str, context: MiddlewareRunContext, details: Mapping[str, Any]) -> None:
        payload = {
            "version": "appv24.middleware-audit/v1",
            "middleware_id": self.middleware_id,
            "hook": hook,
            "run_id": context.run_id,
            "workflow_name": context.workflow_name,
            "package_fingerprint": context.package_fingerprint,
            "mode": context.mode,
            **_safe_details(details),
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


class MiddlewareRuntime:
    def __init__(self, middlewares: tuple[MiddlewareExecutor, ...]):
        self.middlewares = middlewares

    @property
    def middleware_ids(self) -> tuple[str, ...]:
        return tuple(middleware.middleware_id for middleware in self.middlewares)

    def emit(self, hook: str, context: MiddlewareRunContext, **details: Any) -> tuple[str, ...]:
        issues: list[str] = []
        for middleware in self.middlewares:
            try:
                middleware.emit(hook, context, details)
            except OSError as error:
                issues.append(f"middleware {middleware.middleware_id} failed {hook}: {error}")
        return tuple(issues)


def missing_middleware_env_issues(documents: PackageDocuments, env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        f"missing middleware env for {middleware_id}; required env: {env_ref.env_name}"
        for middleware_id, env_ref in middleware_env_refs(documents)
        if env_ref.required and env_ref.env_name not in env
    )


def missing_middleware_executor_issues(
    documents: PackageDocuments,
    executor_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> tuple[str, ...]:
    factories = executor_factories or {}
    issues: list[str] = []
    for definition in package_middleware_definitions(documents):
        if _has_builtin_executor(definition):
            continue
        if definition.middleware_id in factories:
            continue
        issues.append(f"middleware runtime not configured for {definition.middleware_id}")
    return tuple(issues)


def middleware_runtime_setup_issues(
    documents: PackageDocuments,
    env: Mapping[str, str],
    executor_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> tuple[str, ...]:
    return (
        *missing_middleware_env_issues(documents, env),
        *missing_middleware_executor_issues(documents, executor_factories),
    )


def build_middleware_runtime(
    documents: PackageDocuments,
    env: Mapping[str, str],
    executor_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> MiddlewareRuntime:
    middlewares: list[MiddlewareExecutor] = []
    factories = executor_factories or {}
    for definition in package_middleware_definitions(documents):
        if definition.middleware_id == "audit.log" and definition.adapter == "builtin":
            middlewares.append(AuditLogMiddleware(Path(env["APPV24_WORKFLOW_AUDIT_LOG_PATH"]).expanduser()))
            continue
        factory = factories.get(definition.middleware_id)
        if factory is not None:
            middlewares.append(factory(definition, env))
    return MiddlewareRuntime(tuple(middlewares))


def _has_builtin_executor(definition: MiddlewareDefinition) -> bool:
    return definition.adapter == "builtin" and definition.middleware_id == "audit.log"


def has_middleware_executor(
    definition: MiddlewareDefinition,
    executor_factories: Mapping[str, MiddlewareExecutorFactory] | None = None,
) -> bool:
    return _has_builtin_executor(definition) or definition.middleware_id in (executor_factories or {})


def _safe_details(details: Mapping[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "connector_action",
        "issue_count",
        "status",
        "step_id",
        "terminal_step",
    }
    return {key: value for key, value in details.items() if key in allowed_keys and value is not None}
