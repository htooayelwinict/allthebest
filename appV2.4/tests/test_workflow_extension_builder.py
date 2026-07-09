from __future__ import annotations

from pathlib import Path
import sys

import yaml

from appv24.workflows.extension_builder import (
    WorkflowExtensionBuildCommandResult,
    WorkflowExtensionBuildEvent,
    WorkflowExtensionBuildInvocation,
    build_workflow_extension_handoff,
    format_workflow_extension_build_report,
    run_workflow_extension_build_invocation,
)
from appv24.workflows.extension_handoff import write_extension_handoff_for_validation_issues
from appv24.workflows.loader import load_package_documents
from appv24.workflows.validator import validate_workflow_documents


def test_extension_builder_runs_queued_request_applies_returned_qa_and_revalidates(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    invocations: list[WorkflowExtensionBuildInvocation] = []

    def fake_runner(invocation: WorkflowExtensionBuildInvocation) -> WorkflowExtensionBuildCommandResult:
        invocations.append(invocation)
        assert invocation.argv[:3] == (sys.executable, "-m", "appv24.cli")
        assert invocation.argv[3:7] == ("--cwd", str(package.resolve()), "--profile", "coding")
        assert invocation.prompt_path.is_file()
        assert invocation.qa_return_manifest_path.name == "crm.lookup_contact.qa.yaml"
        invocation.qa_return_manifest_path.write_text(
            yaml.safe_dump(_passed_crm_extension_qa(), sort_keys=False),
            encoding="utf-8",
        )
        return WorkflowExtensionBuildCommandResult(returncode=0, stdout="built ok", stderr="")

    report = build_workflow_extension_handoff(package, runner=fake_runner)

    assert report.status == "applied"
    assert report.validation_ok is True
    assert report.built_extensions == ("crm.lookup_contact",)
    assert report.applied_extensions == ("crm.lookup_contact",)
    assert report.issues == ()
    assert len(invocations) == 1
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    assert connectors["connectors"][0]["actions"][0]["qa"]["status"] == "passed"
    rendered = format_workflow_extension_build_report(report)
    assert "Workflow extension build: applied" in rendered
    assert "validation: passed" in rendered
    assert "/workflow-approve" in rendered


def test_extension_builder_invocation_uses_absolute_pythonpath_for_package_cwd(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    observed: dict[str, WorkflowExtensionBuildInvocation] = {}

    def fake_runner(invocation: WorkflowExtensionBuildInvocation) -> WorkflowExtensionBuildCommandResult:
        observed["invocation"] = invocation
        invocation.qa_return_manifest_path.write_text(
            yaml.safe_dump(_passed_crm_extension_qa(), sort_keys=False),
            encoding="utf-8",
        )
        return WorkflowExtensionBuildCommandResult(returncode=0)

    build_workflow_extension_handoff(package, runner=fake_runner)

    pythonpath = observed["invocation"].env["PYTHONPATH"]
    first_entry = pythonpath.split(":")[0]
    assert first_entry.endswith("appV2.4")
    assert Path(first_entry).is_absolute()


def test_extension_builder_default_generates_package_local_connector_artifacts_and_qa(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)

    report = build_workflow_extension_handoff(package)

    assert report.status == "applied"
    assert report.validation_ok is True
    assert report.built_extensions == ("crm.lookup_contact",)
    assert report.applied_extensions == ("crm.lookup_contact",)
    source_path = package / "connectors" / "crm" / "lookup_contact.py"
    test_path = package / "connectors" / "crm" / "test_lookup_contact.py"
    qa_path = package / ".workflow-extension-handoff" / "returned-qa" / "crm.lookup_contact.qa.yaml"
    assert "def run(" in source_path.read_text(encoding="utf-8")
    assert "test_required_inputs_are_enforced" in test_path.read_text(encoding="utf-8")
    assert qa_path.is_file()
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    qa = connectors["connectors"][0]["actions"][0]["qa"]
    assert qa["status"] == "passed"
    assert qa["source"].startswith("local://connectors/crm/lookup_contact.py@sha256:")
    assert qa["fingerprints"]["implementation"].startswith("sha256:")
    assert qa["fingerprints"]["tests"].startswith("sha256:")


def test_extension_builder_default_replaces_stale_package_local_connector_artifacts(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    stale_path = package / "connectors" / "crm" / "lookup_contact.py"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text("def broken(:\n", encoding="utf-8")

    report = build_workflow_extension_handoff(package)

    assert report.status == "applied"
    assert report.validation_ok is True
    assert "def run(" in stale_path.read_text(encoding="utf-8")


def test_extension_builder_blocks_when_runner_does_not_return_qa(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(returncode=0, stdout="no qa", stderr=""),
    )

    assert report.status == "blocked"
    assert report.validation_ok is False
    assert report.built_extensions == ()
    assert any("returned QA manifest was not created" in issue for issue in report.issues)


def test_extension_builder_blocks_on_nonzero_runner_result(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(returncode=2, stdout="", stderr="failed"),
    )

    assert report.status == "blocked"
    assert report.validation_ok is False
    assert report.built_extensions == ()
    assert any("coding profile build failed for crm.lookup_contact" in issue for issue in report.issues)


def test_extension_builder_marks_timeout_result_and_keeps_package_blocked(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    events: list[WorkflowExtensionBuildEvent] = []

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(
            returncode=-9,
            stderr="timed out",
            timed_out=True,
            timeout_seconds=12.0,
        ),
        timeout_seconds=12.0,
        progress=events.append,
    )

    assert report.status == "blocked"
    assert report.validation_ok is False
    assert report.built_extensions == ()
    assert len(report.build_results) == 1
    assert report.build_results[0].status == "timed-out"
    assert report.build_results[0].timed_out is True
    assert report.build_results[0].timeout_seconds == 12.0
    assert any("coding profile build timed out for crm.lookup_contact after 12s" in issue for issue in report.issues)
    assert [event.phase for event in events] == ["extension-started", "extension-timed-out", "finished"]
    rendered = format_workflow_extension_build_report(report)
    assert "- crm.lookup_contact: timed-out" in rendered
    assert "timeout: 12s" in rendered


def test_extension_builder_subprocess_runner_returns_timeout_result(tmp_path: Path) -> None:
    invocation = WorkflowExtensionBuildInvocation(
        extension_id="crm.lookup_contact",
        kind="connector",
        package_path=tmp_path,
        manifest_path=tmp_path / "manifest.json",
        prompt_path=tmp_path / "prompt.md",
        qa_return_manifest_path=tmp_path / "returned.qa.yaml",
        log_path=tmp_path / "build.log",
        argv=(sys.executable, "-c", "import time; time.sleep(2)"),
        env={},
        request_id="request-1",
    )

    result = run_workflow_extension_build_invocation(invocation, timeout_seconds=0.01)

    assert result.timed_out is True
    assert result.timeout_seconds == 0.01
    assert result.returncode != 0
    assert "timed out after 0.01s" in result.stderr


def test_extension_builder_applies_stdout_artifact_bundle_with_noisy_output(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    artifact_path = package / "connectors" / "crm" / "lookup_contact.py"

    def fake_runner(invocation: WorkflowExtensionBuildInvocation) -> WorkflowExtensionBuildCommandResult:
        return WorkflowExtensionBuildCommandResult(
            returncode=0,
            stdout="\n".join(
                (
                    "model log before bundle",
                    _artifact_bundle_stdout(
                        extension_id=invocation.extension_id,
                        files=[
                            {
                                "path": "connectors/crm/lookup_contact.py",
                                "content": "def lookup_contact(email: str) -> dict[str, object]:\n    return {'contact': {'email': email}}\n",
                            }
                        ],
                        qa_manifest=_passed_crm_extension_qa(),
                    ),
                    "model log after bundle",
                )
            ),
        )

    report = build_workflow_extension_handoff(package, runner=fake_runner)

    assert report.status == "applied"
    assert report.validation_ok is True
    assert report.built_extensions == ("crm.lookup_contact",)
    assert report.applied_extensions == ("crm.lookup_contact",)
    assert artifact_path.read_text(encoding="utf-8").startswith("def lookup_contact")
    connectors = yaml.safe_load((package / "connectors.yaml").read_text(encoding="utf-8"))
    assert connectors["connectors"][0]["actions"][0]["qa"]["status"] == "passed"


def test_extension_builder_rejects_stdout_artifact_bundle_parent_path(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(
            returncode=0,
            stdout=_artifact_bundle_stdout(
                extension_id=invocation.extension_id,
                files=[{"path": "../escape.py", "content": "print('escape')\n"}],
                qa_manifest=_passed_crm_extension_qa(),
            ),
        ),
    )

    assert report.status == "blocked"
    assert not (package.parent / "escape.py").exists()
    assert any("artifact bundle file path is outside package" in issue for issue in report.issues)


def test_extension_builder_rejects_stdout_artifact_bundle_outside_extension_root(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(
            returncode=0,
            stdout=_artifact_bundle_stdout(
                extension_id=invocation.extension_id,
                files=[{"path": "workflow.yaml", "content": "name: replaced\n"}],
                qa_manifest=_passed_crm_extension_qa(),
            ),
        ),
    )

    assert report.status == "blocked"
    assert any("artifact bundle file path is outside allowed extension root" in issue for issue in report.issues)
    workflow = yaml.safe_load((package / "workflow.yaml").read_text(encoding="utf-8"))
    assert workflow["name"] == "generated_crm_extension"


def test_extension_builder_rejects_invalid_python_artifact_bundle_without_writes(tmp_path: Path) -> None:
    package = _package_with_handoff(tmp_path)
    artifact_path = package / "connectors" / "crm" / "lookup_contact.py"

    report = build_workflow_extension_handoff(
        package,
        runner=lambda invocation: WorkflowExtensionBuildCommandResult(
            returncode=0,
            stdout=_artifact_bundle_stdout(
                extension_id=invocation.extension_id,
                files=[{"path": "connectors/crm/lookup_contact.py", "content": "def broken(:\n"}],
                qa_manifest=_passed_crm_extension_qa(),
            ),
        ),
    )

    assert report.status == "blocked"
    assert not artifact_path.exists()
    assert any("artifact bundle python file is invalid" in issue for issue in report.issues)


def _package_with_handoff(root: Path) -> Path:
    package = _write_generated_crm_extension_package_without_qa(root)
    documents = load_package_documents(package)
    validation = validate_workflow_documents(documents)
    handoff = write_extension_handoff_for_validation_issues(documents, validation.issues)
    assert handoff.status == "ready"
    assert handoff.manifest_path is not None
    return package


def _write_generated_crm_extension_package(root: Path) -> Path:
    package = root / "generated-crm-extension"
    (package / "dry_run_fixtures").mkdir(parents=True, exist_ok=True)
    (package / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "appv24.workflow/v1",
                "name": "generated_crm_extension",
                "description": "Look up CRM contacts through a generated connector.",
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
                    {
                        "id": "lookup_contact",
                        "uses": "crm.lookup_contact",
                        "with": {"email": "{{ inputs.email }}"},
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
                        "custom": True,
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
                                "qa": _passed_crm_extension_qa(),
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
    (package / "dry_run_fixtures" / "happy_path.yaml").write_text(
        yaml.safe_dump({"input": {"email": "customer@example.com"}}, sort_keys=False),
        encoding="utf-8",
    )
    (package / "rollback.md").write_text("No external mutation occurs.\n", encoding="utf-8")
    return package


def _write_generated_crm_extension_package_without_qa(root: Path) -> Path:
    package = _write_generated_crm_extension_package(root)
    connectors_path = package / "connectors.yaml"
    connectors = yaml.safe_load(connectors_path.read_text(encoding="utf-8"))
    connectors["connectors"][0]["actions"][0].pop("qa", None)
    connectors_path.write_text(yaml.safe_dump(connectors, sort_keys=False), encoding="utf-8")
    return package


def _passed_crm_extension_qa() -> dict[str, object]:
    return {
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


def _artifact_bundle_stdout(
    *,
    extension_id: str,
    files: list[dict[str, str]],
    qa_manifest: dict[str, object],
) -> str:
    import json

    bundle = {
        "version": "appv24.workflow-extension-artifact-bundle/v1",
        "kind": "connector",
        "extension_id": extension_id,
        "files": files,
        "qa_manifest": qa_manifest,
    }
    return "\n".join(
        (
            "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_BEGIN",
            json.dumps(bundle, sort_keys=True),
            "APPV24_WORKFLOW_EXTENSION_ARTIFACT_BUNDLE_END",
        )
    )
