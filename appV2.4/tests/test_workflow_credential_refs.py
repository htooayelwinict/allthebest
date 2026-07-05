from __future__ import annotations

from appv24.workflows.credential_refs import (
    CredentialRefResolver,
    CredentialScope,
    preflight_credential_refs,
)
from appv24.workflows.examples import golden_workflow_examples


def test_credential_preflight_accepts_available_scopes() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver(
        {
            "ticket": CredentialScope(available=True, scopes=("ticket:draft",)),
            "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
        }
    )

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is True
    assert report.issues == ()


def test_credential_preflight_blocks_missing_connector_ref() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver({"ticket": CredentialScope(available=True, scopes=("ticket:draft",))})

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is False
    assert "missing credential ref for telegram" in report.issues


def test_credential_preflight_blocks_wildcard_scope() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver(
        {
            "ticket": CredentialScope(available=True, scopes=("*",)),
            "telegram": CredentialScope(available=True, scopes=("telegram:send",)),
        }
    )

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is False
    assert "wildcard credential scope is not allowed for ticket" in report.issues
