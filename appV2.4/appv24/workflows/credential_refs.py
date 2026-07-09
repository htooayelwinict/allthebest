"""Credential reference preflight for workflow packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.loader import PackageDocuments, load_package_documents


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


def credential_resolver_from_env(
    connector_ids: tuple[str, ...],
    env: Mapping[str, str],
) -> CredentialRefResolver:
    credentials: dict[str, CredentialScope] = {}
    for connector_id in connector_ids:
        env_name = _credential_env_name(connector_id)
        ref = str(env.get(f"APPV24_{env_name}_CREDENTIAL_REF", "")).strip()
        scopes = _split_scope_env(str(env.get(f"APPV24_{env_name}_SCOPES", "")))
        credentials[connector_id] = CredentialScope(available=bool(ref), scopes=scopes)
    return CredentialRefResolver(credentials)


def credential_resolver_from_connector_config(
    connector_ids: tuple[str, ...],
    connector_config: ConnectorConfigResolver,
) -> CredentialRefResolver:
    credentials: dict[str, CredentialScope] = {}
    for connector_id in connector_ids:
        resolved = connector_config.resolve(connector_id)
        if resolved is None:
            credentials[connector_id] = CredentialScope(available=False, scopes=())
            continue
        credentials[connector_id] = CredentialScope(
            available=resolved.available,
            scopes=tuple(resolved.scopes),
        )
    return CredentialRefResolver(credentials)


def credential_resolver_for_package_connector_config(
    package_path: str | Path,
    connector_config: ConnectorConfigResolver,
) -> CredentialRefResolver:
    documents = load_package_documents(Path(package_path))
    connector_ids = tuple(
        str(permission["connector"])
        for permission in documents.permissions.get("permissions", [])
        if isinstance(permission, dict) and isinstance(permission.get("connector"), str)
    )
    return credential_resolver_from_connector_config(tuple(dict.fromkeys(connector_ids)), connector_config)


def credential_env_name(connector_id: str) -> str:
    return _credential_env_name(connector_id)


def preflight_credential_refs(
    documents: PackageDocuments,
    resolver: CredentialRefResolver,
) -> CredentialPreflightReport:
    issues: list[str] = []
    for permission in documents.permissions.get("permissions", []):
        if not isinstance(permission, dict) or not isinstance(permission.get("connector"), str):
            continue
        connector_id = str(permission["connector"])
        requested = tuple(str(scope) for scope in permission.get("scopes", []) if isinstance(scope, str))
        if not requested:
            continue
        credential = resolver.resolve(connector_id)
        if credential is None or not credential.available:
            issues.append(_missing_credential_issue(connector_id, requested))
            continue
        if "*" in credential.scopes:
            issues.append(_wildcard_scope_issue(connector_id))
            continue
        for scope in requested:
            if scope not in credential.scopes:
                issues.append(_missing_scope_issue(connector_id, scope))
    return CredentialPreflightReport(issues=tuple(issues))


def _credential_env_name(connector_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", connector_id).strip("_").upper()


def _missing_credential_issue(connector_id: str, requested_scopes: tuple[str, ...]) -> str:
    return f"missing credential ref for {connector_id}; required env: {_credential_required_env_hint(connector_id, requested_scopes)}"


def _missing_scope_issue(connector_id: str, scope: str) -> str:
    env_name = _credential_env_name(connector_id)
    return f"credential for {connector_id} does not grant {scope}; required env: APPV24_{env_name}_SCOPES={scope}"


def _wildcard_scope_issue(connector_id: str) -> str:
    env_name = _credential_env_name(connector_id)
    return f"wildcard credential scope is not allowed for {connector_id}; configure APPV24_{env_name}_SCOPES without *"


def _credential_required_env_hint(connector_id: str, requested_scopes: tuple[str, ...]) -> str:
    env_name = _credential_env_name(connector_id)
    parts = [f"APPV24_{env_name}_CREDENTIAL_REF"]
    if requested_scopes:
        parts.append(f"APPV24_{env_name}_SCOPES={','.join(requested_scopes)}")
    return ", ".join(parts)


def _split_scope_env(value: str) -> tuple[str, ...]:
    return tuple(scope for scope in re.split(r"[\s,]+", value.strip()) if scope)
