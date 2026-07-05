from __future__ import annotations

from copy import deepcopy

from appv24.workflows.examples import golden_workflow_examples
from appv24.workflows.fingerprints import fingerprint_package_documents
from appv24.workflows.loader import PackageDocuments


def test_package_fingerprint_is_stable_for_same_documents() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fingerprint_package_documents(example.documents) == fingerprint_package_documents(example.documents)


def test_package_fingerprint_changes_when_workflow_changes() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    workflow = deepcopy(example.documents.workflow)
    workflow["description"] = "changed"
    changed = PackageDocuments(
        root=example.documents.root,
        workflow=workflow,
        connectors=example.documents.connectors,
        permissions=example.documents.permissions,
        dry_run_fixtures=example.documents.dry_run_fixtures,
        rollback_text=example.documents.rollback_text,
    )

    assert fingerprint_package_documents(example.documents) != fingerprint_package_documents(changed)


def test_package_fingerprint_uses_sha256_prefix() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]

    assert fingerprint_package_documents(example.documents).startswith("sha256:")
