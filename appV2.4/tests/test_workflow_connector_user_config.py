from __future__ import annotations

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
