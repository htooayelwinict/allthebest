# appv24 Connector User Config MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build connector-owned user config for Brave and Telegram so workflow live runs can use appv24 local connector configuration before falling back to `.env`.

**Architecture:** Add a workflow-local connector config store and resolver under `appV2.4/appv24/workflows/`. Adapt the existing credential preflight and live transport construction to consume resolved connector config while preserving the current env fallback. Wire thin CLI/TUI setup commands on top of that boundary.

**Tech Stack:** Python dataclasses, JSON file persistence, existing appv24 config path helpers, existing workflow credential/live transport modules, pytest, existing CLI/TUI command patterns.

---

## Scope And Risk

This plan is appv24-only. It must not modify appv24 kernel/red-zone runtime files. It touches workflow green-zone code, tests, and the appv24 CLI/TUI command surfaces.

Keep `.env` compatibility. Existing commands that use only env variables must keep working while new commands and live workflow paths prefer user connector config.

## File Structure

- Create `appV2.4/appv24/workflows/connector_user_config.py`
  - Owns persisted connector profile schema, file permissions, redaction, config validation, and env fallback conversion.
- Modify `appV2.4/appv24/coding_agent/config.py`
  - Add `get_connectors_path()` helper beside `get_auth_path()`.
- Modify `appV2.4/appv24/workflows/credential_refs.py`
  - Add adapter functions that build `CredentialRefResolver` from connector user config with env fallback.
- Modify `appV2.4/appv24/workflows/live_transports.py`
  - Add resolver-based transport builders and keep `build_*_from_env()` as compatibility wrappers.
- Modify `appV2.4/appv24/workflows/live_readiness.py`
  - Use connector config resolver by default and render user-facing connector setup hints without leaking raw values.
- Modify `appV2.4/appv24/cli.py`
  - Add slash-command handlers for list/configure/remove.
  - Route guided/live/preflight commands through connector config resolver.
- Modify `appV2.4/appv24/tui/interactive_mode.py`
  - Add TUI command handlers and secret-prompt configuration flows.
  - Route guided/live/preflight commands through connector config resolver.
- Add tests:
  - `appV2.4/tests/test_workflow_connector_user_config.py`
  - Extend `appV2.4/tests/test_workflow_live_transports.py`
  - Extend `appV2.4/tests/test_workflow_live_readiness.py`
  - Extend `appV2.4/tests/test_cli.py`
  - Extend `appV2.4/tests/test_tui.py`
- Update docs:
  - `docs/appv24/workflow-run-live-path.md`
  - `.env.example`

---

### Task 1: Connector Config Store

**Files:**
- Create: `appV2.4/appv24/workflows/connector_user_config.py`
- Modify: `appV2.4/appv24/coding_agent/config.py`
- Test: `appV2.4/tests/test_workflow_connector_user_config.py`

- [ ] **Step 1: Write failing store tests**

Add `appV2.4/tests/test_workflow_connector_user_config.py`:

```python
from __future__ import annotations

import json
import stat
from pathlib import Path

from appv24.workflows.connector_user_config import (
    ConnectorConfigProfile,
    ConnectorConfigStore,
    format_connector_config_list,
    redact_connector_profile,
)


def test_connector_config_store_writes_secure_file_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "agent" / "connectors.json"
    store = ConnectorConfigStore(path)

    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-local",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key", "base_url": "https://search.test"},
            source="user-config",
        )
    )

    loaded = store.get("brave")
    assert loaded is not None
    assert loaded.connector_id == "brave"
    assert loaded.profile == "default"
    assert loaded.credential_ref == "brave-local"
    assert loaded.scopes == ("brave:search",)
    assert loaded.config["api_key"] == "brave-test-key"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_connector_config_store_lists_redacted_profiles(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-local",
            scopes=("telegram:subscribers:read", "telegram:send"),
            config={
                "bot_token": "telegram-test-token",
                "allowed_chat_ids": ["chat-a", "chat-b"],
                "base_url": "https://api.telegram.org",
            },
            source="user-config",
        )
    )

    rendered = format_connector_config_list(store.list_profiles())

    assert "telegram" in rendered
    assert "telegram:send" in rendered
    assert "secrets: present" in rendered
    assert "telegram-test-token" not in rendered
    assert "chat-a" not in rendered
    assert "chat-b" not in rendered


def test_connector_config_store_remove_deletes_profile(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-local",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key"},
            source="user-config",
        )
    )

    assert store.remove("brave") is True
    assert store.get("brave") is None
    assert store.remove("brave") is False


def test_connector_config_store_malformed_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "connectors.json"
    path.write_text("{not-json", encoding="utf-8")
    store = ConnectorConfigStore(path)

    assert store.list_profiles() == ()
    assert store.issues == ("connector config is malformed JSON",)


def test_redact_connector_profile_keeps_metadata_only() -> None:
    redacted = redact_connector_profile(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-local",
            scopes=("telegram:send",),
            config={"bot_token": "telegram-test-token", "allowed_chat_ids": ["chat-a"]},
            source="user-config",
        )
    )

    assert redacted == {
        "connector_id": "telegram",
        "profile": "default",
        "credential_ref": "telegram-local",
        "scopes": ["telegram:send"],
        "source": "user-config",
        "secrets": "present",
        "allowed_targets": "1 configured",
    }
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_user_config.py -q
```

Expected: fails with `ModuleNotFoundError: No module named 'appv24.workflows.connector_user_config'`.

- [ ] **Step 3: Add config path helper**

Modify `appV2.4/appv24/coding_agent/config.py` near `get_auth_path()`:

```python
def get_connectors_path() -> str:
    return str(Path(get_agent_dir()) / "connectors.json")
```

Add the JS-style alias near the existing aliases:

```python
getConnectorsPath = get_connectors_path
```

- [ ] **Step 4: Implement connector user config module**

