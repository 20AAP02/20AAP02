"""Poll webhook.site for Enable Banking AIS redirect codes."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from .client import EnableBankingError, code_from_redirect_url


WEBHOOK_ORIGIN = "https://webhook.site"


def webhook_token_from_url(redirect_url: str) -> str:
    parsed = urlparse(redirect_url)
    parts = [item for item in parsed.path.split("/") if item]
    if not parts:
        raise EnableBankingError(f"Cannot derive webhook.site token from {redirect_url}")
    return parts[-1]


def _code_from_request(item: dict[str, Any]) -> str | None:
    query = item.get("query") or {}
    if isinstance(query, dict):
        raw = query.get("code")
        if isinstance(raw, list) and raw:
            return str(raw[0])
        if isinstance(raw, str) and raw:
            return raw
    url = item.get("url")
    if isinstance(url, str) and "code=" in url:
        try:
            return code_from_redirect_url(url)
        except EnableBankingError:
            parsed = urlparse(url)
            values = parse_qs(parsed.query).get("code") or []
            if values:
                return values[0]
    content = item.get("content")
    if isinstance(content, str) and "code=" in content:
        try:
            return code_from_redirect_url(content)
        except EnableBankingError:
            return None
    return None


def list_webhook_requests(token: str, *, per_page: int = 50) -> list[dict[str, Any]]:
    response = requests.get(
        f"{WEBHOOK_ORIGIN}/token/{token}/requests",
        params={"sorting": "newest", "per_page": per_page},
        timeout=30,
    )
    if response.status_code >= 400:
        raise EnableBankingError(
            f"webhook.site poll failed ({response.status_code}): {response.text}"
        )
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    return list(data or [])


def find_auth_code(
    token: str,
    *,
    seen_uuids: set[str] | None = None,
) -> tuple[str, str] | None:
    """Return (code, request_uuid) for the newest unused AIS redirect."""
    ignored = seen_uuids or set()
    for item in list_webhook_requests(token):
        if item.get("type") == "email":
            continue
        uuid = str(item.get("uuid") or "")
        if uuid and uuid in ignored:
            continue
        code = _code_from_request(item)
        if code:
            return code, uuid
    return None


def wait_for_auth_code(
    token: str,
    *,
    timeout_seconds: int,
    poll_seconds: int = 10,
    seen_uuids: set[str] | None = None,
    on_poll: Any | None = None,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    ignored = set(seen_uuids or set())
    while time.monotonic() < deadline:
        found = find_auth_code(token, seen_uuids=ignored)
        if found:
            return found[0]
        remaining = int(deadline - time.monotonic())
        if on_poll:
            on_poll(remaining)
        time.sleep(min(poll_seconds, max(1, remaining)))
    raise EnableBankingError(
        "Timed out waiting for the Enable Banking redirect `code` on webhook.site. "
        "Complete Revolut SCA so the browser hits the registered redirect URL."
    )
