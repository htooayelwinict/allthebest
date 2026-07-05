"""Credential reference preflight for workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from appv24.workflows.loader import PackageDocuments


@dataclass(frozen=True)
class CredentialScope:
    available: bool
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class CredentialPreflightReport:
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


class CredentialRefResolver:
    def __init__(self, credentials: Mapping[str, CredentialScope]):
        self._credentials = dict(credentials)

    def resolve(self, connector_id: str) -> CredentialScope | None:
        return self._credentials.get(connector_id)


def preflight_credential_refs(
    documents: PackageDocuments,
    resolver: CredentialRefResolver,
) -> CredentialPreflightReport:
    issues: list[str] = []
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        credential = resolver.resolve(connector_id)
        if credential is None or not credential.available:
            issues.append(f"missing credential ref for {connector_id}")
            continue
        if "*" in credential.scopes:
            issues.append(f"wildcard credential scope is not allowed for {connector_id}")
        requested = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        for scope in requested:
            if scope not in credential.scopes:
                issues.append(f"credential for {connector_id} does not grant {scope}")
    return CredentialPreflightReport(issues=tuple(issues))
