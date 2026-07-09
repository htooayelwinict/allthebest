"""Workflow profile package kernel facade.

This module composes workflow-package validation, connector catalog reporting,
middleware requirements, and execution dispatch without changing the app runtime
kernel. Connectors and middlewares remain package-declared extension interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping

from appv24.workflows.broker import LocalWorkflowBroker
from appv24.workflows.compiler import compile_workflow_documents
from appv24.workflows.connector_contracts import external_connector_action_refs, external_connector_env_requirements
from appv24.workflows.connector_registry import WorkflowConnectorRegistryAction, inspect_workflow_connector_registry
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import CredentialRefResolver, credential_resolver_for_package_connector_config
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunReport, WorkflowRunRequest
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.iac_export import export_deployment_intent
from appv24.workflows.live_broker import LiveWorkflowBroker
from appv24.workflows.live_preflight import credential_resolver_for_package_env
from appv24.workflows.live_runner import run_live_draft_workflow, run_live_production_workflow, run_live_read_workflow
from appv24.workflows.live_transports import (
    build_live_draft_transports_from_env,
    build_live_production_transports_from_connector_config,
    build_live_production_transports_from_env,
    build_live_read_transports_from_connector_config,
    build_live_read_transports_from_env,
)
from appv24.workflows.loader import load_package_documents
from appv24.workflows.middleware_contracts import middleware_env_requirements, package_middleware_ids
from appv24.workflows.middleware_registry import WorkflowMiddlewareRegistryEntry, inspect_workflow_middleware_registry
from appv24.workflows.middleware_runtime import MiddlewareExecutorFactory
from appv24.workflows.policy import evaluate_policy
from appv24.workflows.runner import run_workflow
from appv24.workflows.validator import validate_workflow_documents


@dataclass(frozen=True)
class WorkflowProfileKernelPrepareReport:
    status: str
    workflow_name: str
    package_path: Path
    package_fingerprint: str
    validation_ok: bool
    compile_ok: bool
    policy_ok: bool
    connector_actions: tuple[str, ...]
    connector_interfaces: tuple[WorkflowConnectorRegistryAction, ...]
    connector_env_requirements: tuple[str, ...]
    middleware_ids: tuple[str, ...]
    middleware_env_requirements: tuple[str, ...]
    middleware_interfaces: tuple[WorkflowMiddlewareRegistryEntry, ...]
    runtime_modes: tuple[str, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowProfileKernel:
    """Facade used by the workflow profile to prepare and run packages."""

    operator_env: Mapping[str, str] = field(default_factory=dict)
    local_broker: LocalWorkflowBroker | None = None
    live_read_broker: LiveWorkflowBroker | None = None
    live_draft_broker: LiveWorkflowBroker | None = None
    live_production_broker: LiveWorkflowBroker | None = None
    credential_resolver: CredentialRefResolver | None = None
    connector_config: ConnectorConfigResolver | None = None
    middleware_factories: Mapping[str, MiddlewareExecutorFactory] = field(default_factory=dict)

    def prepare(self, package_path: str | Path) -> WorkflowProfileKernelPrepareReport:
        package = Path(package_path).expanduser().resolve()
        documents = load_package_documents(package)
        workflow_name = str(documents.workflow.get("name", ""))
        package_fingerprint = fingerprint_package_documents(documents)
        connector_registry = inspect_workflow_connector_registry(package, env=self.operator_env)
        connector_actions = tuple(action.action_ref for action in connector_registry.actions)
        connector_env = external_connector_env_requirements(documents.connectors)
        middleware_registry = inspect_workflow_middleware_registry(
            package,
            env=self.operator_env,
            middleware_factories=self.middleware_factories,
        )
        middleware_ids = package_middleware_ids(documents)
        middleware_env = middleware_env_requirements(documents)

        validation = validate_workflow_documents(documents)
        if not validation.ok:
            return WorkflowProfileKernelPrepareReport(
                status="blocked",
                workflow_name=workflow_name,
                package_path=package,
                package_fingerprint=package_fingerprint,
                validation_ok=False,
                compile_ok=False,
                policy_ok=False,
                connector_actions=connector_actions,
                connector_interfaces=connector_registry.actions,
                connector_env_requirements=connector_env,
                middleware_ids=middleware_ids,
                middleware_env_requirements=middleware_env,
                middleware_interfaces=middleware_registry.middlewares,
                runtime_modes=("local",),
                issues=tuple(issue.message for issue in validation.issues),
            )

        compiled = compile_workflow_documents(documents)
        if not compiled.ok or compiled.ir is None:
            return WorkflowProfileKernelPrepareReport(
                status="blocked",
                workflow_name=workflow_name,
                package_path=package,
                package_fingerprint=package_fingerprint,
                validation_ok=True,
                compile_ok=False,
                policy_ok=False,
                connector_actions=connector_actions,
                connector_interfaces=connector_registry.actions,
                connector_env_requirements=connector_env,
                middleware_ids=middleware_ids,
                middleware_env_requirements=middleware_env,
                middleware_interfaces=middleware_registry.middlewares,
                runtime_modes=("local",),
                issues=tuple(issue.message for issue in compiled.issues),
            )

        policy = evaluate_policy(compiled.ir)
        if not policy.ok:
            return WorkflowProfileKernelPrepareReport(
                status="blocked",
                workflow_name=workflow_name,
                package_path=package,
                package_fingerprint=package_fingerprint,
                validation_ok=True,
                compile_ok=True,
                policy_ok=False,
                connector_actions=connector_actions,
                connector_interfaces=connector_registry.actions,
                connector_env_requirements=connector_env,
                middleware_ids=middleware_ids,
                middleware_env_requirements=middleware_env,
                middleware_interfaces=middleware_registry.middlewares,
                runtime_modes=("local",),
                issues=tuple(issue.message for issue in policy.issues),
            )

        intent = export_deployment_intent(compiled.ir, documents)
        return WorkflowProfileKernelPrepareReport(
            status="ready",
            workflow_name=workflow_name,
            package_path=package,
            package_fingerprint=package_fingerprint,
            validation_ok=True,
            compile_ok=True,
            policy_ok=True,
            connector_actions=connector_actions,
            connector_interfaces=connector_registry.actions,
            connector_env_requirements=connector_env,
            middleware_ids=middleware_ids,
            middleware_env_requirements=middleware_env,
            middleware_interfaces=middleware_registry.middlewares,
            runtime_modes=intent.runtime_modes,
            issues=(),
        )

    def run(self, request: WorkflowRunRequest) -> WorkflowRunReport:
        request = self._with_operator_env(request)
        if request.mode is WorkflowRunMode.LOCAL:
            return run_workflow(
                request,
                broker=self.local_broker,
                middleware_factories=self.middleware_factories,
            )
        if request.mode is WorkflowRunMode.LIVE_READ:
            return run_live_read_workflow(
                request,
                broker=self._with_package_external_actions(
                    request,
                    self.live_read_broker or LiveWorkflowBroker(self._live_read_transports(request), mode="live-read"),
                ),
                credential_resolver=self._credential_resolver(request),
                middleware_factories=self.middleware_factories,
            )
        if request.mode is WorkflowRunMode.LIVE_DRAFT:
            return run_live_draft_workflow(
                request,
                broker=self._with_package_external_actions(
                    request,
                    self.live_draft_broker or LiveWorkflowBroker(
                        build_live_draft_transports_from_env(request.operator_env),
                        mode="live-draft",
                    ),
                ),
                credential_resolver=self._credential_resolver(request),
                middleware_factories=self.middleware_factories,
            )
        if request.mode is WorkflowRunMode.LIVE_PRODUCTION:
            return run_live_production_workflow(
                request,
                broker=self._with_package_external_actions(
                    request,
                    self.live_production_broker or LiveWorkflowBroker(
                        self._live_production_transports(request),
                        mode="live-production",
                        production_enabled=True,
                    ),
                ),
                credential_resolver=self._credential_resolver(request),
                middleware_factories=self.middleware_factories,
            )
        return run_workflow(request)

    def _with_operator_env(self, request: WorkflowRunRequest) -> WorkflowRunRequest:
        operator_env = {**dict(self.operator_env), **dict(request.operator_env)}
        return replace(request, operator_env=operator_env)

    def _credential_resolver(self, request: WorkflowRunRequest) -> CredentialRefResolver:
        if self.credential_resolver is not None:
            return self.credential_resolver
        if self.connector_config is not None:
            return credential_resolver_for_package_connector_config(request.package_path, self._connector_config(request))
        return credential_resolver_for_package_env(request.package_path, request.operator_env)

    def _connector_config(self, request: WorkflowRunRequest) -> ConnectorConfigResolver:
        if self.connector_config is not None:
            return self.connector_config
        return ConnectorConfigResolver(env=request.operator_env)

    def _live_read_transports(self, request: WorkflowRunRequest):
        transports = dict(build_live_read_transports_from_env(request.operator_env))
        transports.update(build_live_read_transports_from_connector_config(self._connector_config(request)))
        return transports

    def _live_production_transports(self, request: WorkflowRunRequest):
        transports = dict(build_live_production_transports_from_env(request.operator_env))
        transports.update(build_live_production_transports_from_connector_config(self._connector_config(request)))
        return transports

    def _with_package_external_actions(
        self,
        request: WorkflowRunRequest,
        broker: LiveWorkflowBroker,
    ) -> LiveWorkflowBroker:
        documents = load_package_documents(request.package_path)
        return broker.with_external_actions(external_connector_action_refs(documents.connectors))
