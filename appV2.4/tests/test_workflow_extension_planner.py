from __future__ import annotations

import json
from pathlib import Path

import yaml

from appv24.workflows.extension_handoff import (
    apply_extension_handoff_result,
    apply_returned_extension_handoff_results,
    dispatch_extension_handoff,
    format_workflow_extension_handoff,
    format_workflow_extension_handoff_apply_report,
    format_workflow_extension_returned_qa_apply_report,
    format_workflow_extension_handoff_dispatch_report,
    write_extension_handoff_for_validation_issues,
)
from appv24.workflows.extension_planner import (
    WorkflowExtensionBuildRequest,
    build_connector_extension_request,
    build_extension_requests_for_validation_issues,
    build_middleware_extension_request,
    render_extension_prd_prompt,
)
from appv24.workflows.loader import PackageDocuments, load_package_documents
from appv24.workflows.validator import validate_workflow_documents


def test_connector_extension_planner_renders_coder_profile_prd_prompt() -> None:
    request = WorkflowExtensionBuildRequest(
        kind="connector",
        extension_id="crm.lookup_contact",
        business_goal="Look up CRM contacts by email for support workflows.",
        input_schema={"type": "object", "required": ["email"], "properties": {"email": {"type": "string"}}},
        output_schema={"type": "object", "required": ["contact"], "properties": {"contact": {"type": "object"}}},
        scopes=("crm:read",),
        env_requirements=("APPV24_CRM_MCP_SERVER",),
        side_effect_class="read",
        approval_class="none",
        idempotency_key_fields=(),
        dry_run_fixtures=("happy_path.yaml", "not_found.yaml"),
        qa_commands=("PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_crm_connector.py",),
    )

    prompt = render_extension_prd_prompt(request)

    assert "# appv24 connector extension PRD: crm.lookup_contact" in prompt
    assert "Business goal: Look up CRM contacts by email for support workflows." in prompt
    assert "Scopes: crm:read" in prompt
    assert "Environment/config names: APPV24_CRM_MCP_SERVER" in prompt
    assert "Side-effect class: read" in prompt
    assert "Approval class: none" in prompt
    assert "Dry-run fixtures: happy_path.yaml, not_found.yaml" in prompt
    assert "Runtime must not call the coding profile" in prompt
    assert "Write failing tests before implementation" in prompt
    assert "appv24.extension_qa/v1 manifest" in prompt
    assert "You are running with --cwd set to the workflow package directory." in prompt
    assert "Do not inspect or modify files outside that package directory." in prompt
    assert "Write the QA manifest file to the exact return path before exiting." in prompt
    assert "Do not finish by only describing the QA manifest." in prompt
    assert "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_BEGIN" in prompt
    assert "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_END" in prompt
    assert "appv24.workflow-extension-artifact-bundle/v1" in prompt
    assert '"files": [' in prompt
    assert '"qa_manifest": {' in prompt
    assert "Prefer this bundle return over direct package file writes." in prompt
    assert "version: appv24.extension_qa/v1" in prompt
    assert "adapter contract result" in prompt
    assert "qa.adapter_contract: passed" in prompt
    assert "contract.required_scopes" in prompt
    assert "contract.allowed_outputs" in prompt
    assert "fingerprints for spec, implementation, tests, and fixtures" in prompt
    assert "fingerprints must be sha256:<64 lowercase hex characters>" in prompt
    assert "pytest appV2.4/tests/test_crm_connector.py" in prompt
    assert "secret-value" not in prompt


def test_middleware_extension_planner_renders_runtime_interface_boundary() -> None:
    request = WorkflowExtensionBuildRequest(
        kind="middleware",
        extension_id="team.redactor",
        business_goal="Redact configured sensitive fields from workflow audit events.",
        input_schema={"type": "object", "properties": {"event": {"type": "object"}}},
        output_schema={"type": "object", "properties": {"event": {"type": "object"}}},
        scopes=("middleware:team.redactor",),
        env_requirements=("APPV24_TEAM_REDACTOR_LEVEL",),
        side_effect_class="read",
        approval_class="none",
        dry_run_fixtures=("happy_path.yaml",),
        qa_commands=("PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_team_redactor.py",),
    )

    prompt = render_extension_prd_prompt(request)

    assert "# appv24 middleware extension PRD: team.redactor" in prompt
    assert "Adapter boundary: middleware runtime interface" in prompt
    assert "Environment/config names: APPV24_TEAM_REDACTOR_LEVEL" in prompt
    assert "Runtime must consume only the pinned validated artifact" in prompt
    assert "Do not add workflow-runtime special cases for this middleware" in prompt


