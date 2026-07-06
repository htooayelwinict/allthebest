"""HTTP safety helpers for workflow live-read transports."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SafeHttpRequest:
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout_seconds: float
    follow_redirects: bool
    max_response_bytes: int


def validate_http_fetch_url(url: str, *, allowed_hosts: tuple[str, ...]) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "http.fetch URL must be absolute http(s)"
    host = (parsed.hostname or "").lower()
    normalized_allowed = {item.lower() for item in allowed_hosts}
    if host not in normalized_allowed:
        return f"http.fetch URL host is not allowlisted: {host}"
    return None
