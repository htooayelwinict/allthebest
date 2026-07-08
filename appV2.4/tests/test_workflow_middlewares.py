from __future__ import annotations

from pathlib import Path

import yaml

from appv24.workflows.loader import load_package_documents
from appv24.workflows.middleware_contracts import (
    builtin_middleware_contract_catalog,
    middleware_audit_requirements,
    middleware_env_requirements,
    middleware_state_requirements,
    package_middleware_ids,
    validate_middleware_contracts,
)
from appv24.workflows.middleware_registry import inspect_workflow_middleware_registry
from appv24.workflows.middleware_runtime import MiddlewareExecutor, MiddlewareRuntime
from appv24.workflows.schema import validate_schema_documents


def test_load_package_documents_reads_optional_middlewares_yaml(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(package)

    documents = load_package_documents(package)

    assert documents.middlewares["version"] == "appv24.middlewares/v1"
    assert package_middleware_ids(documents) == ("audit.log",)
    assert validate_schema_documents(documents) == []


def test_builtin_middleware_contracts_are_independent_runtime_interfaces(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(package)
    documents = load_package_documents(package)

    catalog = builtin_middleware_contract_catalog()
    contract = catalog["audit.log"]

    assert set(catalog) == {"audit.log"}
    assert contract.middleware_id == "audit.log"
    assert contract.adapter == "builtin"
    assert contract.source_ref == "catalog://audit.log@v1"
    assert contract.env[0].env_name == "APPV24_WORKFLOW_AUDIT_LOG_PATH"
    assert middleware_env_requirements(documents) == ("APPV24_WORKFLOW_AUDIT_LOG_PATH",)
    assert middleware_state_requirements(documents) == ()
    assert middleware_audit_requirements(documents) == ("middleware:audit.log",)
    assert validate_middleware_contracts(documents) == []


def test_unknown_builtin_middleware_is_rejected_without_using_workflow_or_connector_shape(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(
        package,
        {
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "unknown.audit",
                    "adapter": "builtin",
                    "source": "catalog://unknown.audit@v1",
                    "config": {},
                }
            ],
        },
    )
    documents = load_package_documents(package)

    issues = validate_middleware_contracts(documents)

    assert any(issue.code == "unknown_middleware_contract" for issue in issues)
    assert all(issue.file == "middlewares.yaml" for issue in issues)


def test_custom_middleware_requires_appv24_env_namespace(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(
        package,
        {
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "external",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "env": [
                        {"name": "REDAC_LEVEL", "purpose": "redaction level", "required": True},
                    ],
                }
            ],
        },
    )
    documents = load_package_documents(package)

    issues = validate_middleware_contracts(documents)

    assert any(issue.code == "middleware_env_namespace" for issue in issues)


def test_middleware_runtime_uses_middleware_executor_interface() -> None:
    class _Recorder(MiddlewareExecutor):
        middleware_id = "team.recorder"

        def __init__(self) -> None:
            self.hooks: list[str] = []

        def emit(self, hook, context, details) -> None:
            self.hooks.append(hook)

    recorder = _Recorder()
    runtime = MiddlewareRuntime((recorder,))

    assert runtime.emit("run_started", context=None, status="started") == ()
    assert recorder.hooks == ["run_started"]


def test_middleware_registry_reports_external_interface_and_executor_configuration(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(
        package,
        {
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "local",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "config": {"fields": ["token", "secret"]},
                    "env": [
                        {"name": "APPV24_TEAM_REDACTOR_LEVEL", "purpose": "redaction level", "required": True},
                    ],
                    "audit": ["middleware:team.redactor"],
                    "applies_to": ["local"],
                }
            ],
        },
    )

    class _Noop(MiddlewareExecutor):
        middleware_id = "team.redactor"

        def emit(self, hook, context, details) -> None:
            return None

    report = inspect_workflow_middleware_registry(
        package,
        env={"APPV24_TEAM_REDACTOR_LEVEL": "strict"},
        middleware_factories={"team.redactor": lambda definition, env: _Noop()},
    )

    assert report.workflow_name == "middleware_package"
    assert report.issues == ()
    assert len(report.middlewares) == 1
    middleware = report.middlewares[0]
    assert middleware.middleware_id == "team.redactor"
    assert middleware.adapter == "local"
    assert middleware.source_ref == "local://middlewares/team/redactor@sha256:abc123"
    assert middleware.cataloged is True
    assert middleware.executor_available is True
    assert middleware.env_ready is True
    assert middleware.required_env == ("APPV24_TEAM_REDACTOR_LEVEL",)
    assert middleware.config_keys == ("fields",)
    assert middleware.audit_requirements == ("middleware:team.redactor",)
    assert middleware.applies_to == ("local",)
    assert middleware.issues == ()


def test_middleware_registry_reports_missing_external_executor(tmp_path: Path) -> None:
    package = _write_read_only_package(tmp_path)
    _write_middlewares_yaml(
        package,
        {
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "local",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "env": [
                        {"name": "APPV24_TEAM_REDACTOR_LEVEL", "purpose": "redaction level", "required": True},
                    ],
                }
            ],
        },
    )

    report = inspect_workflow_middleware_registry(package, env={})

    assert report.middlewares[0].executor_available is False
    assert report.middlewares[0].env_ready is False
    assert "middleware runtime not configured for team.redactor" in report.issues
    assert "missing middleware env for team.redactor; required env: APPV24_TEAM_REDACTOR_LEVEL" in report.issues


def test_workflows_package_exports_middleware_registry() -> None:
    from appv24.workflows import inspect_workflow_middleware_registry as exported

    assert exported is inspect_workflow_middleware_registry


def _write_read_only_package(root: Path) -> Path:
    package = root / "middleware-package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "middleware_package",
                "description": "Read source data with middleware metadata.",
                "design": {
                    "intent": "Read public source data.",
                    "trigger": "Manual run.",
                    "inputs": ["query"],
                    "outputs": ["items"],
                    "assumptions": ["Credentials are resolved outside the package."],
                    "side_effects": [],
                    "approval_gates": [],
                    "failure_modes": ["Source API unavailable."],
                    "rollback": ["No external mutation occurs."],
                },
                "triggers": [{"id": "manual", "type": "manual"}],
                "entrypoint": "fetch",
                "steps": [
                    {"id": "fetch", "uses": "source.fetch_items", "with": {"query": "AI news"}, "next": "done"},
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
                                "outputs": {"allowed": ["items", "source_count"]},
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
    (fixtures / "happy_path.yaml").write_text("input:\n  query: AI news\n", encoding="utf-8")
    (package / "rollback.md").write_text("# Rollback\n\nNo external mutation occurs.\n", encoding="utf-8")
    return package


def _write_middlewares_yaml(package: Path, payload: dict[str, object] | None = None) -> None:
    (package / "middlewares.yaml").write_text(
        yaml.safe_dump(
            payload
            or {
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
