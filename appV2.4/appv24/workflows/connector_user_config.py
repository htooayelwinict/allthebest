"""User-owned workflow connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from appv24.coding_agent.config import get_connectors_path

CONFIG_VERSION = 1
SUPPORTED_CONNECTORS = frozenset({"brave", "telegram"})
SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bot_token",
        "client_secret",
        "password",
        "refresh_token",
        "secret",
        "token",
        "x-api-key",
    }
)


@dataclass(frozen=True)
class ConnectorConfigProfile:
    connector_id: str
    profile: str
    credential_ref: str
    scopes: tuple[str, ...]
    config: Mapping[str, Any]
    source: str = "user-config"

    @property
    def available(self) -> bool:
        return bool(self.credential_ref.strip())


class ConnectorConfigResolver:
    def __init__(
        self,
        *,
        store: ConnectorConfigStore | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store or ConnectorConfigStore()
        self.env = dict(env or os.environ)

    def resolve(self, connector_id: str) -> ConnectorConfigProfile | None:
        normalized = normalize_connector_id(connector_id)
        configured = self.store.get(normalized)
        if configured is not None and configured.available:
            return configured
        return self._from_env(normalized)

    def list_profiles(self) -> tuple[ConnectorConfigProfile, ...]:
        configured = {profile.connector_id: profile for profile in self.store.list_profiles()}
        for connector_id in SUPPORTED_CONNECTORS:
            configured.setdefault(connector_id, self._from_env(connector_id))
        return tuple(
            profile
            for profile in sorted(configured.values(), key=lambda item: item.connector_id if item else "")
            if profile is not None
        )

    def _from_env(self, connector_id: str) -> ConnectorConfigProfile | None:
        env_name = _credential_env_name(connector_id)
        credential_ref = str(self.env.get(f"APPV24_{env_name}_CREDENTIAL_REF", "")).strip()
        scopes = _split_scopes(str(self.env.get(f"APPV24_{env_name}_SCOPES", "")))
        config = _connector_config_from_env(connector_id, self.env)
        if not credential_ref and not config:
            return None
        return ConnectorConfigProfile(
            connector_id=connector_id,
            profile="default",
            credential_ref=credential_ref,
            scopes=scopes,
            config=config,
            source="env",
        )


class ConnectorConfigStore:
    """Persist local connector profiles outside workflow packages."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or get_connectors_path())
        self._issues: tuple[str, ...] = ()

    @property
    def issues(self) -> tuple[str, ...]:
        return self._issues

    def get(self, connector_id: str) -> ConnectorConfigProfile | None:
        return self._read_profiles().get(normalize_connector_id(connector_id))

    def list_profiles(self) -> tuple[ConnectorConfigProfile, ...]:
        profiles = self._read_profiles()
        return tuple(profiles[key] for key in sorted(profiles))

    def upsert(self, profile: ConnectorConfigProfile) -> None:
        connector_id = normalize_connector_id(profile.connector_id)
        profiles = self._read_profiles()
        profiles[connector_id] = ConnectorConfigProfile(
            connector_id=connector_id,
            profile=profile.profile.strip() or "default",
            credential_ref=profile.credential_ref.strip(),
            scopes=tuple(str(scope).strip() for scope in profile.scopes if str(scope).strip()),
            config=dict(profile.config),
            source=profile.source.strip() or "user-config",
        )
        self._write_profiles(profiles)

    def remove(self, connector_id: str) -> bool:
        profiles = self._read_profiles()
        removed = profiles.pop(normalize_connector_id(connector_id), None)
        if removed is None:
            return False
        self._write_profiles(profiles)
        return True

    def _read_profiles(self) -> dict[str, ConnectorConfigProfile]:
        self._issues = ()
        if not self.path.exists():
            return {}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._issues = ("connector config is malformed JSON",)
            return {}
        except OSError as exc:
            self._issues = (f"connector config cannot be read: {exc}",)
            return {}

        if not isinstance(payload, dict):
            self._issues = ("connector config root must be an object",)
            return {}

        raw_profiles = payload.get("profiles", {})
        if not isinstance(raw_profiles, dict):
            self._issues = ("connector config profiles must be an object",)
            return {}

        profiles: dict[str, ConnectorConfigProfile] = {}
        for key, value in raw_profiles.items():
            profile = _profile_from_payload(str(key), value)
            if profile is not None:
                profiles[profile.connector_id] = profile
        return profiles

    def _write_profiles(self, profiles: Mapping[str, ConnectorConfigProfile]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)

        payload = {
            "version": CONFIG_VERSION,
            "profiles": {
                connector_id: _profile_to_payload(profile)
                for connector_id, profile in sorted(profiles.items())
            },
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"

        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
        except BaseException:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

        os.replace(tmp_path, self.path)
        os.chmod(self.path, 0o600)
        self._issues = ()


def redact_connector_profile(profile: ConnectorConfigProfile) -> dict[str, Any]:
    redacted: dict[str, Any] = {
        "connector_id": profile.connector_id,
        "profile": profile.profile,
        "credential_ref": profile.credential_ref,
        "scopes": list(profile.scopes),
        "source": profile.source,
        "secrets": "present" if _has_secret(profile.config) else "missing",
    }

    allowed_targets = profile.config.get("allowed_chat_ids")
    if isinstance(allowed_targets, list):
        redacted["allowed_targets"] = f"{len(allowed_targets)} configured"

    return redacted


def format_connector_config_list(profiles: tuple[ConnectorConfigProfile, ...] | list[ConnectorConfigProfile]) -> str:
    if not profiles:
        return "Configured workflow connectors:\n- none"

    lines = ["Configured workflow connectors:"]
    for profile in profiles:
        redacted = redact_connector_profile(profile)
        scopes = ", ".join(redacted["scopes"]) if redacted["scopes"] else "none"
        line = (
            f"- {redacted['connector_id']} ({redacted['profile']}): "
            f"credential_ref: {redacted['credential_ref']}; scopes: {scopes}; secrets: {redacted['secrets']}"
        )
        allowed_targets = redacted.get("allowed_targets")
        if allowed_targets:
            line = f"{line}; allowed_targets: {allowed_targets}"
        lines.append(line)
    return "\n".join(lines)


def default_connector_config_resolver(env: Mapping[str, str] | None = None) -> ConnectorConfigResolver:
    return ConnectorConfigResolver(env=env)


def normalize_connector_id(connector_id: str) -> str:
    return connector_id.strip().lower()


def _profile_from_payload(connector_id: str, value: Any) -> ConnectorConfigProfile | None:
    if not isinstance(value, dict):
        return None

    raw_config = value.get("config", {})
    raw_scopes = value.get("scopes", ())
    if not isinstance(raw_config, dict):
        return None
    if not isinstance(raw_scopes, list | tuple):
        return None

    return ConnectorConfigProfile(
        connector_id=normalize_connector_id(str(value.get("connector_id") or connector_id)),
        profile=str(value.get("profile") or "default"),
        credential_ref=str(value.get("credential_ref") or ""),
        scopes=tuple(str(scope) for scope in raw_scopes),
        config=dict(raw_config),
        source=str(value.get("source") or "user-config"),
    )


def _profile_to_payload(profile: ConnectorConfigProfile) -> dict[str, Any]:
    return {
        "connector_id": profile.connector_id,
        "profile": profile.profile,
        "credential_ref": profile.credential_ref,
        "scopes": list(profile.scopes),
        "config": dict(profile.config),
        "source": profile.source,
    }


def _has_secret(config: Mapping[str, Any]) -> bool:
    return _value_has_secret(config)


def _value_has_secret(value: object, *, key: str = "") -> bool:
    if key.lower() in SECRET_KEYS and bool(value):
        return True
    if isinstance(value, Mapping):
        return any(_value_has_secret(item, key=str(item_key)) for item_key, item in value.items())
    if isinstance(value, list | tuple):
        return any(_value_has_secret(item) for item in value)
    return False


def _connector_config_from_env(connector_id: str, env: Mapping[str, str]) -> dict[str, Any]:
    if connector_id == "brave":
        config: dict[str, Any] = {}
        api_key = str(env.get("APPV24_BRAVE_SEARCH_API_KEY", "")).strip()
        base_url = str(env.get("APPV24_BRAVE_SEARCH_BASE_URL", "")).strip()
        if api_key:
            config["api_key"] = api_key
        if base_url:
            config["base_url"] = base_url
        return config

    if connector_id == "telegram":
        config = {}
        bot_token = str(env.get("APPV24_TELEGRAM_BOT_TOKEN", "")).strip()
        allowed_chat_ids = _split_csv(str(env.get("APPV24_TELEGRAM_ALLOWED_CHAT_IDS", "")))
        base_url = str(env.get("APPV24_TELEGRAM_API_BASE_URL", "")).strip()
        if bot_token:
            config["bot_token"] = bot_token
        if allowed_chat_ids:
            config["allowed_chat_ids"] = list(allowed_chat_ids)
        if base_url:
            config["base_url"] = base_url
        return config

    return {}


def _credential_env_name(connector_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", connector_id).strip("_").upper()


def _split_scopes(value: str) -> tuple[str, ...]:
    return tuple(item for item in re.split(r"[\s,]+", value.strip()) if item)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())