def test_extension_planner_renders_catalog_registration_choice() -> None:
    package_only = render_extension_prd_prompt(
        WorkflowExtensionBuildRequest(
            kind="connector",
            extension_id="crm.lookup_contact",
            business_goal="Look up CRM contacts.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            catalog_registration="package",
        )
    )
    reusable = render_extension_prd_prompt(
        WorkflowExtensionBuildRequest(
            kind="connector",
            extension_id="crm.lookup_contact",
            business_goal="Look up CRM contacts.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            catalog_registration="reusable",
        )
    )

    assert "Catalog registration: package-only" in package_only
    assert "Do not add this extension to the reusable catalog unless the user promotes it later." in package_only
    assert "Catalog registration: reusable-catalog-candidate" in reusable
    assert "Include stable catalog label, group, source, and QA evidence for later reuse." in reusable


def test_extension_planner_builds_connector_request_from_package_documents() -> None:
    request = build_connector_extension_request(
        _extension_package_documents(),
        "crm.lookup_contact",
        catalog_registration="reusable",
    )
    prompt = render_extension_prd_prompt(request)

    assert request.kind == "connector"
    assert request.extension_id == "crm.lookup_contact"
    assert request.business_goal == "Look up CRM contacts for support workflow users."
    assert request.scopes == ("crm:read",)
    assert request.env_requirements == ("APPV24_CRM_MCP_SERVER",)
    assert request.side_effect_class == "read"
    assert request.approval_class == "none"
    assert "Catalog registration: reusable-catalog-candidate" in prompt
    assert '"email"' in prompt
    assert '"contact"' in prompt


def test_extension_planner_builds_middleware_request_from_package_documents() -> None:
    request = build_middleware_extension_request(
        _extension_package_documents(),
        "team.redactor",
        catalog_registration="package",
    )
    prompt = render_extension_prd_prompt(request)

    assert request.kind == "middleware"
    assert request.extension_id == "team.redactor"
    assert request.business_goal == "Redact sensitive fields from workflow audit events."
    assert request.env_requirements == ("APPV24_TEAM_REDACTOR_LEVEL",)
    assert request.scopes == ("middleware:team.redactor",)
    assert "Catalog registration: package-only" in prompt
    assert '"event"' in prompt


def test_extension_planner_maps_connector_validation_issue_to_build_request() -> None:
    documents = _extension_package_documents()
    validation = validate_workflow_documents(documents)

    requests = build_extension_requests_for_validation_issues(
        documents,
        validation.issues,
        catalog_registration="reusable",
    )

    connector_requests = tuple(request for request in requests if request.extension_id == "crm.lookup_contact")
    assert len(connector_requests) == 1
    assert connector_requests[0].kind == "connector"
    assert connector_requests[0].catalog_registration == "reusable"
    assert "Catalog registration: reusable-catalog-candidate" in render_extension_prd_prompt(connector_requests[0])


def test_extension_planner_maps_middleware_validation_issue_to_build_request() -> None:
    documents = _extension_package_documents()
    validation = validate_workflow_documents(documents)

    requests = build_extension_requests_for_validation_issues(documents, validation.issues)

    middleware_requests = tuple(request for request in requests if request.extension_id == "team.redactor")
    assert len(middleware_requests) == 1
    assert middleware_requests[0].kind == "middleware"
    assert middleware_requests[0].catalog_registration == "package"
    assert "Adapter boundary: middleware runtime interface" in render_extension_prd_prompt(middleware_requests[0])


