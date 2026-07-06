from __future__ import annotations

import base64
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from typing import Any

import httpx

from appv24.workflows.broker import BrokerRequest
from appv24.workflows.live_transports import (
    GmailCreateDraftTransport,
    GitHubDependabotAlertsTransport,
    HttpFetchTransport,
    SourceFetchDocumentTransport,
    SourceFetchItemsTransport,
    TelegramSendMessageTransport,
    build_live_draft_transports_from_env,
    build_live_production_transports_from_env,
    build_live_read_transports_from_env,
)


def test_live_read_transport_registry_disabled_by_default() -> None:
    transports = build_live_read_transports_from_env(
        {
            "APPV24_GITHUB_TOKEN": "github-token",
        }
    )

    assert transports == {}


def test_live_read_transport_registry_registers_github_when_enabled() -> None:
    transports = build_live_read_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_GITHUB_TOKEN": "github-token",
        }
    )

    assert tuple(transports) == ("github.list_dependabot_alerts",)
    assert isinstance(transports["github.list_dependabot_alerts"], GitHubDependabotAlertsTransport)


def test_live_read_transport_registry_registers_source_reader_when_enabled() -> None:
    transports = build_live_read_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_SOURCE_BASE_URL": "https://source.test/api",
        }
    )

    assert tuple(transports) == ("source.fetch_document", "source.fetch_items")
    assert isinstance(transports["source.fetch_document"], SourceFetchDocumentTransport)
    assert isinstance(transports["source.fetch_items"], SourceFetchItemsTransport)


def test_live_read_transport_registry_registers_http_fetch_with_allowed_hosts() -> None:
    transports = build_live_read_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_READ_ENABLED": "true",
            "APPV24_HTTP_FETCH_ALLOWED_HOSTS": "api.example.com,news.example.com",
        }
    )

    assert tuple(transports) == ("http.fetch",)
    assert isinstance(transports["http.fetch"], HttpFetchTransport)


def test_live_draft_transport_registry_disabled_by_default() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_GMAIL_ACCESS_TOKEN": "gmail-token",
        }
    )

    assert transports == {}


def test_live_draft_transport_registry_registers_gmail_when_enabled() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_ACCESS_TOKEN": "gmail-token",
        }
    )

    assert tuple(transports) == ("gmail.create_draft",)
    assert isinstance(transports["gmail.create_draft"], GmailCreateDraftTransport)


def test_live_draft_transport_registry_registers_gmail_with_refresh_credentials() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
            "APPV24_GMAIL_CLIENT_SECRET": "client-secret",
        }
    )

    assert tuple(transports) == ("gmail.create_draft",)
    assert isinstance(transports["gmail.create_draft"], GmailCreateDraftTransport)


def test_live_draft_transport_registry_blocks_incomplete_refresh_credentials() -> None:
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
        }
    )

    assert transports == {}


def test_live_production_transport_registry_disabled_by_default() -> None:
    transports = build_live_production_transports_from_env(
        {
            "APPV24_TELEGRAM_BOT_TOKEN": "telegram-token",
        }
    )

    assert transports == {}


def test_live_production_transport_registry_registers_telegram_when_enabled() -> None:
    transports = build_live_production_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED": "true",
            "APPV24_TELEGRAM_BOT_TOKEN": "telegram-token",
        }
    )

    assert tuple(transports) == ("telegram.send_message",)
    assert isinstance(transports["telegram.send_message"], TelegramSendMessageTransport)


