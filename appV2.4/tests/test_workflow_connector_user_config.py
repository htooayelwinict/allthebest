from __future__ import annotations

import stat
from pathlib import Path

from appv24.workflows.connector_user_config import (
    ConnectorConfigProfile,
    ConnectorConfigResolver,
    ConnectorConfigStore,
    format_connector_config_list,
    redact_connector_profile,
)
from appv24.workflows.credential_refs import credential_resolver_from_connector_config


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


def test_redact_connector_profile_detects_nested_custom_action_secrets() -> None:
    rendered = format_connector_config_list(
        [
            ConnectorConfigProfile(
                connector_id="crm",
                profile="default",
                credential_ref="crm-local",
                scopes=("crm:read",),
                config={
                    "actions": {
                        "lookup_contact": {
                            "url": "https://crm.test/lookup",
                            "headers": {"Authorization": "Bearer crm-token"},
                        }
                    }
                },
                source="user-config",
            )
        ]
    )

    assert "crm" in rendered
    assert "secrets: present" in rendered
    assert "crm-token" not in rendered
    assert "Authorization" not in rendered


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
