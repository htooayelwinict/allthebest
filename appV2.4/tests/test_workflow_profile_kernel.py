from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows import profile_kernel
from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.bundle_export import write_workflow_bundle
from appv24.workflows.connector_user_config import ConnectorConfigProfile, ConnectorConfigResolver, ConnectorConfigStore
from appv24.workflows.credential_refs import CredentialRefResolver, CredentialScope
from appv24.workflows.drafts import save_workflow_draft_blocks
from appv24.workflows.execution import WorkflowRunMode, WorkflowRunRequest
from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.live_broker import LiveWorkflowBroker, LiveWorkflowTransport
from appv24.workflows.middleware_runtime import MiddlewareExecutor
from appv24.workflows.validator import validate_workflow_draft_text
from appv24.workflows import WorkflowProfileKernel


class _RecordingSourceTransport(LiveWorkflowTransport):
    def invoke(self, request: BrokerRequest) -> BrokerResult:
        return BrokerResult(
            status="live-read",
            provider_id="source-test",
            outputs={"items": [{"title": "Kernel route"}], "source_count": 1},
        )


class _RecordingCrmTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-read",
            provider_id="crm-test",
            outputs={"contact": {"email": "customer@example.com", "tier": "enterprise"}, "artifact_path": "crm.json"},
        )


class _RecordingSearchTransport(LiveWorkflowTransport):
    def __init__(self) -> None:
        self.requests: list[BrokerRequest] = []

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        self.requests.append(request)
        return BrokerResult(
            status="live-read",
            provider_id="brave-test",
            outputs={"items": [{"title": "Kernel config route"}], "source_count": 1, "query": "kernel"},
        )


def test_profile_kernel_prepare_reports_connector_and_middleware_catalogs(tmp_path: Path) -> None:
    package = _write_external_crm_package(tmp_path)
    _write_audit_log_middleware(package)

    report = WorkflowProfileKernel().prepare(package)

    assert report.status == "ready"
    assert report.workflow_name == "external-crm-read"
    assert report.package_fingerprint.startswith("sha256:")
    assert report.connector_actions == ("crm.lookup_contact",)
    assert len(report.connector_interfaces) == 1
    assert report.connector_interfaces[0].action_ref == "crm.lookup_contact"
    assert report.connector_interfaces[0].external is True
    assert report.connector_interfaces[0].source_ref == "mcp://crm.lookup_contact@v1"
    assert report.connector_env_requirements == ("APPV24_CRM_MCP_SERVER",)
    assert report.middleware_ids == ("audit.log",)
    assert report.middleware_env_requirements == ("APPV24_WORKFLOW_AUDIT_LOG_PATH",)
    assert len(report.middleware_interfaces) == 1
    assert report.middleware_interfaces[0].middleware_id == "audit.log"
    assert report.middleware_interfaces[0].executor_available is True
    assert report.middleware_interfaces[0].env_ready is False
    assert report.runtime_modes == ("local",)
    assert report.issues == ()


def test_profile_kernel_run_live_read_dispatches_through_connector_broker(tmp_path: Path) -> None:
    package = _write_source_read_package(tmp_path)
    write_workflow_bundle(package, env={})

    report = WorkflowProfileKernel(
        live_read_broker=LiveWorkflowBroker({"source.fetch_items": _RecordingSourceTransport()}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"source": CredentialScope(available=True, scopes=("source:read",))}),
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("source.fetch_items",),
            run_root=tmp_path / ".workflow-runs",
        )
    )

    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert [step.connector_action for step in report.step_trace if step.connector_action] == ["source.fetch_items"]


def test_profile_kernel_run_live_read_dispatches_external_connector_extension(tmp_path: Path) -> None:
    package = _write_external_crm_package(tmp_path)
    transport = _RecordingCrmTransport()

    report = WorkflowProfileKernel(
        operator_env={"APPV24_CRM_MCP_SERVER": "mcp://crm"},
        live_read_broker=LiveWorkflowBroker({"crm.lookup_contact": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"crm": CredentialScope(available=True, scopes=("crm:read",))}),
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("crm.lookup_contact",),
            run_root=tmp_path / ".workflow-runs",
        )
    )

    assert report.status == "passed"
    assert report.terminal_step == "done"
    assert [step.connector_action for step in report.step_trace if step.connector_action] == ["crm.lookup_contact"]
    assert transport.requests[0].connector_action == "crm.lookup_contact"
    assert transport.requests[0].allowed_outputs == ("contact", "artifact_path")