def test_github_dependabot_transport_calls_api_and_slims_outputs() -> None:
    client = FakeClient(
        [
            {
                "number": 7,
                "state": "open",
                "dependency": {
                    "package": {"ecosystem": "pip", "name": "django"},
                    "manifest_path": "requirements.txt",
                    "scope": "runtime",
                },
                "security_advisory": {
                    "ghsa_id": "GHSA-test",
                    "cve_id": "CVE-2026-0001",
                    "severity": "high",
                    "summary": "Example issue",
                    "description": "must not be copied",
                },
                "html_url": "https://github.com/owner/repo/security/dependabot/7",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": "2026-07-02T00:00:00Z",
                "dismissed_by": {"login": "not-needed"},
            }
        ]
    )
    transport = GitHubDependabotAlertsTransport(
        token="github-token",
        base_url="https://api.github.test",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={
                "with": {
                    "state": "open",
                    "severity": "high",
                    "per_page": 250,
                }
            },
            fixture={"input": {"repo": "owner/repo"}},
            allowed_outputs=("alerts", "count"),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.provider_id == "github"
    assert result.issues == ()
    assert result.outputs == {
        "alerts": [
            {
                "number": 7,
                "state": "open",
                "dependency": {
                    "ecosystem": "pip",
                    "name": "django",
                    "manifest_path": "requirements.txt",
                    "scope": "runtime",
                },
                "advisory": {
                    "ghsa_id": "GHSA-test",
                    "cve_id": "CVE-2026-0001",
                    "severity": "high",
                    "summary": "Example issue",
                },
                "html_url": "https://github.com/owner/repo/security/dependabot/7",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": "2026-07-02T00:00:00Z",
            }
        ],
        "count": 1,
    }
    assert client.calls == [
        {
            "url": "https://api.github.test/repos/owner/repo/dependabot/alerts",
            "headers": {
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer github-token",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            "params": {
                "state": "open",
                "severity": "high",
                "per_page": "100",
            },
        }
    ]


def test_github_dependabot_transport_resolves_input_refs_from_with_fields() -> None:
    client = FakeClient([])
    transport = GitHubDependabotAlertsTransport(
        token="github-token",
        base_url="https://api.github.test",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={
                "with": {
                    "repository": {"inputRef": "target_repo"},
                    "state": {"inputRef": "alert_state"},
                    "severity": {"inputRef": "minimum_severity"},
                    "per_page": {"inputRef": "page_size"},
                }
            },
            fixture={
                "input": {
                    "target_repo": "owner/repo",
                    "alert_state": "open",
                    "minimum_severity": "critical",
                    "page_size": 75,
                    "repo": "fallback/repo",
                }
            },
            allowed_outputs=("alerts", "count"),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert client.calls == [
        {
            "url": "https://api.github.test/repos/owner/repo/dependabot/alerts",
            "headers": {
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer github-token",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            "params": {
                "state": "open",
                "severity": "critical",
                "per_page": "75",
            },
        }
    ]


def test_github_dependabot_transport_blocks_missing_repo_without_http() -> None:
    client = FakeClient([])
    transport = GitHubDependabotAlertsTransport(
        token="github-token",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="inspect_alerts",
            connector_action="github.list_dependabot_alerts",
            input_ref={"with": {}},
            fixture={"input": {}},
            allowed_outputs=("alerts",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("github.list_dependabot_alerts requires repo as owner/repo",)
    assert client.calls == []


def test_source_fetch_items_transport_calls_base_url_and_slims_outputs() -> None:
    client = FakeClient(
        {
            "items": [
                {
                    "id": "item-1",
                    "title": "NVDA announces guidance",
                    "url": "https://source.test/news/1",
                    "published_at": "2026-07-06T00:00:00Z",
                    "body": "must not be persisted",
                }
            ],
            "source_count": 9,
            "raw": "must not be persisted",
        }
    )
    transport = SourceFetchItemsTransport(base_url="https://source.test/api/items", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="fetch_news",
            connector_action="source.fetch_items",
            input_ref={
                "with": {
                    "query": "public company catalysts",
                    "tickers": ["NVDA", "MSFT"],
                    "limit": 50,
                }
            },
            fixture={"input": {}},
            allowed_outputs=("items", "source_count"),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.provider_id == "source"
    assert result.issues == ()
    assert result.outputs == {
        "items": [
            {
                "id": "item-1",
                "title": "NVDA announces guidance",
                "url": "https://source.test/news/1",
                "published_at": "2026-07-06T00:00:00Z",
            }
        ],
        "source_count": 9,
    }
    assert client.calls == [
        {
            "url": "https://source.test/api/items",
            "headers": {"Accept": "application/json"},
            "params": {
                "limit": "50",
                "query": "public company catalysts",
                "tickers": "NVDA,MSFT",
            },
        }
    ]


def test_http_fetch_transport_blocks_unapproved_host_before_http() -> None:
    client = FakeClient({"ok": True})
    transport = HttpFetchTransport(allowed_hosts=("api.example.com",), client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="fetch_news",
            connector_action="http.fetch",
            input_ref={"with": {"url": "https://evil.example/news.json"}},
            fixture={"input": {}},
            allowed_outputs=("status_code", "url", "json", "text"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("http.fetch URL host is not allowlisted: evil.example",)
    assert client.calls == []


def test_http_fetch_transport_fetches_allowlisted_json() -> None:
    client = FakeClient({"items": [{"id": "1", "title": "Market update"}]})
    transport = HttpFetchTransport(allowed_hosts=("api.example.com",), client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="fetch_news",
            connector_action="http.fetch",
            input_ref={
                "with": {
                    "url": "https://api.example.com/news.json",
                    "topic": "finance",
                }
            },
            fixture={"input": {}},
            allowed_outputs=("status_code", "url", "json", "text"),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.provider_id == "http"
    assert result.outputs == {
        "status_code": 200,
        "url": "https://api.example.com/news.json",
        "json": {"items": [{"id": "1", "title": "Market update"}]},
    }
    assert client.calls == [
        {
            "url": "https://api.example.com/news.json",
            "headers": {"Accept": "application/json, text/plain;q=0.9"},
            "params": {"topic": "finance"},
        }
    ]


def test_source_fetch_document_transport_allows_absolute_url_under_base() -> None:
    client = FakeClient(
        {
            "document": {
                "id": "doc-1",
                "title": "Public topic",
                "url": "https://source.test/news/public-topic",
                "text": "Public document body.",
                "secret": "must not be persisted",
            }
        }
    )
    transport = SourceFetchDocumentTransport(base_url="https://source.test", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="fetch_public_news",
            connector_action="source.fetch_document",
            input_ref={"with": {"source_url": "https://source.test/news/public-topic"}},
            fixture={"input": {}},
            allowed_outputs=("document",),
            dry_run=False,
        )
    )

    assert result.status == "live-read"
    assert result.outputs == {
        "document": {
            "id": "doc-1",
            "title": "Public topic",
            "url": "https://source.test/news/public-topic",
            "text": "Public document body.",
        }
    }
    assert client.calls[0]["url"] == "https://source.test/news/public-topic"


def test_source_fetch_document_transport_blocks_off_base_absolute_url_without_http() -> None:
    client = FakeClient({"document": {"text": "unused"}})
    transport = SourceFetchDocumentTransport(base_url="https://source.test", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="fetch_public_news",
            connector_action="source.fetch_document",
            input_ref={"with": {"source_url": "https://evil.test/news/public-topic"}},
            fixture={"input": {}},
            allowed_outputs=("document",),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("source.fetch_document URL must stay under APPV24_SOURCE_BASE_URL",)
    assert client.calls == []


def test_gmail_create_draft_transport_posts_encoded_mime_and_slims_outputs() -> None:
    client = FakeClient({"id": "draft-1", "message": {"threadId": "thread-1", "id": "message-1"}})
    transport = GmailCreateDraftTransport(
        token="gmail-token",
        base_url="https://gmail.test/gmail/v1",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={
                "with": {
                    "to": "ops@example.com",
                    "subject": "Incident update",
                    "body": "The workflow found a new incident.",
                    "cc": "lead@example.com",
                    "thread_id": "thread-original",
                }
            },
            fixture={"input": {"recipient": "fallback@example.com", "body": "fallback body"}},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=False,
        )
    )

    assert result.status == "live-draft"
    assert result.provider_id == "gmail"
    assert result.issues == ()
    assert result.outputs == {"draft_id": "draft-1", "thread_id": "thread-1"}
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["url"] == "https://gmail.test/gmail/v1/users/me/drafts"
    assert call["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer gmail-token",
        "Content-Type": "application/json",
    }
    assert sorted(call["json"]) == ["message"]
    encoded = call["json"]["message"]["raw"]
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(encoded.encode()))
    assert message["To"] == "ops@example.com"
    assert message["Cc"] == "lead@example.com"
    assert message["Subject"] == "Incident update"
    assert "The workflow found a new incident." in message.get_content()
    assert "threadId" in call["json"]["message"]
    assert call["json"]["message"]["threadId"] == "thread-original"
    assert "gmail-token" not in repr(result.outputs)


def test_gmail_create_draft_transport_resolves_input_refs_from_with_fields() -> None:
    client = FakeClient({"id": "draft-1", "message": {"threadId": "thread-1"}})
    transport = GmailCreateDraftTransport(
        token="gmail-token",
        base_url="https://gmail.test/gmail/v1",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={
                "with": {
                    "to": {"inputRef": "customer_email"},
                    "subject": {"inputRef": "draft_subject"},
                    "body": {"inputRef": "draft_body"},
                }
            },
            fixture={
                "input": {
                    "customer_email": "customer@example.com",
                    "draft_subject": "Account update",
                    "draft_body": "Your ticket is ready for review.",
                    "to": "fallback@example.com",
                }
            },
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=False,
        )
    )

    assert result.status == "live-draft"
    assert result.outputs == {"draft_id": "draft-1", "thread_id": "thread-1"}
    encoded = client.post_calls[0]["json"]["message"]["raw"]
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(encoded.encode()))
    assert message["To"] == "customer@example.com"
    assert message["Subject"] == "Account update"
    assert "Your ticket is ready for review." in message.get_content()


def test_gmail_create_draft_transport_blocks_missing_required_fields_without_http() -> None:
    client = FakeClient({"id": "draft-1"})
    transport = GmailCreateDraftTransport(
        token="gmail-token",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={"with": {"subject": "Missing body"}},
            fixture={"input": {"recipient": "ops@example.com"}},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("gmail.create_draft requires recipient, subject, and body",)
    assert client.post_calls == []


def test_gmail_create_draft_transport_refreshes_token_before_draft_post() -> None:
    client = FakeClient(
        {},
        post_payloads=[
            {"access_token": "refreshed-token", "expires_in": 3600},
            {"id": "draft-1", "message": {"threadId": "thread-1"}},
        ],
    )
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
            "APPV24_GMAIL_CLIENT_SECRET": "client-secret",
            "APPV24_GOOGLE_OAUTH_TOKEN_URL": "https://oauth.test/token",
            "APPV24_GMAIL_API_BASE_URL": "https://gmail.test/gmail/v1",
        },
        client_factory=lambda: client,
    )

    result = transports["gmail.create_draft"].invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={
                "with": {
                    "to": "ops@example.com",
                    "subject": "Incident update",
                    "body": "The workflow found a new incident.",
                }
            },
            fixture={"input": {}},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=False,
        )
    )

    assert result.status == "live-draft"
    assert result.outputs == {"draft_id": "draft-1", "thread_id": "thread-1"}
    assert len(client.post_calls) == 2
    token_call = client.post_calls[0]
    assert token_call["url"] == "https://oauth.test/token"
    assert token_call["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert token_call["data"] == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
        "grant_type": "refresh_token",
    }
    draft_call = client.post_calls[1]
    assert draft_call["url"] == "https://gmail.test/gmail/v1/users/me/drafts"
    assert draft_call["headers"]["Authorization"] == "Bearer refreshed-token"
    assert "refresh-token" not in repr(result.outputs)
    assert "client-secret" not in repr(result.outputs)
    assert "refreshed-token" not in repr(result.outputs)
    assert "refresh-token" not in repr(result.issues)
    assert "client-secret" not in repr(result.issues)
    assert "refreshed-token" not in repr(result.issues)


def test_gmail_create_draft_transport_blocks_malformed_refresh_response_before_draft_post() -> None:
    client = FakeClient({}, post_payloads=[{"expires_in": 3600}])
    transports = build_live_draft_transports_from_env(
        {
            "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED": "true",
            "APPV24_GMAIL_REFRESH_TOKEN": "refresh-token",
            "APPV24_GMAIL_CLIENT_ID": "client-id",
            "APPV24_GMAIL_CLIENT_SECRET": "client-secret",
        },
        client_factory=lambda: client,
    )

    result = transports["gmail.create_draft"].invoke(
        BrokerRequest(
            step_id="create_gmail_draft",
            connector_action="gmail.create_draft",
            input_ref={
                "with": {
                    "to": "ops@example.com",
                    "subject": "Incident update",
                    "body": "The workflow found a new incident.",
                }
            },
            fixture={"input": {}},
            allowed_outputs=("draft_id", "thread_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("gmail token refresh returned malformed response",)
    assert len(client.post_calls) == 1
    assert "refresh-token" not in repr(result.issues)
    assert "client-secret" not in repr(result.issues)


def test_telegram_send_message_transport_posts_json_and_slims_outputs() -> None:
    client = FakeClient({"ok": True, "result": {"message_id": 42, "chat": {"id": "admin-chat"}}})
    transport = TelegramSendMessageTransport(
        token="telegram-token",
        base_url="https://telegram.test",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={
                "with": {
                    "admin_chat_id": "admin-chat",
                    "message_text": "Ticket created.",
                    "disable_notification": True,
                }
            },
            fixture={"input": {"chat_id": "fallback-chat", "text": "fallback"}},
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "live-production"
    assert result.provider_id == "telegram"
    assert result.issues == ()
    assert result.outputs == {"message_id": "42", "chat_id": "admin-chat"}
    assert client.post_calls == [
        {
            "url": "https://telegram.test/bottelegram-token/sendMessage",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            "json": {
                "chat_id": "admin-chat",
                "text": "Ticket created.",
                "disable_notification": True,
            },
            "data": {},
        }
    ]
    assert "telegram-token" not in repr(result.outputs)
    assert "telegram-token" not in repr(result.issues)


def test_telegram_send_message_transport_resolves_input_refs_from_with_fields() -> None:
    client = FakeClient({"ok": True, "result": {"message_id": 43, "chat": {"id": "admin-chat"}}})
    transport = TelegramSendMessageTransport(
        token="telegram-token",
        base_url="https://telegram.test",
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={
                "with": {
                    "chat_id": {"inputRef": "target_chat"},
                    "text": {"inputRef": "alert_text"},
                    "disable_notification": {"inputRef": "silent"},
                }
            },
            fixture={
                "input": {
                    "target_chat": "admin-chat",
                    "alert_text": "Ticket created from workflow.",
                    "silent": True,
                    "chat_id": "fallback-chat",
                    "text": "fallback",
                }
            },
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "live-production"
    assert result.outputs == {"message_id": "43", "chat_id": "admin-chat"}
    assert client.post_calls[0]["json"] == {
        "chat_id": "admin-chat",
        "text": "Ticket created from workflow.",
        "disable_notification": True,
    }


def test_telegram_send_message_transport_blocks_missing_fields_without_http() -> None:
    client = FakeClient({"ok": True})
    transport = TelegramSendMessageTransport(token="telegram-token", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"text": "Missing chat"}},
            fixture={"input": {}},
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.send_message requires chat_id and text",)
    assert client.post_calls == []


def test_telegram_send_message_transport_blocks_unapproved_chat_before_http() -> None:
    client = FakeClient({"ok": True})
    transport = TelegramSendMessageTransport(
        token="telegram-token",
        allowed_chat_ids=("admin-chat",),
        client_factory=lambda: client,
    )

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"chat_id": "customer-chat", "text": "Ticket created."}},
            fixture={"input": {}},
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.send_message chat_id is not allowlisted: customer-chat",)
    assert client.post_calls == []


def test_telegram_send_message_transport_sanitizes_http_failure() -> None:
    client = FakeClient({"ok": False}, post_responses=[FakeResponse({"ok": False}, status_code=403)])
    transport = TelegramSendMessageTransport(token="telegram-token", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"chat_id": "admin-chat", "text": "Ticket created."}},
            fixture={"input": {}},
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.send_message returned HTTP 403",)
    assert "telegram-token" not in repr(result.issues)


def test_telegram_send_message_transport_blocks_malformed_response() -> None:
    client = FakeClient({"ok": True, "result": {"chat": {"id": "admin-chat"}}})
    transport = TelegramSendMessageTransport(token="telegram-token", client_factory=lambda: client)

    result = transport.invoke(
        BrokerRequest(
            step_id="notify_admin",
            connector_action="telegram.send_message",
            input_ref={"with": {"chat_id": "admin-chat", "text": "Ticket created."}},
            fixture={"input": {}},
            allowed_outputs=("message_id", "chat_id"),
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.outputs == {}
    assert result.issues == ("telegram.send_message returned malformed response",)


@dataclass
class FakeResponse:
    payload: Any
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("HTTP failure", request=request, response=response)

    def json(self) -> Any:
        return self.payload


class FakeClient:
    def __init__(
        self,
        payload: Any,
        *,
        post_payloads: list[Any] | None = None,
        post_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.payload = payload
        self.post_payloads = list(post_payloads or [])
        self.post_responses = list(post_responses or [])
        self.calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> FakeResponse:
        self.calls.append({"url": url, "headers": dict(headers), "params": dict(params)})
        return FakeResponse(self.payload)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.post_calls.append({"url": url, "headers": dict(headers), "json": dict(json or {}), "data": dict(data or {})})
        if self.post_responses:
            return self.post_responses.pop(0)
        payload = self.post_payloads.pop(0) if self.post_payloads else self.payload
        return FakeResponse(payload)

    def close(self) -> None:
        pass
