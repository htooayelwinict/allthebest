from __future__ import annotations

from appv24.workflows.credential_refs import (
    CredentialRefResolver,
    CredentialScope,
    credential_resolver_from_env,
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
    assert report.issues == (
        "missing credential ref for telegram; required env: "
        "APPV24_TELEGRAM_CREDENTIAL_REF, APPV24_TELEGRAM_SCOPES=telegram:send",
    )


def test_credential_preflight_blocks_missing_required_scope_with_env_hint() -> None:
    example = golden_workflow_examples()["telegram_support_ticket"]
    resolver = CredentialRefResolver(
        {
            "ticket": CredentialScope(available=True, scopes=("ticket:draft",)),
            "telegram": CredentialScope(available=True, scopes=("telegram:read",)),
        }
    )

    report = preflight_credential_refs(example.documents, resolver)

    assert report.ok is False
    assert report.issues == (
        "credential for telegram does not grant telegram:send; required env: APPV24_TELEGRAM_SCOPES=telegram:send",
    )


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
    assert report.issues == ("wildcard credential scope is not allowed for ticket; configure APPV24_TICKET_SCOPES without *",)


def test_credential_resolver_from_env_uses_refs_and_scopes_without_secret_values() -> None:
    resolver = credential_resolver_from_env(
        ("gmail", "ticket-system"),
        {
            "APPV24_GMAIL_CREDENTIAL_REF": "gmail-prod",
            "APPV24_GMAIL_SCOPES": "gmail.compose,gmail.readonly",
            "APPV24_TICKET_SYSTEM_CREDENTIAL_REF": "ticket-prod",
            "APPV24_TICKET_SYSTEM_SCOPES": "ticket:draft ticket:read",
        },
    )

    assert resolver.resolve("gmail") == CredentialScope(available=True, scopes=("gmail.compose", "gmail.readonly"))
    assert resolver.resolve("ticket-system") == CredentialScope(available=True, scopes=("ticket:draft", "ticket:read"))