Create `appV2.4/appv24/workflows/connector_user_config.py`:

```python
"""User-owned workflow connector configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from appv24.coding_agent.config import get_connectors_path

CONFIG_VERSION = 1
SUPPORTED_CONNECTORS = frozenset({"brave", "telegram"})
SECRET_KEYS = frozenset({"api_key", "bot_token", "access_token", "refresh_token", "client_secret"})


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


class ConnectorConfigStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or get_connectors_path()).expanduser()
        self.issues: tuple[str, ...] = ()

    def list_profiles(self) -> tuple[ConnectorConfigProfile, ...]:
        data = self._read()
        profiles = []
        connectors = data.get("connectors") if isinstance(data, dict) else None
        if not isinstance(connectors, dict):
            return ()
        for connector_id, raw_profile in sorted(connectors.items()):
            profile = self._parse_profile(str(connector_id), raw_profile)
            if profile is not None:
                profiles.append(profile)
        return tuple(profiles)

    def get(self, connector_id: str) -> ConnectorConfigProfile | None:
        normalized = normalize_connector_id(connector_id)
        for profile in self.list_profiles():
            if profile.connector_id == normalized:
                return profile
        return None

    def upsert(self, profile: ConnectorConfigProfile) -> None:
        connector_id = normalize_connector_id(profile.connector_id)
        if connector_id not in SUPPORTED_CONNECTORS:
            raise ValueError(f"unsupported workflow connector: {connector_id}")
        data = self._read()
        connectors = data.get("connectors")
        if not isinstance(connectors, dict):
            connectors = {}
        connectors[connector_id] = {
            "profile": profile.profile or "default",
            "credential_ref": profile.credential_ref,
            "scopes": list(profile.scopes),
            "config": dict(profile.config),
        }
        self._write({"version": CONFIG_VERSION, "connectors": connectors})

    def remove(self, connector_id: str) -> bool:
        normalized = normalize_connector_id(connector_id)
        data = self._read()
        connectors = data.get("connectors")
        if not isinstance(connectors, dict) or normalized not in connectors:
            return False
        del connectors[normalized]
        self._write({"version": CONFIG_VERSION, "connectors": connectors})
        return True

    def _read(self) -> dict[str, Any]:
        self.issues = ()
        if not self.path.exists():
            return {"version": CONFIG_VERSION, "connectors": {}}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.issues = ("connector config is malformed JSON",)
            return {"version": CONFIG_VERSION, "connectors": {}}
        except OSError as error:
            self.issues = (f"connector config could not be read: {type(error).__name__}",)
            return {"version": CONFIG_VERSION, "connectors": {}}
        if not isinstance(parsed, dict):
            self.issues = ("connector config root must be an object",)
            return {"version": CONFIG_VERSION, "connectors": {}}
        return parsed

    def _write(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(self.path, 0o600)

    def _parse_profile(self, connector_id: str, raw_profile: object) -> ConnectorConfigProfile | None:
        if not isinstance(raw_profile, Mapping):
            return None
        config = raw_profile.get("config")
        if not isinstance(config, Mapping):
            config = {}
        scopes = tuple(str(scope) for scope in raw_profile.get("scopes", ()) if isinstance(scope, str))
        return ConnectorConfigProfile(
            connector_id=normalize_connector_id(connector_id),
            profile=str(raw_profile.get("profile") or "default"),
            credential_ref=str(raw_profile.get("credential_ref") or ""),
            scopes=scopes,
            config=dict(config),
            source="user-config",
        )


def normalize_connector_id(connector_id: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", connector_id.strip().lower()).strip("_")


def redact_connector_profile(profile: ConnectorConfigProfile) -> dict[str, object]:
    redacted = {
        "connector_id": profile.connector_id,
        "profile": profile.profile,
        "credential_ref": profile.credential_ref,
        "scopes": list(profile.scopes),
        "source": profile.source,
        "secrets": "present" if _has_secret(profile.config) else "missing",
    }
    allowed_chat_ids = profile.config.get("allowed_chat_ids")
    if isinstance(allowed_chat_ids, list):
        redacted["allowed_targets"] = f"{len(allowed_chat_ids)} configured"
    return redacted


def format_connector_config_list(profiles: tuple[ConnectorConfigProfile, ...]) -> str:
    if not profiles:
        return "Configured workflow connectors:\n- none"
    lines = ["Configured workflow connectors:"]
    for profile in profiles:
        redacted = redact_connector_profile(profile)
        scopes = ", ".join(str(scope) for scope in redacted["scopes"]) or "none"
        line = (
            f"- {redacted['connector_id']}: configured via {redacted['source']}, "
            f"credential ref: {redacted['credential_ref'] or 'missing'}, "
            f"scopes: {scopes}, secrets: {redacted['secrets']}"
        )
        if "allowed_targets" in redacted:
            line = f"{line}, allowed targets: {redacted['allowed_targets']}"
        lines.append(line)
    return "\n".join(lines)


def _has_secret(config: Mapping[str, Any]) -> bool:
    for key, value in config.items():
        if key in SECRET_KEYS and str(value).strip():
            return True
    return False
```

- [ ] **Step 5: Run store tests to verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_user_config.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add appV2.4/appv24/coding_agent/config.py appV2.4/appv24/workflows/connector_user_config.py appV2.4/tests/test_workflow_connector_user_config.py
git commit -m "feat(appv24): add workflow connector user config store"
```

---

### Task 2: Resolver And Credential Preflight Adapter

**Files:**
- Modify: `appV2.4/appv24/workflows/connector_user_config.py`
- Modify: `appV2.4/appv24/workflows/credential_refs.py`
- Test: `appV2.4/tests/test_workflow_connector_user_config.py`

- [ ] **Step 1: Write failing resolver tests**

Append to `appV2.4/tests/test_workflow_connector_user_config.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import credential_resolver_from_connector_config


