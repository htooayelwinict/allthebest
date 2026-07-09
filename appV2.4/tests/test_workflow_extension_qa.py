from __future__ import annotations

from appv24.workflows.extension_qa import validate_extension_qa_manifest


def test_extension_qa_manifest_accepts_passed_connector_evidence() -> None:
    report = validate_extension_qa_manifest(
        {
            "version": "appv24.extension_qa/v1",
            "kind": "connector",
            "extension_id": "crm.lookup_contact",
            "source": "local://connectors/crm.lookup_contact@sha256:abc123",
            "status": "passed",
            "qa": {
                "schema_validation": "passed",
                "tests": [
                    {
                        "command": "PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_crm_connector.py",
                        "status": "passed",
                    }
                ],
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
        },
        expected_kind="connector",
        expected_extension_id="crm.lookup_contact",
        expected_required_scopes=("crm:read",),
        expected_allowed_outputs=("contact", "artifact_path"),
    )

    assert report.ok
    assert report.issues == ()


def test_extension_qa_manifest_requires_expected_connector_contract_evidence() -> None:
    report = validate_extension_qa_manifest(
        {
            "version": "appv24.extension_qa/v1",
            "kind": "connector",
            "extension_id": "crm.lookup_contact",
            "source": "local://connectors/crm.lookup_contact@sha256:abc123",
            "status": "passed",
            "qa": {
                "schema_validation": "passed",
                "tests": [{"command": "pytest appV2.4/tests/test_crm_connector.py", "status": "passed"}],
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
        expected_kind="connector",
        expected_extension_id="crm.lookup_contact",
        expected_required_scopes=("crm:read",),
        expected_allowed_outputs=("contact", "artifact_path"),
    )

    assert not report.ok
    codes = {issue.code for issue in report.issues}
    assert "extension_qa_required_scopes" in codes
    assert "extension_qa_allowed_outputs" in codes


def test_extension_qa_manifest_requires_adapter_contract_evidence() -> None:
    report = validate_extension_qa_manifest(
        {
            "version": "appv24.extension_qa/v1",
            "kind": "connector",
            "extension_id": "crm.lookup_contact",
            "source": "local://connectors/crm.lookup_contact@sha256:abc123",
            "status": "passed",
            "qa": {
                "schema_validation": "passed",
                "tests": [{"command": "pytest appV2.4/tests/test_crm_connector.py", "status": "passed"}],
                "side_effect_policy": "passed",
                "runtime_boundary": "passed",
            },
            "fingerprints": {
                "spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                "implementation": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                "tests": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
                "fixtures": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
            },
        }
    )

    assert not report.ok
    assert any(issue.code == "extension_qa_adapter_contract" for issue in report.issues)


def test_extension_qa_manifest_rejects_missing_or_failed_evidence() -> None:
    report = validate_extension_qa_manifest(
        {
            "version": "appv24.extension_qa/v1",
            "kind": "connector",
            "extension_id": "crm.lookup_contact",
            "source": "local://connectors/crm.lookup_contact@sha256:abc123",
            "status": "failed",
            "qa": {
                "schema_validation": "passed",
                "tests": [{"command": "pytest appV2.4/tests/test_crm_connector.py", "status": "failed"}],
                "side_effect_policy": "missing",
            },
            "fingerprints": {"spec": "sha256:1111111111111111111111111111111111111111111111111111111111111111"},
        }
    )

    assert not report.ok
    codes = {issue.code for issue in report.issues}
    assert "extension_qa_status" in codes
    assert "extension_qa_test_failed" in codes
    assert "extension_qa_side_effect_policy" in codes
    assert "extension_qa_fingerprint_missing" in codes


def test_extension_qa_manifest_rejects_placeholder_fingerprints() -> None:
    report = validate_extension_qa_manifest(
        {
            "version": "appv24.extension_qa/v1",
            "kind": "connector",
            "extension_id": "crm.lookup_contact",
            "source": "local://connectors/crm.lookup_contact@sha256:abc123",
            "status": "passed",
            "qa": {
                "schema_validation": "passed",
                "tests": [{"command": "pytest appV2.4/tests/test_crm_connector.py", "status": "passed"}],
                "side_effect_policy": "passed",
                "runtime_boundary": "passed",
                "adapter_contract": "passed",
            },
            "fingerprints": {
                "spec": "sha256:spec",
                "implementation": "sha256:impl",
                "tests": "sha256:tests",
                "fixtures": "sha256:fixtures",
            },
        }
    )

    assert not report.ok
    assert {issue.code for issue in report.issues} == {"extension_qa_fingerprint_invalid"}


def test_workflows_package_exports_extension_qa_validator() -> None:
    from appv24.workflows import validate_extension_qa_manifest as exported

    assert exported is validate_extension_qa_manifest