def test_extension_handoff_writes_coding_profile_manifest_and_prompts(tmp_path: Path) -> None:
    documents = _extension_package_documents(root=tmp_path / "package")
    validation = validate_workflow_documents(documents)
    output_dir = tmp_path / "handoff"

    handoff = write_extension_handoff_for_validation_issues(
        documents,
        validation.issues,
        output_dir=output_dir,
        catalog_registration="reusable",
    )

    assert handoff.status == "ready"
    assert handoff.target_profile == "coding"
    assert handoff.handoff_mode == "coding-profile-command"
    assert handoff.dispatch_status == "pending_coding_profile"
    assert handoff.output_dir == output_dir.resolve()
    assert handoff.manifest_path == output_dir.resolve() / "extension-handoff.manifest.json"
    assert handoff.dispatch_path == output_dir.resolve() / "coding-profile.dispatch.jsonl"
    assert handoff.dispatch_script_path == output_dir.resolve() / "coding-profile.dispatch.sh"
    assert len(handoff.prompt_paths) == 2
    assert len(handoff.prompts) == 2
    assert all(path.exists() for path in handoff.prompt_paths)
    assert output_dir.joinpath("returned-qa").is_dir()
    assert handoff.dispatch_path.exists()
    assert handoff.dispatch_script_path.exists()
    assert "workflow runtime must not execute this script" in handoff.dispatch_script_path.read_text(encoding="utf-8")
    assert "appv24 --cwd " in handoff.dispatch_script_path.read_text(encoding="utf-8")
    assert "# appv24 connector extension PRD: crm.lookup_contact" in handoff.prompts[0]
    assert "Runtime must not call the coding profile" in handoff.prompts[0]
    assert "Catalog registration: reusable-catalog-candidate" in handoff.prompts[0]
    assert f"Return QA manifest path: {output_dir.resolve() / 'returned-qa' / 'crm.lookup_contact.qa.yaml'}" in handoff.prompts[0]
    assert "The user-facing /workflow-approve command reads this returned-QA inbox." in handoff.prompts[0]

    manifest = json.loads(handoff.manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "appv24.workflow-extension-handoff/v1"
    assert manifest["target_profile"] == "coding"
    assert manifest["handoff_mode"] == "coding-profile-command"
    assert manifest["dispatch_status"] == "pending_coding_profile"
    assert manifest["runtime_boundary"] == "build-time handoff only; workflow runtime must not call the coding profile"
    assert manifest["catalog_registration"] == "reusable"
    assert manifest["package_fingerprint"].startswith("sha256:")
    assert manifest["dispatch_outbox_path"] == "coding-profile.dispatch.jsonl"
    assert manifest["dispatch_script_path"] == "coding-profile.dispatch.sh"
    assert manifest["returned_qa_dir"] == "returned-qa"
    assert len(manifest["dispatch_requests"]) == 2
    assert manifest["dispatch_requests"][0]["status"] == "queued_for_coding_profile"
    assert manifest["dispatch_requests"][0]["target_profile"] == "coding"
    assert manifest["dispatch_requests"][0]["extension_id"] == "crm.lookup_contact"
    assert manifest["dispatch_requests"][0]["qa_return_manifest_path"] == "returned-qa/crm.lookup_contact.qa.yaml"
    assert manifest["requests"][0]["extension_id"] == "crm.lookup_contact"
    assert manifest["requests"][0]["qa_manifest_required"] == "appv24.extension_qa/v1"
    assert manifest["requests"][0]["qa_return_target"] == "connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa"
    assert manifest["requests"][0]["qa_return_manifest_path"] == "returned-qa/crm.lookup_contact.qa.yaml"
    assert manifest["requests"][0]["prompt_path"].endswith(".prd.md")
    assert manifest["requests"][0]["dispatch_request_id"] == manifest["dispatch_requests"][0]["request_id"]
    assert manifest["qa_required_fields"] == [
        "version: appv24.extension_qa/v1",
        "status: passed",
        "qa.schema_validation: passed",
        "qa.side_effect_policy: passed",
        "qa.runtime_boundary: passed",
        "qa.adapter_contract: passed",
        "contract.required_scopes",
        "contract.allowed_outputs",
        "fingerprints.spec",
        "fingerprints.implementation",
        "fingerprints.tests",
        "fingerprints.fixtures",
    ]
    assert len(manifest["coding_profile_commands"]) == 2
    assert manifest["coding_profile_commands"][0].startswith("appv24 --cwd ")
    assert "--profile coding --plain" in manifest["coding_profile_commands"][0]
    assert "cat " in manifest["coding_profile_commands"][0]
    assert manifest["requests"][0]["coding_profile_command"] == manifest["coding_profile_commands"][0]
    dispatch_records = [
        json.loads(line)
        for line in handoff.dispatch_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(dispatch_records) == 2
    assert dispatch_records[0]["version"] == "appv24.workflow-extension-dispatch/v1"
    assert dispatch_records[0]["request_id"] == manifest["dispatch_requests"][0]["request_id"]
    assert dispatch_records[0]["status"] == "queued_for_coding_profile"
    assert dispatch_records[0]["target_profile"] == "coding"
    assert dispatch_records[0]["handoff_manifest"] == "extension-handoff.manifest.json"
    assert dispatch_records[0]["extension_id"] == "crm.lookup_contact"
    assert dispatch_records[0]["qa_return_target"] == "connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa"
    assert dispatch_records[0]["qa_return_manifest_path"] == "returned-qa/crm.lookup_contact.qa.yaml"
    assert dispatch_records[0]["coding_profile_command"] == manifest["coding_profile_commands"][0]
    assert dispatch_records[0]["runtime_boundary"] == "build-time handoff only; workflow runtime must not call the coding profile"
    rendered = format_workflow_extension_handoff(handoff)
    assert "handoff mode: coding-profile-command" in rendered
    assert "dispatch status: pending_coding_profile" in rendered
    assert "dispatch outbox:" in rendered
    assert "dispatch script:" in rendered
    assert "queued_for_coding_profile" in rendered
    assert "QA return targets:" in rendered
    assert "connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa" in rendered
    assert "QA return manifest paths:" in rendered
    assert "returned-qa/crm.lookup_contact.qa.yaml" in rendered
    assert "coding profile commands:" in rendered
    assert "appv24 --cwd " in rendered


def test_extension_handoff_apply_result_attaches_connector_qa_to_return_target(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package_documents(_connector_only_extension_package_documents(root=package))
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    qa_path = tmp_path / "crm-lookup.qa.yaml"
    qa_path.write_text(
        yaml.safe_dump(_passed_connector_qa("crm.lookup_contact"), sort_keys=False),
        encoding="utf-8",
    )

    report = apply_extension_handoff_result(handoff.manifest_path, qa_path)

    assert report.status == "applied"
    assert report.extension_id == "crm.lookup_contact"
    assert report.qa_return_target == "connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa"
    assert report.next_commands == (
        f"/workflow-approve {package.resolve()} happy_path.yaml",
        f"/workflow-extension-promote {package.resolve()} {(tmp_path / 'workflow-extension-catalog.yaml').resolve()} crm.lookup_contact",
        f"/workflow-checkin {(tmp_path / 'workflow-extension-catalog.yaml').resolve()}",
    )
    rendered = format_workflow_extension_handoff_apply_report(report)
    assert "Workflow extension handoff apply: applied" in rendered
    assert "extension: crm.lookup_contact" in rendered
    assert "QA return target: connectors.yaml:connectors[id=crm].actions[id=lookup_contact].qa" in rendered
    assert "next commands:" in rendered
    assert f"/workflow-approve {package.resolve()} happy_path.yaml" in rendered
    assert "/workflow-run-guided" not in rendered
    assert "/workflow-extension-promote" in rendered
    assert "/workflow-checkin" in rendered
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    action = connectors["connectors"][0]["actions"][0]
    assert action["qa"]["status"] == "passed"
    assert action["qa"]["contract"]["required_scopes"] == ["crm:read"]
    validation_after = validate_workflow_documents(load_package_documents(package))
    assert not any(
        issue.code.startswith("extension_qa") and "crm.lookup_contact" in issue.message
        for issue in validation_after.issues
    )


def test_extension_handoff_applies_returned_qa_from_standard_inbox(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package_documents(_connector_only_extension_package_documents(root=package))
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    returned_dir = package / ".workflow-extension-handoff" / "returned-qa"
    assert returned_dir.is_dir()
    qa_path = returned_dir / "crm.lookup_contact.qa.yaml"
    qa_path.write_text(
        yaml.safe_dump(_passed_connector_qa("crm.lookup_contact"), sort_keys=False),
        encoding="utf-8",
    )

    report = apply_returned_extension_handoff_results(package)

    assert report.status == "applied"
    assert report.package_path == package.resolve()
    assert report.handoff_manifest_path == handoff.manifest_path
    assert report.returned_qa_dir == returned_dir.resolve()
    assert len(report.applied_reports) == 1
    assert report.applied_reports[0].extension_id == "crm.lookup_contact"
    rendered = format_workflow_extension_returned_qa_apply_report(report)
    assert "Workflow extension returned QA apply: applied" in rendered
    assert "crm.lookup_contact.qa.yaml" in rendered
    assert "extension catalog choice:" in rendered
    assert "keep package-only: no command needed" in rendered
    assert (
        f"/workflow-extension-promote {package.resolve()} "
        f"{(tmp_path / 'workflow-extension-catalog.yaml').resolve()} crm.lookup_contact"
    ) in rendered
    assert f"/workflow-checkin {(tmp_path / 'workflow-extension-catalog.yaml').resolve()}" in rendered
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    assert connectors["connectors"][0]["actions"][0]["qa"]["status"] == "passed"
    validation_after = validate_workflow_documents(load_package_documents(package))
    assert not any(issue.code.startswith("extension_qa") for issue in validation_after.issues)


def test_extension_handoff_applies_returned_middleware_qa_from_standard_inbox(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package_documents(_extension_package_documents(root=package))
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    returned_dir = package / ".workflow-extension-handoff" / "returned-qa"
    assert returned_dir.is_dir()
    qa_path = returned_dir / "team.redactor.qa.yaml"
    qa_path.write_text(
        yaml.safe_dump(_passed_middleware_qa("team.redactor"), sort_keys=False),
        encoding="utf-8",
    )

    report = apply_returned_extension_handoff_results(package)

    assert report.status == "applied"
    assert report.package_path == package.resolve()
    assert report.handoff_manifest_path == handoff.manifest_path
    assert len(report.applied_reports) == 1
    assert report.applied_reports[0].extension_id == "team.redactor"
    assert report.applied_reports[0].qa_return_target == "middlewares.yaml:middlewares[id=team.redactor].qa"
    rendered = format_workflow_extension_returned_qa_apply_report(report)
    assert "Workflow extension returned QA apply: applied" in rendered
    assert "team.redactor.qa.yaml" in rendered
    assert "extension catalog choice:" in rendered
    assert "keep package-only: no command needed" in rendered
    assert (
        f"/workflow-extension-promote {package.resolve()} "
        f"{(tmp_path / 'workflow-extension-catalog.yaml').resolve()} team.redactor"
    ) in rendered
    assert f"/workflow-checkin {(tmp_path / 'workflow-extension-catalog.yaml').resolve()}" in rendered
    middlewares = yaml.safe_load((package / "middlewares.yaml").read_text(encoding="utf-8"))
    assert middlewares["middlewares"][0]["qa"]["status"] == "passed"
    validation_after = validate_workflow_documents(load_package_documents(package))
    assert not any(
        issue.code.startswith("extension_qa") and "team.redactor" in issue.message
        for issue in validation_after.issues
    )


def test_extension_handoff_dispatch_report_can_mark_outbox_dispatched(tmp_path: Path) -> None:
    documents = _extension_package_documents(root=tmp_path / "package")
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)

    queued = dispatch_extension_handoff(handoff.manifest_path)

    assert queued.status == "queued"
    assert queued.marked_dispatched is False
    assert queued.dispatch_path == handoff.dispatch_path
    assert queued.dispatch_script_path == handoff.dispatch_script_path
    assert len(queued.records) == 2
    assert queued.records[0].status == "queued_for_coding_profile"
    assert queued.records[0].target_profile == "coding"
    assert queued.records[0].extension_id == "crm.lookup_contact"
    assert queued.records[0].qa_return_manifest_path == "returned-qa/crm.lookup_contact.qa.yaml"
    rendered_queued = format_workflow_extension_handoff_dispatch_report(queued)
    assert "Workflow extension dispatch: queued" in rendered_queued
    assert "crm.lookup_contact" in rendered_queued
    assert "dispatch script:" in rendered_queued
    assert "QA return manifest: returned-qa/crm.lookup_contact.qa.yaml" in rendered_queued
    assert "appv24 --cwd " in rendered_queued

    marked = dispatch_extension_handoff(handoff.manifest_path, mark_dispatched=True)

    assert marked.status == "dispatched"
    assert marked.marked_dispatched is True
    assert marked.dispatch_script_path == handoff.dispatch_script_path
    assert all(record.status == "dispatched_to_coding_profile" for record in marked.records)
    rendered_marked = format_workflow_extension_handoff_dispatch_report(marked)
    assert "Workflow extension dispatch: dispatched" in rendered_marked
    assert "dispatched_to_coding_profile" in rendered_marked
    outbox_records = [
        json.loads(line)
        for line in handoff.dispatch_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {record["status"] for record in outbox_records} == {"dispatched_to_coding_profile"}
    manifest = json.loads(handoff.manifest_path.read_text(encoding="utf-8"))
    assert manifest["dispatch_status"] == "dispatched_to_coding_profile"
    assert {request["status"] for request in manifest["dispatch_requests"]} == {"dispatched_to_coding_profile"}


def test_extension_handoff_apply_result_blocks_stale_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package_documents(_extension_package_documents(root=package))
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    workflow = yaml.safe_load((package / "workflow.yaml").read_text(encoding="utf-8"))
    workflow["name"] = "changed_after_handoff"
    (package / "workflow.yaml").write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    qa_path = tmp_path / "crm-lookup.qa.yaml"
    qa_path.write_text(
        yaml.safe_dump(_passed_connector_qa("crm.lookup_contact"), sort_keys=False),
        encoding="utf-8",
    )

    report = apply_extension_handoff_result(handoff.manifest_path, qa_path)

    assert report.status == "blocked"
    assert any("package fingerprint changed" in issue for issue in report.issues)


def test_extension_handoff_apply_result_rejects_contract_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "package"
    _write_package_documents(_extension_package_documents(root=package))
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    qa = _passed_connector_qa("crm.lookup_contact")
    qa.pop("contract")
    qa_path = tmp_path / "crm-lookup.qa.yaml"
    qa_path.write_text(yaml.safe_dump(qa, sort_keys=False), encoding="utf-8")

    report = apply_extension_handoff_result(handoff.manifest_path, qa_path)

    assert report.status == "blocked"
    assert any("contract.required_scopes" in issue for issue in report.issues)
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    assert "qa" not in connectors["connectors"][0]["actions"][0]


def test_workflows_package_exports_extension_planner_and_profile_builder() -> None:
    from appv24.workflows import (
        WorkflowExtensionBuildRequest as exported_request,
        apply_extension_handoff_result as exported_handoff_result_applier,
        apply_returned_extension_handoff_results as exported_returned_qa_applier,
        dispatch_extension_handoff as exported_handoff_dispatcher,
        format_workflow_extension_handoff_apply_report as exported_handoff_apply_formatter,
        format_workflow_extension_handoff_dispatch_report as exported_handoff_dispatch_formatter,
        format_workflow_extension_returned_qa_apply_report as exported_returned_qa_formatter,
        write_extension_handoff_for_validation_issues as exported_handoff_writer,
        build_connector_extension_request as exported_connector_request_builder,
        build_extension_requests_for_validation_issues as exported_issue_request_builder,
        build_middleware_extension_request as exported_middleware_request_builder,
        build_workflow_extension_profile,
        render_extension_prd_prompt as exported_renderer,
    )

    assert exported_request is WorkflowExtensionBuildRequest
    assert exported_handoff_result_applier is apply_extension_handoff_result
    assert exported_returned_qa_applier is apply_returned_extension_handoff_results
    assert exported_handoff_dispatcher is dispatch_extension_handoff
    assert exported_handoff_apply_formatter is format_workflow_extension_handoff_apply_report
    assert exported_handoff_dispatch_formatter is format_workflow_extension_handoff_dispatch_report
    assert exported_returned_qa_formatter is format_workflow_extension_returned_qa_apply_report
    assert exported_renderer is render_extension_prd_prompt
    assert exported_handoff_writer is write_extension_handoff_for_validation_issues
    assert exported_connector_request_builder is build_connector_extension_request
    assert exported_issue_request_builder is build_extension_requests_for_validation_issues
    assert exported_middleware_request_builder is build_middleware_extension_request
    assert callable(build_workflow_extension_profile)


def _connector_only_extension_package_documents(*, root: Path = Path("<test>")) -> PackageDocuments:
    base = _extension_package_documents(root=root)
    return PackageDocuments(
        root=base.root,
        workflow=base.workflow,
        connectors=base.connectors,
        permissions=base.permissions,
        dry_run_fixtures={"happy_path.yaml": {"input": {"email": "customer@example.com"}}},
        rollback_text="No external mutation occurs.\n",
        middlewares={},
    )


def _extension_package_documents(*, root: Path = Path("<test>")) -> PackageDocuments:
    return PackageDocuments(
        root=root,
        workflow={
            "version": "appv24.workflow/v1",
            "name": "extension_test",
            "triggers": [{"id": "manual", "type": "manual"}],
            "entrypoint": "done",
            "steps": [{"id": "done", "type": "terminal"}],
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
                        {"name": "APPV24_CRM_MCP_SERVER", "purpose": "CRM MCP server", "required": True}
                    ],
                    "actions": [
                        {
                            "id": "lookup_contact",
                            "custom": True,
                            "source": "mcp://crm.lookup_contact@v1",
                            "side_effect": False,
                            "side_effect_class": "read",
                            "execution_mode": "brokered",
                            "dry_run_supported": True,
                            "required_scopes": ["crm:read"],
                            "outputs": {"allowed": ["contact", "artifact_path"]},
                            "workflow_hints": {
                                "business_goal": "Look up CRM contacts for support workflow users.",
                                "generated_by": "coding_profile",
                                "input_fields": ["email"],
                                "qa_required": True,
                                "required_input_fields": ["email"],
                            },
                        }
                    ],
                }
            ],
        },
        permissions={"version": "appv24.permissions/v1", "permissions": []},
        dry_run_fixtures={},
        rollback_text=None,
        middlewares={
            "version": "appv24.middlewares/v1",
            "middlewares": [
                {
                    "id": "team.redactor",
                    "adapter": "local",
                    "source": "local://middlewares/team/redactor@sha256:abc123",
                    "env": [
                        {
                            "name": "APPV24_TEAM_REDACTOR_LEVEL",
                            "purpose": "redaction level",
                            "required": True,
                        }
                    ],
                    "audit": ["middleware:team.redactor"],
                    "workflow_hints": {
                        "business_goal": "Redact sensitive fields from workflow audit events.",
                        "generated_by": "coding_profile",
                        "input_schema": {"type": "object", "properties": {"event": {"type": "object"}}},
                        "output_schema": {"type": "object", "properties": {"event": {"type": "object"}}},
                        "qa_required": True,
                    },
                }
            ],
        },
    )


def _passed_connector_qa(extension_id: str) -> dict[str, object]:
    return {
        "version": "appv24.extension_qa/v1",
        "kind": "connector",
        "extension_id": extension_id,
        "source": f"mcp://{extension_id}@v1",
        "status": "passed",
        "qa": {
            "schema_validation": "passed",
            "tests": [{"command": f"pytest appV2.4/tests/test_{extension_id.replace('.', '_')}.py", "status": "passed"}],
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


def _passed_middleware_qa(extension_id: str) -> dict[str, object]:
    return {
        "version": "appv24.extension_qa/v1",
        "kind": "middleware",
        "extension_id": extension_id,
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
    }


def _write_package_documents(documents: PackageDocuments) -> None:
    root = documents.root
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow.yaml").write_text(yaml.safe_dump(dict(documents.workflow), sort_keys=False), encoding="utf-8")
    (root / "connectors.yaml").write_text(yaml.safe_dump(dict(documents.connectors), sort_keys=False), encoding="utf-8")
    (root / "permissions.yaml").write_text(yaml.safe_dump(dict(documents.permissions), sort_keys=False), encoding="utf-8")
    if documents.middlewares:
        (root / "middlewares.yaml").write_text(
            yaml.safe_dump(dict(documents.middlewares), sort_keys=False),
            encoding="utf-8",
        )
    if documents.dry_run_fixtures:
        fixture_root = root / "dry_run_fixtures"
        fixture_root.mkdir()
        for name, fixture in documents.dry_run_fixtures.items():
            (fixture_root / name).write_text(yaml.safe_dump(dict(fixture), sort_keys=False), encoding="utf-8")