def test_connector_config_resolver_prefers_user_config_over_env(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-user",
            scopes=("brave:search",),
            config={"api_key": "brave-user-key", "base_url": "https://user.test"},
            source="user-config",
        )
    )
    resolver = ConnectorConfigResolver(
        store=store,
        env={
            "APPV24_BRAVE_CREDENTIAL_REF": "brave-env",
            "APPV24_BRAVE_SCOPES": "wrong:scope",
            "APPV24_BRAVE_SEARCH_API_KEY": "brave-env-key",
        },
    )

    resolved = resolver.resolve("brave")

    assert resolved is not None
    assert resolved.credential_ref == "brave-user"
    assert resolved.scopes == ("brave:search",)
    assert resolved.config["api_key"] == "brave-user-key"
    assert resolved.source == "user-config"


def test_connector_config_resolver_uses_env_fallback(tmp_path: Path) -> None:
    resolver = ConnectorConfigResolver(
        store=ConnectorConfigStore(tmp_path / "connectors.json"),
        env={
            "APPV24_BRAVE_CREDENTIAL_REF": "brave-env",
            "APPV24_BRAVE_SCOPES": "brave:search",
            "APPV24_BRAVE_SEARCH_API_KEY": "brave-env-key",
            "APPV24_BRAVE_SEARCH_BASE_URL": "https://env.test",
        },
    )

    resolved = resolver.resolve("brave")

    assert resolved is not None
    assert resolved.credential_ref == "brave-env"
    assert resolved.scopes == ("brave:search",)
    assert resolved.config["api_key"] == "brave-env-key"
    assert resolved.config["base_url"] == "https://env.test"
    assert resolved.source == "env"


def test_connector_config_resolver_builds_telegram_env_fallback(tmp_path: Path) -> None:
    resolver = ConnectorConfigResolver(
        store=ConnectorConfigStore(tmp_path / "connectors.json"),
        env={
            "APPV24_TELEGRAM_CREDENTIAL_REF": "telegram-env",
            "APPV24_TELEGRAM_SCOPES": "telegram:subscribers:read,telegram:send",
            "APPV24_TELEGRAM_BOT_TOKEN": "telegram-env-token",
            "APPV24_TELEGRAM_ALLOWED_CHAT_IDS": "chat-a, chat-b",
            "APPV24_TELEGRAM_API_BASE_URL": "https://telegram.test",
        },
    )

    resolved = resolver.resolve("telegram")

    assert resolved is not None
    assert resolved.credential_ref == "telegram-env"
    assert resolved.scopes == ("telegram:subscribers:read", "telegram:send")
    assert resolved.config["bot_token"] == "telegram-env-token"
    assert resolved.config["allowed_chat_ids"] == ["chat-a", "chat-b"]
    assert resolved.config["base_url"] == "https://telegram.test"


def test_credential_resolver_from_connector_config_uses_user_config(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-user",
            scopes=("telegram:send",),
            config={"bot_token": "telegram-user-token", "allowed_chat_ids": ["chat-a"]},
            source="user-config",
        )
    )

    credential_resolver = credential_resolver_from_connector_config(
        connector_ids=("telegram",),
        connector_config=ConnectorConfigResolver(store=store, env={}),
    )

    credential = credential_resolver.resolve("telegram")
    assert credential is not None
    assert credential.available is True
    assert credential.scopes == ("telegram:send",)
```

- [ ] **Step 2: Run resolver tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_user_config.py -q
```

Expected: fails because `ConnectorConfigResolver` and `credential_resolver_from_connector_config` do not exist.

- [ ] **Step 3: Implement resolver**

Append to `appV2.4/appv24/workflows/connector_user_config.py`:

```python
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
        return tuple(profile for profile in sorted(configured.values(), key=lambda item: item.connector_id) if profile is not None)

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


def default_connector_config_resolver(env: Mapping[str, str] | None = None) -> ConnectorConfigResolver:
    return ConnectorConfigResolver(env=env)


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
```

- [ ] **Step 4: Implement credential preflight adapter**

Modify `appV2.4/appv24/workflows/credential_refs.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigResolver
```

Add below `credential_resolver_from_env()`:

```python
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
        credentials[connector_id] = CredentialScope(available=resolved.available, scopes=tuple(resolved.scopes))
    return CredentialRefResolver(credentials)
```

- [ ] **Step 5: Run resolver tests to verify pass**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_connector_user_config.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add appV2.4/appv24/workflows/connector_user_config.py appV2.4/appv24/workflows/credential_refs.py appV2.4/tests/test_workflow_connector_user_config.py
git commit -m "feat(appv24): resolve workflow connector config"
```

---

### Task 3: Resolver-Based Live Transport Builders

**Files:**
- Modify: `appV2.4/appv24/workflows/live_transports.py`
- Test: `appV2.4/tests/test_workflow_live_transports.py`

- [ ] **Step 1: Write failing transport-builder tests**

Append to `appV2.4/tests/test_workflow_live_transports.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigProfile, ConnectorConfigResolver, ConnectorConfigStore
from appv24.workflows.live_transports import (
    build_live_production_transports_from_connector_config,
    build_live_read_transports_from_connector_config,
)


def test_build_live_read_transports_from_connector_user_config(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-local",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key", "base_url": "https://search.test"},
            source="user-config",
        )
    )
    store.upsert(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-local",
            scopes=("telegram:subscribers:read",),
            config={"allowed_chat_ids": ["chat-a"]},
            source="user-config",
        )
    )

    transports = build_live_read_transports_from_connector_config(
        ConnectorConfigResolver(store=store, env={"APPV24_WORKFLOW_LIVE_READ_ENABLED": "true"})
    )

    assert sorted(transports) == ["brave.search", "telegram.list_subscribers"]


