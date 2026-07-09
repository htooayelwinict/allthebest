from __future__ import annotations

from pathlib import Path

from appv24.workflows.capability_catalog import build_workflow_capability_catalog
from appv24.workflows.extension_catalog import (
    format_workflow_extension_catalog_authoring_context,
    format_workflow_extension_catalog_promotion,
    load_workflow_extension_catalog,
    promote_package_extensions_to_catalog,
)
from appv24.workflows.loader import PackageDocuments


def test_promote_package_extensions_writes_reusable_catalog_and_loader_feeds_capability_catalog(tmp_path: Path) -> None:
    documents = _package_with_passed_connector_qa(root=tmp_path / "package")
    output_path = tmp_path / "workflow-extension-catalog.yaml"

    promotion = promote_package_extensions_to_catalog(documents, output_path)
    loaded = load_workflow_extension_catalog(output_path)
    catalog = build_workflow_capability_catalog(extension_profile=loaded.extension_profile)
    by_action = {item.action_ref: item for item in catalog.capabilities}

    assert promotion.status == "promoted"
    assert promotion.output_path == output_path.resolve()
    assert promotion.promoted_ids == ("crm.lookup_contact",)
    assert promotion.skipped_ids == ()
    assert loaded.version == "appv24.workflow-extension-catalog/v1"
    assert loaded.package_fingerprint.startswith("sha256:")
    assert "crm.lookup_contact" in by_action
    assert by_action["crm.lookup_contact"].label == "Lookup CRM contact"
    assert by_action["crm.lookup_contact"].group == "Read data"
    assert by_action["crm.lookup_contact"].required_env_names == ("APPV24_CRM_MCP_SERVER",)
    rendered = output_path.read_text(encoding="utf-8")
    assert "APPV24_CRM_MCP_SERVER" in rendered
    assert "secret-value" not in rendered


def test_extension_catalog_authoring_context_includes_redacted_connector_declaration(tmp_path: Path) -> None:
    documents = _package_with_passed_connector_qa(root=tmp_path / "package")
    output_path = tmp_path / "workflow-extension-catalog.yaml"
    promote_package_extensions_to_catalog(documents, output_path)
    loaded = load_workflow_extension_catalog(output_path)

    context = format_workflow_extension_catalog_authoring_context(loaded)

    assert "Reusable extension package declarations" in context
    assert "connectors.yaml entries to copy" in context
    assert "permissions.yaml entries to copy" in context
    assert "crm.lookup_contact" in context
    assert "custom: true" in context
    assert "source: mcp://crm.lookup_contact@v1" in context
    assert "qa:" in context
    assert "adapter_contract: passed" in context
    assert "APPV24_CRM_MCP_SERVER" in context
    assert "secret-value" not in context


def test_promote_package_middleware_writes_reusable_catalog_and_authoring_context(tmp_path: Path) -> None:
    documents = _package_with_passed_middleware_qa(root=tmp_path / "package")
    output_path = tmp_path / "workflow-extension-catalog.yaml"

    promotion = promote_package_extensions_to_catalog(documents, output_path)
    loaded = load_workflow_extension_catalog(output_path)
    catalog = build_workflow_capability_catalog(extension_profile=loaded.extension_profile)
    context = format_workflow_extension_catalog_authoring_context(loaded)
    by_middleware = {item.middleware_id: item for item in catalog.middleware_capabilities}

    assert promotion.status == "promoted"
    assert promotion.promoted_ids == ("team.redactor",)
    assert promotion.skipped_ids == ()
    assert "team.redactor" in by_middleware
    assert by_middleware["team.redactor"].label == "Team redactor"
    assert by_middleware["team.redactor"].required_env_names == ("APPV24_TEAM_REDACTOR_LEVEL",)
    assert "middlewares.yaml entries to copy" in context
    assert "team.redactor" in context
    assert "adapter: local" in context
    assert "APPV24_TEAM_REDACTOR_LEVEL" in context
    assert "secret-value" not in context


def test_promote_package_extensions_refuses_missing_qa(tmp_path: Path) -> None:
    documents = _package_with_passed_connector_qa(root=tmp_path / "package", include_qa=False)

    promotion = promote_package_extensions_to_catalog(documents, tmp_path / "workflow-extension-catalog.yaml")

    assert promotion.status == "blocked"
    assert promotion.promoted_ids == ()
    assert promotion.output_path is None
    assert any("crm.lookup_contact requires passed extension QA evidence" in issue for issue in promotion.issues)


def test_workflows_package_exports_extension_catalog_api() -> None:
    from appv24.workflows import (
        format_workflow_extension_catalog_authoring_context as exported_authoring_formatter,
        load_workflow_extension_catalog as exported_loader,
        format_workflow_extension_catalog_promotion as exported_formatter,
        promote_package_extensions_to_catalog as exported_promoter,
    )

    assert exported_authoring_formatter is format_workflow_extension_catalog_authoring_context
    assert exported_loader is load_workflow_extension_catalog
    assert exported_formatter is format_workflow_extension_catalog_promotion
    assert exported_promoter is promote_package_extensions_to_catalog