def test_profile_kernel_default_live_read_uses_connector_user_config(monkeypatch, tmp_path: Path) -> None:
    package = _write_brave_search_package(tmp_path)
    write_workflow_bundle(package, env={})
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-user",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key"},
            source="user-config",
        )
    )
    transport = _RecordingSearchTransport()
    resolver = ConnectorConfigResolver(store=store, env={"APPV24_WORKFLOW_LIVE_READ_ENABLED": "true"})
    monkeypatch.setattr(
        profile_kernel,
        "build_live_read_transports_from_connector_config",
        lambda connector_config: {"brave.search": transport},
    )

    report = WorkflowProfileKernel(
        operator_env={"APPV24_WORKFLOW_LIVE_READ_ENABLED": "true"},
        connector_config=resolver,
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("brave.search",),
            run_root=tmp_path / ".workflow-runs",
        )
    )

    assert report.status == "passed"
    assert transport.requests[0].connector_action == "brave.search"


def test_profile_kernel_run_local_uses_configured_middleware_runtime(tmp_path: Path) -> None:
    package = _write_golden_package(tmp_path, "telegram_support_ticket")
    _write_audit_log_middleware(package)
    audit_path = tmp_path / "workflow-audit.jsonl"

    report = WorkflowProfileKernel(
        operator_env={"APPV24_WORKFLOW_AUDIT_LOG_PATH": str(audit_path)}
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "passed"
    assert report.terminal_step == "done"
    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [event["hook"] for event in audit_events if event["hook"] in {"run_started", "run_completed"}] == [
        "run_started",
        "run_completed",
    ]


def test_profile_kernel_blocks_custom_middleware_without_executor_factory(tmp_path: Path) -> None:
    package = _write_golden_package(tmp_path, "telegram_support_ticket")
    _write_custom_middleware(package)

    report = WorkflowProfileKernel(
        operator_env={"APPV24_TEAM_REDACTOR_LEVEL": "strict"}
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "blocked"
    assert report.terminal_step is None
    assert "middleware runtime not configured for team.redactor" in report.issues


def test_profile_kernel_run_local_uses_registered_custom_middleware_executor(tmp_path: Path) -> None:
    package = _write_golden_package(tmp_path, "telegram_support_ticket")
    _write_custom_middleware(package)
    record: dict[str, object] = {}

    class _TeamRedactor(MiddlewareExecutor):
        middleware_id = "team.redactor"

        def __init__(self, definition, env) -> None:
            self.hooks: list[str] = []
            record["definition_id"] = definition.middleware_id
            record["definition_source"] = definition.source_ref
            record["definition_config"] = definition.config
            record["env_value"] = env["APPV24_TEAM_REDACTOR_LEVEL"]

        def emit(self, hook, context, details) -> None:
            self.hooks.append(hook)
            record["hooks"] = list(self.hooks)

    report = WorkflowProfileKernel(
        operator_env={"APPV24_TEAM_REDACTOR_LEVEL": "strict"},
        middleware_factories={"team.redactor": lambda definition, env: _TeamRedactor(definition, env)},
    ).run(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LOCAL,
            run_root=tmp_path / ".workflow-runs",
            approve_all=True,
        )
    )

    assert report.status == "passed"
    assert record["definition_id"] == "team.redactor"
    assert record["definition_source"] == "local://middlewares/team/redactor@sha256:abc123"
    assert record["definition_config"] == {"fields": ["token", "secret"]}
    assert record["env_value"] == "strict"
    assert "run_started" in record["hooks"]
    assert "run_completed" in record["hooks"]


def test_profile_kernel_accepts_generated_package_with_external_connector_and_middleware(tmp_path: Path) -> None:
    generated_source = _write_external_crm_package(tmp_path / "source")
    _write_custom_middleware(generated_source)
    draft = _draft_text_from_package(generated_source)
    validation = validate_workflow_draft_text(draft)
    transport = _RecordingCrmTransport()
    record: dict[str, object] = {}

    class _TeamRedactor(MiddlewareExecutor):
        middleware_id = "team.redactor"

        def __init__(self, definition, env) -> None:
            record["definition_id"] = definition.middleware_id
            record["source_ref"] = definition.source_ref
            record["env_value"] = env["APPV24_TEAM_REDACTOR_LEVEL"]
            self.hooks: list[str] = []

        def emit(self, hook, context, details) -> None:
            self.hooks.append(hook)
            record["hooks"] = list(self.hooks)

    assert validation.ok
    saved_package = save_workflow_draft_blocks(
        validation.blocks,
        cwd=tmp_path,
        relative_path="generated/external-crm-read",
    )
    kernel = WorkflowProfileKernel(
        operator_env={
            "APPV24_CRM_MCP_SERVER": "mcp://crm",
            "APPV24_TEAM_REDACTOR_LEVEL": "strict",
        },
        live_read_broker=LiveWorkflowBroker({"crm.lookup_contact": transport}, mode="live-read"),
        credential_resolver=CredentialRefResolver({"crm": CredentialScope(available=True, scopes=("crm:read",))}),
        middleware_factories={"team.redactor": lambda definition, env: _TeamRedactor(definition, env)},
    )

    prepared = kernel.prepare(saved_package)
    report = kernel.run(
        WorkflowRunRequest(
            package_path=saved_package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("crm.lookup_contact",),
            run_root=tmp_path / ".workflow-runs",
        )
    )

    assert prepared.status == "ready"
    assert prepared.connector_interfaces[0].external is True
    assert prepared.middleware_interfaces[0].middleware_id == "team.redactor"
    assert prepared.middleware_interfaces[0].executor_available is True
    assert report.status == "passed"
    assert transport.requests[0].connector_action == "crm.lookup_contact"
    assert record["definition_id"] == "team.redactor"
    assert record["source_ref"] == "local://middlewares/team/redactor@sha256:abc123"
    assert record["env_value"] == "strict"
    assert "run_started" in record["hooks"]
    assert "run_completed" in record["hooks"]


def _write_external_crm_package(root: Path) -> Path:
    package = root / "external-crm-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "external-crm-read",
                "description": "Read CRM contact data through an external connector interface.",
                "design": {
                    "intent": "Look up one CRM contact by email.",
                    "trigger": "Manual operator run.",
                    "inputs": ["Customer email address."],
                    "outputs": ["CRM contact fields."],
                    "assumptions": ["CRM MCP server is configured by the operator."],
                    "side_effects": ["Read-only CRM lookup."],
                    "approval_gates": ["None."],
                    "failure_modes": ["CRM connector unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "lookup_contact",
                "steps": [
                    {
                        "id": "lookup_contact",
                        "uses": "crm.lookup_contact",
                        "with": {"email": {"inputRef": "email"}},
                        "next": "done",
                    },
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "crm",
                        "adapter": "mcp",
                        "source": "mcp://crm@v1",
                        "env": [
                            {
                                "name": "APPV24_CRM_MCP_SERVER",
                                "purpose": "CRM MCP server endpoint",
                                "required": True,
                            }
                        ],
                        "actions": [
                            {
                                "id": "lookup_contact",
                                "custom": True,
                                "source": "mcp://crm.lookup_contact@v1",
                                "tool": "crm.lookup_contact",
                                "side_effect": False,
                                "side_effect_class": "read",
                                "execution_mode": "brokered",
                                "dry_run_supported": True,
                                "outputs": {"allowed": ["contact", "artifact_path"]},
                                "required_scopes": ["crm:read"],
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text(
        yaml.safe_dump({"input": {"email": "customer@example.com"}}, sort_keys=False),
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_source_read_package(root: Path) -> Path:
    package = root / "source-read"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "source-read",
                "description": "Read source items through a connector broker.",
                "design": {
                    "intent": "Read source items.",
                    "trigger": "Manual operator run.",
                    "inputs": ["query"],
                    "outputs": ["items"],
                    "assumptions": ["Source credentials are configured outside the package."],
                    "side_effects": ["Read-only source lookup."],
                    "approval_gates": ["None."],
                    "failure_modes": ["Source connector unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "fetch_items",
                "steps": [
                    {"id": "fetch_items", "uses": "source.fetch_items", "with": {"query": "kernel"}, "next": "done"},
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "source",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "fetch_items",
                                "operationId": "source_fetch_items",
                                "side_effect": False,
                                "outputs": {"allowed": ["items", "source_count", "artifact_path"]},
                                "required_scopes": ["source:read"],
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "source", "actions": ["fetch_items"], "scopes": ["source:read"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  query: kernel\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_brave_search_package(root: Path) -> Path:
    package = root / "brave-search"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "brave-search",
                "description": "Read public search results through Brave.",
                "design": {
                    "intent": "Search public news.",
                    "trigger": "Manual operator run.",
                    "inputs": ["query"],
                    "outputs": ["items"],
                    "assumptions": ["Brave Search is configured outside the package."],
                    "side_effects": ["Read-only public search."],
                    "approval_gates": ["None."],
                    "failure_modes": ["Search API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "search",
                "steps": [
                    {"id": "search", "uses": "brave.search", "with": {"query": "kernel"}, "next": "done"},
                    {"id": "done", "type": "terminal"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "connectors.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.connectors/v1",
                "connectors": [
                    {
                        "id": "brave",
                        "adapter": "direct_api",
                        "actions": [
                            {
                                "id": "search",
                                "operationId": "brave_search",
                                "side_effect": False,
                                "outputs": {"allowed": ["items", "source_count", "query"]},
                                "required_scopes": ["brave:search"],
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (package / "permissions.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.permissions/v1",
                "permissions": [{"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    (fixtures / "happy_path.yaml").write_text("input:\n  query: kernel\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_audit_log_middleware(package: Path) -> None:
    (package / "middlewares.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.middlewares/v1",
                "middlewares": [
                    {
                        "id": "audit.log",
                        "adapter": "builtin",
                        "source": "catalog://audit.log@v1",
                        "config": {"events": True},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_custom_middleware(package: Path) -> None:
    (package / "middlewares.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.middlewares/v1",
                "middlewares": [
                    {
                        "id": "team.redactor",
                        "adapter": "local",
                        "source": "local://middlewares/team/redactor@sha256:abc123",
                        "config": {"fields": ["token", "secret"]},
                        "env": [
                            {
                                "name": "APPV24_TEAM_REDACTOR_LEVEL",
                                "purpose": "team redaction level",
                                "required": True,
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_golden_package(root: Path, example_name: str) -> Path:
    example = golden_workflow_examples()[example_name]
    package = root / example_name
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(yaml.safe_dump(dict(example.documents.workflow), sort_keys=False), encoding="utf-8")
    (package / "connectors.yaml").write_text(yaml.safe_dump(dict(example.documents.connectors), sort_keys=False), encoding="utf-8")
    (package / "permissions.yaml").write_text(yaml.safe_dump(dict(example.documents.permissions), sort_keys=False), encoding="utf-8")
    fixtures = package / "dry_run_fixtures"
    fixtures.mkdir()
    for name, fixture in example.documents.dry_run_fixtures.items():
        (fixtures / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
    if example.documents.rollback_text is not None:
        (package / "rollback.md").write_text(example.documents.rollback_text, encoding="utf-8")
    return package


def _draft_text_from_package(package: Path) -> str:
    blocks = [
        f"```yaml workflow.yaml\n{(package / 'workflow.yaml').read_text(encoding='utf-8')}```",
        f"```yaml connectors.yaml\n{(package / 'connectors.yaml').read_text(encoding='utf-8')}```",
        f"```yaml permissions.yaml\n{(package / 'permissions.yaml').read_text(encoding='utf-8')}```",
    ]
    middlewares = package / "middlewares.yaml"
    if middlewares.exists():
        blocks.append(f"```yaml middlewares.yaml\n{middlewares.read_text(encoding='utf-8')}```")
    for fixture in sorted((package / "dry_run_fixtures").glob("*.yaml")):
        blocks.append(
            f"```yaml dry_run_fixtures/{fixture.name}\n{fixture.read_text(encoding='utf-8')}```"
        )
    rollback = package / "rollback.md"
    if rollback.exists():
        blocks.append(f"```markdown rollback.md\n{rollback.read_text(encoding='utf-8')}```")
    return "\n\n".join(blocks)