def test_build_live_production_transports_from_connector_user_config(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-local",
            scopes=("telegram:send",),
            config={
                "bot_token": "telegram-test-token",
                "allowed_chat_ids": ["chat-a"],
                "base_url": "https://telegram.test",
            },
            source="user-config",
        )
    )

    transports = build_live_production_transports_from_connector_config(
        ConnectorConfigResolver(store=store, env={"APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true"})
    )

    assert sorted(transports) == ["telegram.broadcast_message", "telegram.send_message"]


def test_connector_user_config_transport_builder_preserves_env_gate(tmp_path: Path) -> None:
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-local",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key"},
            source="user-config",
        )
    )

    transports = build_live_read_transports_from_connector_config(ConnectorConfigResolver(store=store, env={}))

    assert transports == {}
```

- [ ] **Step 2: Run targeted transport tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py -q
```

Expected: fails because resolver-based builder functions do not exist.

- [ ] **Step 3: Add resolver imports and helper accessors**

Modify `appV2.4/appv24/workflows/live_transports.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigResolver
```

Add helper functions near `_split_csv` helpers:

```python
def _config_string(config: Mapping[str, Any], key: str, default: str = "") -> str:
    value = config.get(key, default)
    return str(value).strip()


def _config_string_list(config: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = config.get(key)
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return _split_csv(value)
    return ()
```

- [ ] **Step 4: Implement resolver-based live-read builder**

Add above `build_live_read_transports_from_env()`:

```python
def build_live_read_transports_from_connector_config(
    connector_config: ConnectorConfigResolver,
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> dict[str, LiveWorkflowTransport]:
    if not _env_enabled(connector_config.env.get(LIVE_READ_ENABLED_ENV)):
        return {}
    transports: dict[str, LiveWorkflowTransport] = {}
    brave = connector_config.resolve("brave")
    if brave is not None:
        api_key = _config_string(brave.config, "api_key")
        if api_key:
            base_url = _config_string(brave.config, "base_url", DEFAULT_BRAVE_SEARCH_BASE_URL) or DEFAULT_BRAVE_SEARCH_BASE_URL
            transports["brave.search"] = BraveSearchTransport(
                api_key=api_key,
                base_url=base_url,
                client_factory=client_factory,
            )
    telegram = connector_config.resolve("telegram")
    if telegram is not None:
        allowed_chat_ids = _config_string_list(telegram.config, "allowed_chat_ids")
        if allowed_chat_ids:
            transports["telegram.list_subscribers"] = TelegramListSubscribersTransport(
                allowed_chat_ids=allowed_chat_ids,
            )
    return transports
```

- [ ] **Step 5: Implement resolver-based production builder**

Add above `build_live_production_transports_from_env()`:

```python
def build_live_production_transports_from_connector_config(
    connector_config: ConnectorConfigResolver,
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> dict[str, LiveWorkflowTransport]:
    if not _env_enabled(connector_config.env.get(LIVE_PRODUCTION_ENABLED_ENV)):
        return {}
    telegram = connector_config.resolve("telegram")
    if telegram is None:
        return {}
    bot_token = _config_string(telegram.config, "bot_token")
    allowed_chat_ids = _config_string_list(telegram.config, "allowed_chat_ids")
    if not bot_token or not allowed_chat_ids:
        return {}
    base_url = _config_string(telegram.config, "base_url", DEFAULT_TELEGRAM_API_BASE_URL) or DEFAULT_TELEGRAM_API_BASE_URL
    return {
        "telegram.send_message": TelegramSendMessageTransport(
            token=bot_token,
            base_url=base_url,
            allowed_chat_ids=allowed_chat_ids,
            client_factory=client_factory,
        ),
        "telegram.broadcast_message": TelegramBroadcastMessageTransport(
            token=bot_token,
            base_url=base_url,
            allowed_chat_ids=allowed_chat_ids,
            client_factory=client_factory,
        ),
    }
```

- [ ] **Step 6: Update env wrappers to use resolver path for Brave/Telegram**

Modify `build_live_read_transports_from_env()` so it starts with:

```python
    if not _env_enabled(env.get(LIVE_READ_ENABLED_ENV)):
        return {}
    transports = build_live_read_transports_from_connector_config(
        ConnectorConfigResolver(env=env),
        client_factory=client_factory,
    )
```

Keep GitHub, source, and http additions after that. Remove the old Brave and Telegram env-building blocks from this function to prevent duplicate logic.

Modify `build_live_production_transports_from_env()` to delegate fully:

```python
    return build_live_production_transports_from_connector_config(
        ConnectorConfigResolver(env=env),
        client_factory=client_factory,
    )
```

- [ ] **Step 7: Run transport tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_transports.py appV2.4/tests/test_workflow_connector_user_config.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add appV2.4/appv24/workflows/live_transports.py appV2.4/tests/test_workflow_live_transports.py
git commit -m "feat(appv24): build live transports from connector config"
```

---

### Task 4: Readiness And Guided Run Integration

**Files:**
- Modify: `appV2.4/appv24/workflows/live_readiness.py`
- Modify: `appV2.4/appv24/cli.py`
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_workflow_live_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

Append to `appV2.4/tests/test_workflow_live_readiness.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigProfile, ConnectorConfigResolver, ConnectorConfigStore


def test_live_readiness_uses_connector_user_config_for_brave(tmp_path: Path) -> None:
    package = _write_live_read_workflow_package(
        tmp_path,
        name="brave-read",
        connector_id="brave",
        action_id="search",
        scopes=("brave:search",),
        action_ref="brave.search",
        outputs=("items", "source_count"),
    )
    store = ConnectorConfigStore(tmp_path / "connectors.json")
    store.upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-user",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key"},
            source="user-config",
        )
    )

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("brave.search",),
        ),
        env={"APPV24_WORKFLOW_LIVE_READ_ENABLED": "true"},
        connector_config=ConnectorConfigResolver(store=store, env={"APPV24_WORKFLOW_LIVE_READ_ENABLED": "true"}),
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "ready"
    assert report.configured_transports == ("brave.search",)
    assert "brave-test-key" not in rendered


