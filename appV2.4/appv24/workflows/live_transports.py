"""Env-gated live workflow transports."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from appv24.workflows.broker import BrokerRequest, BrokerResult
from appv24.workflows.http_safety import validate_http_fetch_url
from appv24.workflows.live_broker import LiveWorkflowTransport


LIVE_READ_ENABLED_ENV = "APPV24_WORKFLOW_LIVE_READ_ENABLED"
LIVE_DRAFT_ENABLED_ENV = "APPV24_WORKFLOW_LIVE_DRAFT_ENABLED"
LIVE_PRODUCTION_ENABLED_ENV = "APPV24_WORKFLOW_LIVE_PRODUCTION_ENABLED"
GITHUB_TOKEN_ENV = "APPV24_GITHUB_TOKEN"
GMAIL_ACCESS_TOKEN_ENV = "APPV24_GMAIL_ACCESS_TOKEN"
GMAIL_REFRESH_TOKEN_ENV = "APPV24_GMAIL_REFRESH_TOKEN"
GMAIL_CLIENT_ID_ENV = "APPV24_GMAIL_CLIENT_ID"
GMAIL_CLIENT_SECRET_ENV = "APPV24_GMAIL_CLIENT_SECRET"
TELEGRAM_BOT_TOKEN_ENV = "APPV24_TELEGRAM_BOT_TOKEN"
SOURCE_BASE_URL_ENV = "APPV24_SOURCE_BASE_URL"
GITHUB_API_BASE_URL_ENV = "APPV24_GITHUB_API_BASE_URL"
GMAIL_API_BASE_URL_ENV = "APPV24_GMAIL_API_BASE_URL"
GOOGLE_OAUTH_TOKEN_URL_ENV = "APPV24_GOOGLE_OAUTH_TOKEN_URL"
TELEGRAM_API_BASE_URL_ENV = "APPV24_TELEGRAM_API_BASE_URL"
HTTP_FETCH_ALLOWED_HOSTS_ENV = "APPV24_HTTP_FETCH_ALLOWED_HOSTS"
HTTP_FETCH_MAX_RESPONSE_BYTES_ENV = "APPV24_HTTP_FETCH_MAX_RESPONSE_BYTES"
HTTP_FETCH_TIMEOUT_SECONDS_ENV = "APPV24_HTTP_FETCH_TIMEOUT_SECONDS"
HTTP_FETCH_FOLLOW_REDIRECTS_ENV = "APPV24_HTTP_FETCH_FOLLOW_REDIRECTS"
TELEGRAM_ALLOWED_CHAT_IDS_ENV = "APPV24_TELEGRAM_ALLOWED_CHAT_IDS"
GITHUB_API_VERSION = "2026-03-10"
DEFAULT_GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
DEFAULT_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class _HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> Any:
        raise NotImplementedError

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def build_live_read_transports_from_env(
    env: Mapping[str, str],
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> dict[str, LiveWorkflowTransport]:
    """Build live-read transports from explicit operator environment settings."""

    if not _env_enabled(env.get(LIVE_READ_ENABLED_ENV)):
        return {}
    github_token = env.get(GITHUB_TOKEN_ENV, "").strip()
    transports: dict[str, LiveWorkflowTransport] = {}
    if github_token:
        base_url = env.get(GITHUB_API_BASE_URL_ENV, DEFAULT_GITHUB_API_BASE_URL).strip() or DEFAULT_GITHUB_API_BASE_URL
        transports["github.list_dependabot_alerts"] = GitHubDependabotAlertsTransport(
            token=github_token,
            base_url=base_url,
            client_factory=client_factory,
        )
    source_base_url = env.get(SOURCE_BASE_URL_ENV, "").strip()
    if source_base_url:
        transports["source.fetch_document"] = SourceFetchDocumentTransport(
            base_url=source_base_url,
            client_factory=client_factory,
        )
        transports["source.fetch_items"] = SourceFetchItemsTransport(
            base_url=source_base_url,
            client_factory=client_factory,
        )
    http_allowed_hosts = _split_csv(env.get(HTTP_FETCH_ALLOWED_HOSTS_ENV, ""))
    if http_allowed_hosts:
        transports["http.fetch"] = HttpFetchTransport(
            allowed_hosts=http_allowed_hosts,
            timeout_seconds=_float_env(env.get(HTTP_FETCH_TIMEOUT_SECONDS_ENV), default=15.0),
            max_response_bytes=_int_env(env.get(HTTP_FETCH_MAX_RESPONSE_BYTES_ENV), default=65536),
            follow_redirects=_env_enabled(env.get(HTTP_FETCH_FOLLOW_REDIRECTS_ENV)),
            client_factory=client_factory,
        )
    return transports


def build_live_draft_transports_from_env(
    env: Mapping[str, str],
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> dict[str, LiveWorkflowTransport]:
    """Build live-draft transports from explicit operator environment settings."""

    if not _env_enabled(env.get(LIVE_DRAFT_ENABLED_ENV)):
        return {}
    gmail_token = env.get(GMAIL_ACCESS_TOKEN_ENV, "").strip()
    token_provider: Callable[[], str] | None
    if gmail_token:
        token_provider = StaticBearerTokenProvider(gmail_token)
    else:
        refresh_token = env.get(GMAIL_REFRESH_TOKEN_ENV, "").strip()
        client_id = env.get(GMAIL_CLIENT_ID_ENV, "").strip()
        client_secret = env.get(GMAIL_CLIENT_SECRET_ENV, "").strip()
        if not refresh_token or not client_id or not client_secret:
            return {}
        token_url = env.get(GOOGLE_OAUTH_TOKEN_URL_ENV, DEFAULT_GOOGLE_OAUTH_TOKEN_URL).strip() or DEFAULT_GOOGLE_OAUTH_TOKEN_URL
        token_provider = GoogleOAuthRefreshTokenProvider(
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            client_factory=client_factory,
        )
    if token_provider is None:
        return {}
    base_url = env.get(GMAIL_API_BASE_URL_ENV, DEFAULT_GMAIL_API_BASE_URL).strip() or DEFAULT_GMAIL_API_BASE_URL
    return {
        "gmail.create_draft": GmailCreateDraftTransport(
            token_provider=token_provider,
            base_url=base_url,
            client_factory=client_factory,
        )
    }


def build_live_production_transports_from_env(
    env: Mapping[str, str],
    *,
    client_factory: Callable[[], _HttpClient] | None = None,
) -> dict[str, LiveWorkflowTransport]:
    """Build live-production transports from explicit operator environment settings."""

    if not _env_enabled(env.get(LIVE_PRODUCTION_ENABLED_ENV)):
        return {}
    telegram_token = env.get(TELEGRAM_BOT_TOKEN_ENV, "").strip()
    if not telegram_token:
        return {}
    base_url = env.get(TELEGRAM_API_BASE_URL_ENV, DEFAULT_TELEGRAM_API_BASE_URL).strip() or DEFAULT_TELEGRAM_API_BASE_URL
    allowed_chat_ids = _split_csv(env.get(TELEGRAM_ALLOWED_CHAT_IDS_ENV, ""))
    return {
        "telegram.send_message": TelegramSendMessageTransport(
            token=telegram_token,
            base_url=base_url,
            allowed_chat_ids=allowed_chat_ids,
            client_factory=client_factory,
        )
    }


class GitHubDependabotAlertsTransport:
    """Read-only GitHub Dependabot alerts transport."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = DEFAULT_GITHUB_API_BASE_URL,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        repo = _repository_for_request(request)
        if repo is None:
            return _blocked("github.list_dependabot_alerts requires repo as owner/repo")
        owner, name = repo
        client = self._client_factory()
        try:
            response = client.get(
                f"{self._base_url}/repos/{owner}/{name}/dependabot/alerts",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
                params=_query_params(request),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"github.list_dependabot_alerts returned HTTP {status_code}")
        except httpx.RequestError as error:
            return _blocked(f"github.list_dependabot_alerts request failed: {type(error).__name__}")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, list):
            return _blocked("github.list_dependabot_alerts returned malformed response")
        alerts = [_slim_alert(alert) for alert in payload if isinstance(alert, Mapping)]
        return BrokerResult(
            status="live-read",
            provider_id="github",
            outputs={"alerts": alerts, "count": len(alerts)},
        )