def _package_with_passed_connector_qa(*, root: Path, include_qa: bool = True) -> PackageDocuments:
    qa = {
        "version": "appv24.extension_qa/v1",
        "kind": "connector",
        "extension_id": "crm.lookup_contact",
        "source": "mcp://crm.lookup_contact@v1",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": "pytest appV2.4/tests/test_crm_connector.py", "status": "passed"}],
            "side_effect_policy": "passed",
            "runtime_boundary": "passed",
            "adapter_contract": "passed",
        },
        "contract": {
            "required_scopes": ["crm:read"],
            "allowed_outputs": ["contact", "artifact_path"],
        },
        "fingerprints": {
            "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
            "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
        },
    }
    action: dict[str, object] = {
        "id": "lookup_contact",
        "custom": True,
        "source": "mcp://crm.lookup_contact@v1",
        "tool": "crm.lookup_contact",
        "side_effect": False,
        "side_effect_class": "read",
        "execution_mode": "brokered",
        "dry_run_supported": True,
        "required_scopes": ["crm:read"],
        "outputs": {"allowed": ["contact", "artifact_path"]},
        "workflow_hints": {
            "catalog_group": "Read data",
            "catalog_label": "Lookup CRM contact",
            "generated_by": "coding_profile",
            "input_fields": ["email"],
            "qa_required": True,
            "required_input_fields": ["email"],
        },
    }
    if include_qa:
        action["qa"] = qa
    return PackageDocuments(
        root=root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "crm_lookup",
            "description": "Look up CRM contacts through a reusable extension connector.",
            "design": {
                "intent": "Look up CRM contacts by email.",
                "trigger": "Manual support workflow.",
                "inputs": ["Customer email address."],
                "outputs": ["CRM contact record."],
                "assumptions": ["The CRM connector implementation has passed extension QA."],
                "side_effects": ["Read-only CRM lookup."],
                "approval_gates": ["None."],
                "failure_modes": ["CRM connector unavailable."],
                "rollback": ["No external mutation occurs."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "lookup_contact",
            "steps": [
                {"id": "lookup_contact", "uses": "crm.lookup_contact", "with": {"email": "{{ inputs.email }}"}, "next": "done"},
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "crm",
                    "adapter": "mcp",
                    "custom": True,
                    "source": "mcp://crm@v1",
        "env": [
            {
                "name": "APPV24_CRM_MCP_SERVER",
                "purpose": "CRM MCP server endpoint",
                "required": True,
                "value": "secret-value",
            }
        ],
                    "actions": [action],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "crm", "actions": ["lookup_contact"], "scopes": ["crm:read"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {"email": "customer@example.com"}}},
        rollback_text=None,
    )


def _package_with_passed_middleware_qa(*, root: Path) -> PackageDocuments:
    return PackageDocuments(
        root=root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "redacted_audit",
            "description": "Use a generated middleware to redact audit fields.",
            "design": {
                "intent": "Redact sensitive audit fields.",
                "trigger": "Manual workflow.",
                "inputs": [],
                "outputs": ["Redacted audit events."],
                "assumptions": ["The redactor middleware has passed extension QA."],
                "side_effects": [],
                "approval_gates": [],
                "failure_modes": ["Middleware unavailable."],
                "rollback": ["Disable the middleware."],
            },
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "search_ai_news",
            "steps": [
                {
                    "id": "search_ai_news",
                    "uses": "brave.search",
                    "with": {"query": "AI news", "count": 3},
                    "next": "done",
                },
                {"id": "done", "type": "terminal"},
            ],
        },
        connectors={
            "version": "appv24.connectors/v1",
            "connectors": [
                {
                    "id": "brave",
                    "adapter": "direct_api",
                    "actions": [{"id": "search", "side_effect": False, "required_scopes": ["brave:search"]}],
                }
            ],
        },
        permissions={
            "version": "appv24.permissions/v1",
            "permissions": [{"connector": "brave", "actions": ["search"], "scopes": ["brave:search"]}],
        },
        dry_run_fixtures={"happy_path.yaml": {"input": {}}},
        rollback_text=None,
        middlewares={
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "local",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "config": {"fields": ["token"], "secret": "secret-value"},
                    "env": [
                        {
                            "name": "APPV24_TEAM_REDACTOR_LEVEL",
                            "purpose": "redaction level",
                            "required": True,
                            "value": "secret-value",
                        }
                    ],
                    "audit": ["middleware:team.redactor"],
                    "applies_to": ["local", "live-production"],
                    "workflow_hints": {
                        "catalog_label": "Team redactor",
                        "generated_by": "coding_profile",
                        "qa_required": True,
                    },
                    "qa": {
                        "version": "appv24.extension_qa/v1",
                        "kind": "middleware",
                        "extension_id": "team.redactor",
                        "source": "local://middlewares/team/redactor@sha256:abc123",
                        "status": "passed",
                        "qa": {
                            "schema_validation": "passed",
                            "tests": [{"command": "pytest appV2.4/tests/test_team_redactor.py", "status": "passed"}],
                            "side_effect_policy": "passed",
                            "runtime_boundary": "passed",
                            "adapter_contract": "passed",
                        },
                        "fingerprints": {
                            "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                            "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                            "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
                            "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
                        },
                    },
                }
            ],
        },
    )