def test_live_readiness_missing_connector_config_prefers_configure_hint(tmp_path: Path) -> None:
    package = _write_live_read_workflow_package(
        tmp_path,
        name="brave-read",
        connector_id="brave",
        action_id="search",
        scopes=("brave:search",),
        action_ref="brave.search",
        outputs=("items", "source_count"),
    )

    report = inspect_workflow_live_readiness(
        WorkflowRunRequest(
            package_path=package,
            fixture_name="happy_path.yaml",
            mode=WorkflowRunMode.LIVE_READ,
            connector_allowlist=("brave.search",),
        ),
        env={},
        connector_config=ConnectorConfigResolver(store=ConnectorConfigStore(tmp_path / "missing.json"), env={}),
    )

    rendered = format_workflow_live_readiness_report(report)
    assert report.status == "blocked"
    assert "missing connector config for brave; run /workflow-connector-configure brave" in rendered
    assert "APPV24_BRAVE_CREDENTIAL_REF" in rendered
```

- [ ] **Step 2: Run readiness tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_readiness.py -q
```

Expected: fails because `inspect_workflow_live_readiness()` has no `connector_config` parameter and still only uses env.

- [ ] **Step 3: Add connector config parameter to readiness**

Modify imports in `appV2.4/appv24/workflows/live_readiness.py`:

```python
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import credential_resolver_from_connector_config
from appv24.workflows.live_transports import (
    build_live_production_transports_from_connector_config,
    build_live_read_transports_from_connector_config,
)
```

Update `inspect_workflow_live_readiness()` signature:

```python
def inspect_workflow_live_readiness(
    request: WorkflowRunRequest,
    *,
    env: Mapping[str, str],
    connector_config: ConnectorConfigResolver | None = None,
) -> WorkflowLiveReadinessReport:
```

Inside the function, add:

```python
    connector_config = connector_config or ConnectorConfigResolver(env=env)
```

Replace the two `credential_resolver_for_package_env(request.package_path, env)` calls in `live_readiness.py` with:

```python
credential_resolver_for_package_connector_config(request.package_path, connector_config)
```

Keep env-only behavior for middleware and external connector env checks.

- [ ] **Step 4: Use resolver-based configured transport discovery**

Modify the helper that computes configured transports in `live_readiness.py` so it accepts `connector_config`:

```python
def _configured_transports(mode: WorkflowRunMode, env: Mapping[str, str], connector_config: ConnectorConfigResolver) -> tuple[str, ...]:
    if mode is WorkflowRunMode.LIVE_READ:
        return tuple(sorted(build_live_read_transports_from_connector_config(connector_config).keys()))
    if mode is WorkflowRunMode.LIVE_PRODUCTION:
        return tuple(sorted(build_live_production_transports_from_connector_config(connector_config).keys()))
    return tuple(sorted(_legacy_configured_transports(mode, env)))
```

Keep the existing old body as `_legacy_configured_transports()` for live-draft/Gmail, GitHub, source, and http behavior until those connectors move into user config.

- [ ] **Step 5: Add setup hints without changing preflight semantics**

Add a helper in `live_readiness.py`:

```python
def _connector_config_hint(connector_id: str, requested_scopes: tuple[str, ...]) -> str:
    env_name = credential_env_name(connector_id)
    pieces = [f"missing connector config for {connector_id}; run /workflow-connector-configure {connector_id}"]
    env_hint = [f"APPV24_{env_name}_CREDENTIAL_REF"]
    if requested_scopes:
        env_hint.append(f"APPV24_{env_name}_SCOPES={','.join(requested_scopes)}")
    pieces.append(f"env fallback: {', '.join(env_hint)}")
    return "; ".join(pieces)
```

When credential preflight returns a missing credential issue for a connector not resolved by connector config, append the connector setup hint in addition to the existing safe env issue. Do not include raw credential values.

- [ ] **Step 6: Route CLI guided/live commands through resolver**

In `appV2.4/appv24/cli.py`, import:

```python
from appv24.workflows.connector_user_config import ConnectorConfigResolver
from appv24.workflows.credential_refs import credential_resolver_from_connector_config
from appv24.workflows.live_transports import (
    build_live_production_transports_from_connector_config,
    build_live_read_transports_from_connector_config,
)
```

Add helper:

```python
def _workflow_connector_config(env: Mapping[str, str] | None = None) -> ConnectorConfigResolver:
    return ConnectorConfigResolver(env=env or os.environ)
```

For guided/live-read/live-production/preflight handlers, create:

```python
connector_config = _workflow_connector_config()
```

Replace Brave/Telegram transport builder calls with resolver-based calls:

```python
LiveWorkflowBroker(build_live_read_transports_from_connector_config(connector_config), mode="live-read")
LiveWorkflowBroker(
    build_live_production_transports_from_connector_config(connector_config),
    mode="live-production",
    production_enabled=True,
)
```

Add a package helper in `credential_refs.py` so CLI/TUI handlers do not parse connector ids themselves:

```python
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
    return credential_resolver_from_connector_config(connector_ids, connector_config)
```

Then use `credential_resolver_for_package_connector_config(package_path, connector_config)`.

- [ ] **Step 7: Route TUI guided/live commands through resolver**

In `appV2.4/appv24/tui/interactive_mode.py`, mirror the CLI changes:

```python
connector_config = ConnectorConfigResolver(env=os.environ)
```

Use `build_live_read_transports_from_connector_config(connector_config)`, `build_live_production_transports_from_connector_config(connector_config)`, and `credential_resolver_for_package_connector_config(package_path, connector_config)` in guided/preflight/live handlers.