class GmailTokenError(Exception):
    """Sanitized Gmail token resolution failure."""


class SourceFetchItemsTransport:
    """Read-only generic source transport for item feeds under an operator base URL."""

    def __init__(
        self,
        *,
        base_url: str,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        url, issue = _source_url_for_request(request, self._base_url, action_ref="source.fetch_items")
        if issue is not None:
            return _blocked(issue, provider_id="source")
        client = self._client_factory()
        try:
            response = client.get(
                url,
                headers={"Accept": "application/json"},
                params=_source_query_params(request),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"source.fetch_items returned HTTP {status_code}", provider_id="source")
        except httpx.RequestError as error:
            return _blocked(f"source.fetch_items request failed: {type(error).__name__}", provider_id="source")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        parsed = _source_items_payload(payload)
        if parsed is None:
            return _blocked("source.fetch_items returned malformed response", provider_id="source")
        items, source_count = parsed
        return BrokerResult(
            status="live-read",
            provider_id="source",
            outputs={"items": items, "source_count": source_count},
        )


class SourceFetchDocumentTransport:
    """Read-only generic source transport for public documents under an operator base URL."""

    def __init__(
        self,
        *,
        base_url: str,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        url, issue = _source_url_for_request(request, self._base_url, action_ref="source.fetch_document")
        if issue is not None:
            return _blocked(issue, provider_id="source")
        client = self._client_factory()
        try:
            response = client.get(
                url,
                headers={"Accept": "application/json"},
                params=_source_query_params(request),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"source.fetch_document returned HTTP {status_code}", provider_id="source")
        except httpx.RequestError as error:
            return _blocked(f"source.fetch_document request failed: {type(error).__name__}", provider_id="source")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        document = _source_document_payload(payload)
        if document is None:
            return _blocked("source.fetch_document returned malformed response", provider_id="source")
        return BrokerResult(status="live-read", provider_id="source", outputs={"document": document})


class HttpFetchPayloadError(Exception):
    """Sanitized HTTP response shaping failure."""


class HttpFetchTransport:
    """Read-only HTTP GET transport for operator-allowlisted public URLs."""

    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...],
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 65536,
        follow_redirects: bool = False,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._allowed_hosts = tuple(host.strip().lower() for host in allowed_hosts if host.strip())
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._follow_redirects = follow_redirects
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=timeout_seconds, follow_redirects=follow_redirects, trust_env=False)
        )

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        url = _http_fetch_url_for_request(request)
        if url is None:
            return _blocked("http.fetch requires url", provider_id="http")
        issue = validate_http_fetch_url(url, allowed_hosts=self._allowed_hosts)
        if issue is not None:
            return _blocked(issue, provider_id="http")

        client = self._client_factory()
        try:
            response = client.get(
                url,
                headers={"Accept": "application/json, text/plain;q=0.9"},
                params=_http_query_params(request),
            )
            response.raise_for_status()
            outputs = _http_fetch_outputs(response, fallback_url=url, max_response_bytes=self._max_response_bytes)
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"http.fetch returned HTTP {status_code}", provider_id="http")
        except HttpFetchPayloadError as error:
            return _blocked(str(error), provider_id="http")
        except httpx.RequestError as error:
            return _blocked(f"http.fetch request failed: {type(error).__name__}", provider_id="http")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        return BrokerResult(status="live-read", provider_id="http", outputs=outputs)


class StaticBearerTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def __call__(self) -> str:
        return self._token


class GoogleOAuthRefreshTokenProvider:
    def __init__(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        token_url: str = DEFAULT_GOOGLE_OAUTH_TOKEN_URL,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def __call__(self) -> str:
        client = self._client_factory()
        try:
            response = client.post(
                self._token_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            raise GmailTokenError(f"gmail token refresh returned HTTP {status_code}") from error
        except httpx.RequestError as error:
            raise GmailTokenError(f"gmail token refresh request failed: {type(error).__name__}") from error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, Mapping):
            raise GmailTokenError("gmail token refresh returned malformed response")
        access_token = _string(payload.get("access_token"))
        if access_token is None:
            raise GmailTokenError("gmail token refresh returned malformed response")
        return access_token


class GmailCreateDraftTransport:
    """Draft-only Gmail transport for approved live-draft workflow runs."""

    def __init__(
        self,
        *,
        token: str | None = None,
        token_provider: Callable[[], str] | None = None,
        base_url: str = DEFAULT_GMAIL_API_BASE_URL,
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        if token_provider is None:
            if token is None:
                raise ValueError("GmailCreateDraftTransport requires token or token_provider")
            token_provider = StaticBearerTokenProvider(token)
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        fields = _draft_fields_for_request(request)
        if fields is None:
            return _blocked("gmail.create_draft requires recipient, subject, and body", provider_id="gmail")
        try:
            token = self._token_provider()
        except GmailTokenError as error:
            return _blocked(str(error), provider_id="gmail")

        client = self._client_factory()
        try:
            response = client.post(
                f"{self._base_url}/users/me/drafts",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=_gmail_create_draft_payload(fields),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"gmail.create_draft returned HTTP {status_code}", provider_id="gmail")
        except httpx.RequestError as error:
            return _blocked(f"gmail.create_draft request failed: {type(error).__name__}", provider_id="gmail")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, Mapping):
            return _blocked("gmail.create_draft returned malformed response", provider_id="gmail")
        draft_id = _string(payload.get("id"))
        message = _mapping(payload.get("message"))
        thread_id = _string(message.get("threadId"))
        if draft_id is None:
            return _blocked("gmail.create_draft returned malformed response", provider_id="gmail")
        outputs: dict[str, Any] = {"draft_id": draft_id}
        if thread_id is not None:
            outputs["thread_id"] = thread_id
        return BrokerResult(status="live-draft", provider_id="gmail", outputs=outputs)


class TelegramSendMessageTransport:
    """Production Telegram Bot API transport for approved send-message workflow runs."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = DEFAULT_TELEGRAM_API_BASE_URL,
        allowed_chat_ids: tuple[str, ...] = (),
        client_factory: Callable[[], _HttpClient] | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._allowed_chat_ids = tuple(chat_id.strip() for chat_id in allowed_chat_ids if chat_id.strip())
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))

    def invoke(self, request: BrokerRequest) -> BrokerResult:
        fields = _telegram_message_fields_for_request(request)
        if fields is None:
            return _blocked("telegram.send_message requires chat_id and text", provider_id="telegram")
        chat_id = str(fields["chat_id"])
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            return _blocked(f"telegram.send_message chat_id is not allowlisted: {chat_id}", provider_id="telegram")

        client = self._client_factory()
        try:
            response = client.post(
                f"{self._base_url}/bot{self._token}/sendMessage",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=_telegram_send_message_payload(fields),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code if error.response is not None else "unknown"
            return _blocked(f"telegram.send_message returned HTTP {status_code}", provider_id="telegram")
        except httpx.RequestError as error:
            return _blocked(f"telegram.send_message request failed: {type(error).__name__}", provider_id="telegram")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            return _blocked("telegram.send_message returned unsuccessful response", provider_id="telegram")
        result = _mapping(payload.get("result"))
        message_id = _scalar_string(result.get("message_id"))
        if message_id is None:
            return _blocked("telegram.send_message returned malformed response", provider_id="telegram")
        outputs: dict[str, Any] = {"message_id": message_id}
        chat_id = _scalar_string(_mapping(result.get("chat")).get("id"))
        if chat_id is not None:
            outputs["chat_id"] = chat_id
        return BrokerResult(status="live-production", provider_id="telegram", outputs=outputs)


def _env_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


def _int_env(value: str | None, *, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _float_env(value: str | None, *, default: float) -> float:
    try:
        parsed = float(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _repository_for_request(request: BrokerRequest) -> tuple[str, str] | None:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    repo = (
        _string(_resolve_input_ref(with_map.get("repository"), fixture_input))
        or _string(_resolve_input_ref(with_map.get("repo"), fixture_input))
        or _string(fixture_input.get("repository"))
        or _string(fixture_input.get("repo"))
    )
    if repo is None or "/" not in repo:
        return None
    owner, name = (part.strip() for part in repo.split("/", 1))
    if not owner or not name or "/" in name:
        return None
    return owner, name


def _query_params(request: BrokerRequest) -> dict[str, str]:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    params: dict[str, str] = {}
    for key in ("state", "severity", "ecosystem", "package", "manifest", "scope", "sort", "direction"):
        value = _string(_resolve_input_ref(with_map.get(key), fixture_input))
        if value:
            params[key] = value
    per_page = _per_page(_resolve_input_ref(with_map.get("per_page"), fixture_input))
    if per_page is not None:
        params["per_page"] = str(per_page)
    return params


def _per_page(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    return min(max(parsed, 1), 100)


def _slim_alert(alert: Mapping[str, Any]) -> dict[str, Any]:
    dependency = _mapping(alert.get("dependency"))
    package = _mapping(dependency.get("package"))
    advisory = _mapping(alert.get("security_advisory"))
    return {
        "number": alert.get("number"),
        "state": alert.get("state"),
        "dependency": {
            "ecosystem": package.get("ecosystem"),
            "name": package.get("name"),
            "manifest_path": dependency.get("manifest_path"),
            "scope": dependency.get("scope"),
        },
        "advisory": {
            "ghsa_id": advisory.get("ghsa_id"),
            "cve_id": advisory.get("cve_id"),
            "severity": advisory.get("severity"),
            "summary": advisory.get("summary"),
        },
        "html_url": alert.get("html_url"),
        "created_at": alert.get("created_at"),
        "updated_at": alert.get("updated_at"),
    }


def _source_url_for_request(request: BrokerRequest, base_url: str, *, action_ref: str) -> tuple[str, str | None]:
    if not _valid_base_url(base_url):
        return "", f"{action_ref} requires http(s) APPV24_SOURCE_BASE_URL"
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    raw_value = (
        _resolve_input_ref(with_map.get("source_url"), fixture_input)
        or _resolve_input_ref(with_map.get("url"), fixture_input)
        or _resolve_input_ref(with_map.get("path"), fixture_input)
        or _resolve_input_ref(with_map.get("endpoint"), fixture_input)
        or fixture_input.get("source_url")
        or fixture_input.get("url")
        or fixture_input.get("path")
        or fixture_input.get("endpoint")
    )
    raw_url = _string(raw_value)
    if raw_url is None:
        return base_url.rstrip("/"), None
    resolved = _resolve_under_base_url(base_url, raw_url)
    if resolved is None:
        return "", f"{action_ref} URL must stay under APPV24_SOURCE_BASE_URL"
    return resolved, None


def _valid_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_under_base_url(base_url: str, value: str) -> str | None:
    base = urlparse(base_url)
    candidate = urlparse(value)
    if candidate.scheme or candidate.netloc:
        if candidate.scheme not in {"http", "https"}:
            return None
        if candidate.scheme.lower() != base.scheme.lower() or candidate.netloc.lower() != base.netloc.lower():
            return None
        if not _path_under_base(base.path, candidate.path):
            return None
        return value
    path_parts = [part for part in candidate.path.split("/") if part]
    if any(part == ".." for part in path_parts):
        return None
    return urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))


def _path_under_base(base_path: str, candidate_path: str) -> bool:
    normalized_base = (base_path or "/").rstrip("/")
    normalized_candidate = (candidate_path or "/").rstrip("/")
    if normalized_base in {"", "/"}:
        return True
    return normalized_candidate == normalized_base or normalized_candidate.startswith(normalized_base + "/")


def _source_query_params(request: BrokerRequest) -> dict[str, str]:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    params: dict[str, str] = {}
    for key in sorted(with_map):
        if key in {"source_url", "url", "path", "endpoint"}:
            continue
        value = _resolve_input_ref(with_map.get(key), fixture_input)
        rendered = _query_param_value(value)
        if rendered is not None:
            params[key] = rendered
    return params


def _http_fetch_url_for_request(request: BrokerRequest) -> str | None:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    return (
        _string(_resolve_input_ref(with_map.get("url"), fixture_input))
        or _string(_resolve_input_ref(with_map.get("source_url"), fixture_input))
        or _string(fixture_input.get("url"))
        or _string(fixture_input.get("source_url"))
    )


def _http_query_params(request: BrokerRequest) -> dict[str, str]:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    params: dict[str, str] = {}
    for key in sorted(with_map):
        if key in {"url", "source_url"}:
            continue
        value = _resolve_input_ref(with_map.get(key), fixture_input)
        rendered = _query_param_value(value)
        if rendered is not None:
            params[key] = rendered
    return params


def _http_fetch_outputs(response: Any, *, fallback_url: str, max_response_bytes: int) -> dict[str, Any]:
    content = getattr(response, "content", None)
    if isinstance(content, bytes) and len(content) > max_response_bytes:
        raise HttpFetchPayloadError("http.fetch response exceeded max response bytes")
    status_code = getattr(response, "status_code", 200)
    outputs: dict[str, Any] = {
        "status_code": status_code if isinstance(status_code, int) else 200,
        "url": str(getattr(response, "url", fallback_url) or fallback_url),
    }
    try:
        payload = response.json()
    except ValueError:
        text = getattr(response, "text", "")
        if not isinstance(text, str):
            text = ""
        if len(text.encode("utf-8")) > max_response_bytes:
            raise HttpFetchPayloadError("http.fetch response exceeded max response bytes")
        outputs["text"] = text
        return outputs
    outputs["json"] = payload
    return outputs


def _query_param_value(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        parts = tuple(_query_param_value(item) for item in value)
        values = tuple(part for part in parts if part)
        return ",".join(values) if values else None
    return None


def _source_items_payload(payload: Any) -> tuple[list[dict[str, Any]], int] | None:
    if isinstance(payload, list):
        raw_items = payload
        source_count = len(raw_items)
    elif isinstance(payload, Mapping):
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return None
        raw_count = payload.get("source_count", payload.get("count"))
        source_count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else len(raw_items)
    else:
        return None
    items = [_slim_source_item(item) for item in raw_items if isinstance(item, Mapping)]
    return items, source_count


def _slim_source_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return _slim_mapping(
        item,
        (
            "id",
            "title",
            "url",
            "published_at",
            "summary",
            "source",
            "ticker",
            "tickers",
        ),
    )


def _source_document_payload(payload: Any) -> dict[str, Any] | None:
    raw_document: Any
    if isinstance(payload, Mapping) and "document" in payload:
        raw_document = payload.get("document")
    else:
        raw_document = payload
    if isinstance(raw_document, str):
        return {"text": raw_document}
    if not isinstance(raw_document, Mapping):
        return None
    document = _slim_mapping(raw_document, ("id", "title", "url", "published_at", "source", "text", "metadata"))
    return document if document else None


def _slim_mapping(value: Mapping[str, Any], allowed_keys: tuple[str, ...]) -> dict[str, Any]:
    slimmed: dict[str, Any] = {}
    for key in allowed_keys:
        item = value.get(key)
        if isinstance(item, str | int | float) and not isinstance(item, bool):
            slimmed[key] = item
        elif isinstance(item, list) and all(isinstance(part, str | int | float) and not isinstance(part, bool) for part in item):
            slimmed[key] = list(item)
        elif key == "metadata" and isinstance(item, Mapping):
            slimmed[key] = {
                str(meta_key): meta_value
                for meta_key, meta_value in item.items()
                if isinstance(meta_key, str) and isinstance(meta_value, str | int | float) and not isinstance(meta_value, bool)
            }
    return slimmed


def _draft_fields_for_request(request: BrokerRequest) -> dict[str, str] | None:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    recipient = _first_string(with_map, fixture_input, ("to", "recipient"))
    subject = _first_string(with_map, fixture_input, ("subject",))
    body = _first_string(with_map, fixture_input, ("body",))
    if recipient is None or subject is None or body is None:
        return None
    fields = {"to": recipient, "subject": subject, "body": body}
    for key in ("from", "cc", "bcc", "thread_id"):
        value = _first_string(with_map, fixture_input, (key,))
        if value is not None:
            fields[key] = value
    return fields


def _gmail_create_draft_payload(fields: Mapping[str, str]) -> dict[str, Any]:
    message = EmailMessage()
    message["To"] = fields["to"]
    sender = fields.get("from")
    if sender:
        message["From"] = sender
    if fields.get("cc"):
        message["Cc"] = fields["cc"]
    if fields.get("bcc"):
        message["Bcc"] = fields["bcc"]
    message["Subject"] = fields["subject"]
    message.set_content(fields["body"])
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    payload: dict[str, Any] = {"message": {"raw": encoded}}
    thread_id = fields.get("thread_id")
    if thread_id:
        payload["message"]["threadId"] = thread_id
    return payload


def _telegram_message_fields_for_request(request: BrokerRequest) -> dict[str, Any] | None:
    with_map = _mapping(request.input_ref.get("with"))
    fixture_input = _mapping(request.fixture.get("input"))
    chat_id = _first_string(with_map, fixture_input, ("chat_id", "admin_chat_id", "recipient"))
    text = _first_string(with_map, fixture_input, ("text", "message_text", "body"))
    if chat_id is None or text is None:
        return None
    fields: dict[str, Any] = {"chat_id": chat_id, "text": text}
    parse_mode = _first_string(with_map, fixture_input, ("parse_mode",))
    if parse_mode is not None:
        fields["parse_mode"] = parse_mode
    for key in ("disable_notification", "protect_content"):
        value = _first_bool(with_map, fixture_input, key)
        if value is not None:
            fields[key] = value
    return fields


def _telegram_send_message_payload(fields: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "chat_id": fields["chat_id"],
        "text": fields["text"],
    }
    for key in ("parse_mode", "disable_notification", "protect_content"):
        if key in fields:
            payload[key] = fields[key]
    return payload


def _first_string(primary: Mapping[str, Any], fallback: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for source, fixture_input in ((primary, fallback), (fallback, fallback)):
        for key in keys:
            value = _string(_resolve_input_ref(source.get(key), fixture_input))
            if value is not None:
                return value
    return None


def _first_bool(primary: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> bool | None:
    for source, fixture_input in ((primary, fallback), (fallback, fallback)):
        value = _resolve_input_ref(source.get(key), fixture_input)
        if isinstance(value, bool):
            return value
    return None


def _resolve_input_ref(value: object, fixture_input: Mapping[str, Any]) -> object:
    if isinstance(value, Mapping):
        input_ref = value.get("inputRef")
        if isinstance(input_ref, str):
            return fixture_input.get(input_ref)
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _scalar_string(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    return _string(value)


def _blocked(issue: str, *, provider_id: str = "github") -> BrokerResult:
    return BrokerResult(status="blocked", provider_id=provider_id, outputs={}, issues=(issue,))