- [ ] **Step 8: Run readiness and existing workflow slices**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_readiness.py appV2.4/tests/test_workflow_live_runner.py appV2.4/tests/test_workflow_phase_runner.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 4**

Run:

```bash
git add appV2.4/appv24/workflows/live_readiness.py appV2.4/appv24/workflows/credential_refs.py appV2.4/appv24/cli.py appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_workflow_live_readiness.py
git commit -m "feat(appv24): use connector config for workflow readiness"
```

---

### Task 5: CLI Connector Config Commands

**Files:**
- Modify: `appV2.4/appv24/cli.py`
- Test: `appV2.4/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI command tests**

Append to `appV2.4/tests/test_cli.py` near workflow command tests:

```python
def _run_workflow_connector_cli_prompts(monkeypatch, tmp_path: Path, prompts: list[str]) -> int:
    from appv24 import cli
    from appv24.ai.types import Model

    prompt_iter = iter(prompts)

    class FakeApp:
        def __init__(self, **kwargs):
            self.cwd = Path(kwargs["cwd"])
            self.messages = []

        def run_turn(self, prompt: str) -> None:
            self.messages.append(prompt)

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(
        cli,
        "_startup_model_from_env",
        lambda *args, **kwargs: cli._StartupModelSelection(
            model=Model(id="m", name="m", api="faux", provider="faux", base_url=""),
            thinking_level=kwargs.get("cli_thinking"),
            scoped_models=[],
        ),
    )
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompt_iter))
    return cli.main(["--cwd", str(tmp_path), "--profile", "workflow-designer-lite", "--plain"])


def test_cli_workflow_connector_list_redacts_user_config(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(tmp_path / "agent"))
    from appv24.workflows.connector_user_config import ConnectorConfigProfile, ConnectorConfigStore
    from appv24.coding_agent.config import get_connectors_path

    ConnectorConfigStore(get_connectors_path()).upsert(
        ConnectorConfigProfile(
            connector_id="brave",
            profile="default",
            credential_ref="brave-local",
            scopes=("brave:search",),
            config={"api_key": "brave-test-key"},
            source="user-config",
        )
    )

    exit_code = _run_workflow_connector_cli_prompts(monkeypatch, tmp_path, ["/workflow-connector-list", "/exit"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Configured workflow connectors:" in captured.out
    assert "brave" in captured.out
    assert "brave:search" in captured.out
    assert "brave-test-key" not in captured.out


def test_cli_workflow_connector_remove_deletes_profile(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(tmp_path / "agent"))
    from appv24.workflows.connector_user_config import ConnectorConfigProfile, ConnectorConfigStore
    from appv24.coding_agent.config import get_connectors_path

    store = ConnectorConfigStore(get_connectors_path())
    store.upsert(
        ConnectorConfigProfile(
            connector_id="telegram",
            profile="default",
            credential_ref="telegram-local",
            scopes=("telegram:send",),
            config={"bot_token": "telegram-test-token", "allowed_chat_ids": ["chat-a"]},
            source="user-config",
        )
    )

    exit_code = _run_workflow_connector_cli_prompts(
        monkeypatch,
        tmp_path,
        ["/workflow-connector-remove telegram", "/workflow-connector-list", "/exit"],
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Removed workflow connector config: telegram" in captured.out
    assert "- none" in captured.out
    assert "telegram-test-token" not in captured.out
```

- [ ] **Step 2: Run CLI tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py -k "workflow_connector" -q
```

Expected: fails because command handlers do not exist.

- [ ] **Step 3: Add CLI imports and handlers**

In `appV2.4/appv24/cli.py`, import:

```python
from appv24.workflows.connector_user_config import (
    ConnectorConfigStore,
    format_connector_config_list,
    normalize_connector_id,
)
```

Add handlers:

```python
def _handle_workflow_connector_list_command(app: CodingApp, prompt: str) -> bool:
    if prompt.strip() != "/workflow-connector-list":
        return False
    store = ConnectorConfigStore()
    print(format_connector_config_list(store.list_profiles()))
    return True


def _handle_workflow_connector_remove_command(app: CodingApp, prompt: str) -> bool:
    if not _is_workflow_connector_remove_command(prompt):
        return False
    args = prompt.split()
    try:
        if len(args) != 2:
            raise ValueError("expected /workflow-connector-remove brave|telegram")
        connector_id = normalize_connector_id(args[1])
        removed = ConnectorConfigStore().remove(connector_id)
        if removed:
            print(f"Removed workflow connector config: {connector_id}")
        else:
            print(f"Workflow connector config not found: {connector_id}")
    except Exception as error:
        print(f"Workflow connector remove failed: {error}")
    return True


def _is_workflow_connector_remove_command(prompt: str) -> bool:
    return prompt == "/workflow-connector-remove" or prompt.startswith("/workflow-connector-remove ")
```

- [ ] **Step 4: Wire CLI dispatch**

Add the handlers to the prompt dispatch list before generic model turns, next to other workflow commands:

```python
or _handle_workflow_connector_list_command(app, prompt)
or _handle_workflow_connector_remove_command(app, prompt)
```

Do not implement non-interactive secret prompts in plain CLI in this task. For `/workflow-connector-configure`, return a safe message:

```python
def _handle_workflow_connector_configure_command(app: CodingApp, prompt: str) -> bool:
    if not (prompt == "/workflow-connector-configure" or prompt.startswith("/workflow-connector-configure ")):
        return False
    print("Workflow connector configure is available in the TUI so secrets can be entered with masked prompts.")
    return True
```

Wire it into dispatch.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_cli.py -k "workflow_connector" -q
```

Expected: tests pass.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add appV2.4/appv24/cli.py appV2.4/tests/test_cli.py
git commit -m "feat(appv24): add cli workflow connector config commands"
```

---

### Task 6: TUI Connector Config Commands

**Files:**
- Modify: `appV2.4/appv24/tui/interactive_mode.py`
- Test: `appV2.4/tests/test_tui.py`

- [ ] **Step 1: Write failing TUI tests**

Append to `appV2.4/tests/test_tui.py` near workflow command tests:

```python
def test_tui_workflow_connector_configure_brave_masks_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(tmp_path / "agent"))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=100, rows=20), enable_tui=True)
    inputs = iter([
        "/workflow-connector-configure brave",
        "brave-local",
        "brave:search",
        "brave-test-key",
        "",
        "/workflow-connector-list",
        "/exit",
    ])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = mode.history.render(100, 200)
    assert "Saved workflow connector config: brave" in rendered
    assert "brave:search" in rendered
    assert "brave-test-key" not in rendered


def test_tui_workflow_connector_configure_telegram_masks_secret_and_targets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(tmp_path / "agent"))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=100, rows=20), enable_tui=True)
    inputs = iter([
        "/workflow-connector-configure telegram",
        "telegram-local",
        "telegram:subscribers:read,telegram:send",
        "telegram-test-token",
        "chat-a,chat-b",
        "",
        "/workflow-connector-list",
        "/exit",
    ])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = mode.history.render(100, 200)
    assert "Saved workflow connector config: telegram" in rendered
    assert "telegram:send" in rendered
    assert "allowed targets: 2 configured" in rendered
    assert "telegram-test-token" not in rendered
    assert "chat-a" not in rendered
    assert "chat-b" not in rendered


def test_tui_workflow_connector_remove(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPV24_CODING_AGENT_DIR", str(tmp_path / "agent"))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(columns=100, rows=20), enable_tui=True)
    inputs = iter([
        "/workflow-connector-configure brave",
        "brave-local",
        "brave:search",
        "brave-test-key",
        "",
        "/workflow-connector-remove brave",
        "/workflow-connector-list",
        "/exit",
    ])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    rendered = mode.history.render(100, 200)
    assert "Removed workflow connector config: brave" in rendered
    assert "- none" in rendered
    assert "brave-test-key" not in rendered
```

- [ ] **Step 2: Run TUI tests to verify failure**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py -k "workflow_connector" -q
```

Expected: fails because TUI command handlers do not exist.

- [ ] **Step 3: Add TUI imports**

In `appV2.4/appv24/tui/interactive_mode.py`, import:

```python
from appv24.workflows.connector_user_config import (
    ConnectorConfigProfile,
    ConnectorConfigStore,
    format_connector_config_list,
    normalize_connector_id,
)
```

- [ ] **Step 4: Add help and command palette entries**

Add to the command list near other workflow commands:

```python
{"name": "workflow-connector-list", "description": "List configured workflow connectors"},
{"name": "workflow-connector-configure", "description": "Configure workflow connector credentials"},
{"name": "workflow-connector-remove", "description": "Remove workflow connector configuration"},
```

Add to `_run_help_command()`:

```python
"/workflow-connector-list - List configured workflow connectors.",
"/workflow-connector-configure brave|telegram - Configure Brave or Telegram connector credentials.",
"/workflow-connector-remove brave|telegram - Remove workflow connector configuration.",
```

- [ ] **Step 5: Add TUI command handlers**

Add methods in `InteractiveMode` near other workflow command handlers:

```python
    def _run_workflow_connector_list_command(self, prompt: str) -> bool:
        if prompt.strip() != "/workflow-connector-list":
            return False
        rendered = format_connector_config_list(ConnectorConfigStore().list_profiles())
        self.history.add(StatusLine("Workflow connector config", kind="info"))
        self.history.add(Text(rendered))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _run_workflow_connector_remove_command(self, prompt: str) -> bool:
        if not (prompt == "/workflow-connector-remove" or prompt.startswith("/workflow-connector-remove ")):
            return False
        args = prompt.split()
        if len(args) != 2:
            self.history.add(StatusLine("Workflow connector remove failed: expected /workflow-connector-remove brave|telegram", kind="error"))
            return True
        connector_id = normalize_connector_id(args[1])
        removed = ConnectorConfigStore().remove(connector_id)
        message = f"Removed workflow connector config: {connector_id}" if removed else f"Workflow connector config not found: {connector_id}"
        self.history.add(StatusLine(message, kind="info"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True

    def _run_workflow_connector_configure_command(self, prompt: str) -> bool:
        if not (prompt == "/workflow-connector-configure" or prompt.startswith("/workflow-connector-configure ")):
            return False
        args = prompt.split()
        if len(args) != 2:
            self.history.add(StatusLine("Workflow connector configure failed: expected /workflow-connector-configure brave|telegram", kind="error"))
            return True
        connector_id = normalize_connector_id(args[1])
        try:
            profile = self._prompt_workflow_connector_profile(connector_id)
            ConnectorConfigStore().upsert(profile)
        except Exception as error:
            self.history.add(StatusLine(f"Workflow connector configure failed: {error}", kind="error"))
            return True
        self.history.add(StatusLine(f"Saved workflow connector config: {connector_id}", kind="info"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()
        return True
```

Add prompt helper:

```python
    def _prompt_workflow_connector_profile(self, connector_id: str) -> ConnectorConfigProfile:
        if connector_id == "brave":
            credential_ref = self.prompt_extension_input("Credential ref") or "brave-local"
            scopes = _split_connector_scope_input(self.prompt_extension_input("Scopes") or "brave:search")
            api_key = self.prompt_extension_input("Brave Search API key", options={"secret": True}) or ""
            base_url = self.prompt_extension_input("Brave Search base URL") or ""
            config = {"api_key": api_key}
            if base_url.strip():
                config["base_url"] = base_url.strip()
            return ConnectorConfigProfile(
                connector_id="brave",
                profile="default",
                credential_ref=credential_ref.strip(),
                scopes=scopes,
                config=config,
                source="user-config",
            )
        if connector_id == "telegram":
            credential_ref = self.prompt_extension_input("Credential ref") or "telegram-local"
            scopes = _split_connector_scope_input(
                self.prompt_extension_input("Scopes") or "telegram:subscribers:read,telegram:send"
            )
            bot_token = self.prompt_extension_input("Telegram bot token", options={"secret": True}) or ""
            allowed_chat_ids = _split_connector_scope_input(self.prompt_extension_input("Allowed chat IDs") or "")
            base_url = self.prompt_extension_input("Telegram API base URL") or ""
            config = {"bot_token": bot_token, "allowed_chat_ids": list(allowed_chat_ids)}
            if base_url.strip():
                config["base_url"] = base_url.strip()
            return ConnectorConfigProfile(
                connector_id="telegram",
                profile="default",
                credential_ref=credential_ref.strip(),
                scopes=scopes,
                config=config,
                source="user-config",
            )
        raise ValueError(f"unsupported workflow connector: {connector_id}")
```

Add module helper near parser helpers:

```python
def _split_connector_scope_input(value: str) -> tuple[str, ...]:
    return tuple(item for item in re.split(r"[\s,]+", value.strip()) if item)
```

If `re` is not already imported in `interactive_mode.py`, add `import re`.

- [ ] **Step 6: Wire TUI dispatch**

Add the handlers to the interactive prompt command dispatch before model turns:

```python
or self._run_workflow_connector_list_command(prompt)
or self._run_workflow_connector_configure_command(prompt)
or self._run_workflow_connector_remove_command(prompt)
```

- [ ] **Step 7: Run TUI connector tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_tui.py -k "workflow_connector" -q
```

Expected: tests pass and rendered output does not contain fake secret or chat ID values.

- [ ] **Step 8: Commit Task 6**

Run:

```bash
git add appV2.4/appv24/tui/interactive_mode.py appV2.4/tests/test_tui.py
git commit -m "feat(appv24): configure workflow connectors in tui"
```

---

### Task 7: Docs And Compatibility Cleanup

**Files:**
- Modify: `docs/appv24/workflow-run-live-path.md`
- Modify: `.env.example`
- Test: `appV2.4/tests/test_workflow_live_env_template.py`

- [ ] **Step 1: Update docs text**

In `docs/appv24/workflow-run-live-path.md`, add a section near the live setup instructions:

```markdown
### Connector User Config

Normal local workflow use should configure supported connectors through the app:

```text
/workflow-connector-configure brave
/workflow-connector-configure telegram
/workflow-connector-list
```

The connector config file lives under the appv24 agent directory as
`connectors.json`. appv24 writes the parent directory with `0700` permissions and
the file with `0600` permissions. Readiness reports and run artifacts show
connector ids, credential refs, scope names, and setup hints only. They must not
show API keys, bot tokens, OAuth tokens, or raw connector target lists.

The existing `APPV24_*` environment variables remain supported for CI and manual
operator runs.
```

- [ ] **Step 2: Update `.env.example`**

Add comments above connector variables:

```dotenv
# Workflow connectors can be configured with /workflow-connector-configure.
# These env vars remain supported as CI/manual fallback and should not be the
# normal local user path.
```

Do not add real values.

- [ ] **Step 3: Keep live env template as fallback**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests/test_workflow_live_env_template.py -q
```

Expected: existing env-template tests still pass because `.env` fallback remains documented and supported.

- [ ] **Step 4: Commit Task 7**

Run:

```bash
git add docs/appv24/workflow-run-live-path.md .env.example
git commit -m "docs(appv24): document workflow connector user config"
```

---

### Task 8: Final QA And Live-Path Smoke

**Files:**
- No new source files expected.
- Use existing tests and one local TUI/CLI smoke without real secrets.

- [ ] **Step 1: Run targeted connector workflow tests**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest \
  appV2.4/tests/test_workflow_connector_user_config.py \
  appV2.4/tests/test_workflow_live_transports.py \
  appV2.4/tests/test_workflow_live_readiness.py \
  appV2.4/tests/test_cli.py -k "workflow_connector or workflow_run_guided or workflow_live_readiness" \
  appV2.4/tests/test_tui.py -k "workflow_connector or workflow_run_guided or workflow_live_readiness" \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full appv24 Python QA**

Run:

```bash
PYTHONPATH=appV2.4 .venv/bin/python -m pytest appV2.4/tests
```

Expected: all tests pass.

- [ ] **Step 3: Run appv24 npm CLI QA**

Run:

```bash
npm --prefix packages/appv24-cli test
```

Expected: `15 passed`.

- [ ] **Step 4: Run pack dry-run**

Run:

```bash
npm --prefix packages/appv24-cli run pack:dry-run
```

Expected: command exits 0. Do not publish.

- [ ] **Step 5: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Run safe credential scan**

Run:

```bash
git diff --cached -- . ':!.env' ':!*.lock' | rg -n "APPV24_.*(TOKEN|KEY|SECRET)=|sk-[A-Za-z0-9]|ghp_|xoxb-|telegram-test-token|brave-test-key" || true
```

Expected: no real secrets. Fake test values may appear only in test files and must be obvious literals such as `telegram-test-token` and `brave-test-key`.

- [ ] **Step 7: Commit any final fixes**

If final QA requires fixes, commit each focused fix:

```bash
git add appV2.4/appv24/workflows/connector_user_config.py appV2.4/appv24/workflows/live_transports.py appV2.4/appv24/workflows/live_readiness.py appV2.4/appv24/cli.py appV2.4/appv24/tui/interactive_mode.py
git commit -m "fix(appv24): harden workflow connector user config"
```

- [ ] **Step 8: Report final state**

Run:

```bash
git status --short --branch
git log --oneline -8
```

Expected: branch ahead of upstream with the connector user config MVP commits and no uncommitted tracked changes.
