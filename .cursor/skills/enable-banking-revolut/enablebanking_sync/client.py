"""Enable Banking client for Revolut AIS (account information) sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import jwt
import requests


API_ORIGIN = "https://api.enablebanking.com"


class EnableBankingError(RuntimeError):
    """Raised when Enable Banking returns a non-success response."""


@dataclass(frozen=True)
class EnableBankingConfig:
    application_id: str
    private_key_pem: str
    redirect_url: str | None = None
    session_id: str | None = None
    aspsp_name: str = "Revolut"
    aspsp_country: str = "LT"
    psu_type: str = "personal"


def make_jwt(config: EnableBankingConfig, lifetime_seconds: int = 3600) -> str:
    issued_at = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
    }
    return jwt.encode(
        payload,
        config.private_key_pem,
        algorithm="RS256",
        headers={"kid": config.application_id},
    )


def auth_header(config: EnableBankingConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_jwt(config)}"}


def _request(
    config: EnableBankingConfig,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    url = f"{API_ORIGIN}{path}"
    response = requests.request(
        method,
        url,
        headers={**auth_header(config), "Content-Type": "application/json"},
        json=json_body,
        params=params,
        timeout=60,
    )
    if response.status_code >= 400:
        raise EnableBankingError(
            f"{method} {path} failed ({response.status_code}): {response.text}"
        )
    if not response.content:
        return None
    return response.json()


def get_application(config: EnableBankingConfig) -> dict[str, Any]:
    return _request(config, "GET", "/application")


def list_aspsps(config: EnableBankingConfig, country: str) -> list[dict[str, Any]]:
    payload = _request(config, "GET", "/aspsps", params={"country": country})
    return payload.get("aspsps", [])


def start_authorization(
    config: EnableBankingConfig,
    *,
    valid_days: int = 90,
    redirect_url: str | None = None,
) -> dict[str, Any]:
    application = get_application(config)
    registered_redirects = application.get("redirect_urls") or []
    chosen_redirect = redirect_url or config.redirect_url
    if not chosen_redirect:
        if not registered_redirects:
            raise EnableBankingError(
                "No redirect URL configured. Set ENABLE_BANKING_REDIRECT_URL "
                "to one of the URLs registered on the Enable Banking application."
            )
        chosen_redirect = registered_redirects[0]
    if registered_redirects and chosen_redirect not in registered_redirects:
        raise EnableBankingError(
            f"Redirect URL {chosen_redirect} is not registered. "
            f"Registered: {registered_redirects}"
        )
    valid_until = datetime.now(timezone.utc) + timedelta(days=valid_days)
    body = {
        "access": {
            "balances": True,
            "transactions": True,
            "valid_until": valid_until.isoformat(),
        },
        "aspsp": {"name": config.aspsp_name, "country": config.aspsp_country},
        "state": str(uuid4()),
        "redirect_url": chosen_redirect,
        "psu_type": config.psu_type,
    }
    payload = _request(config, "POST", "/auth", json_body=body)
    payload["redirect_url_used"] = chosen_redirect
    return payload


def create_session(config: EnableBankingConfig, code: str) -> dict[str, Any]:
    return _request(config, "POST", "/sessions", json_body={"code": code})


def get_session(config: EnableBankingConfig, session_id: str) -> dict[str, Any]:
    return _request(config, "GET", f"/sessions/{session_id}")


def get_account_balances(config: EnableBankingConfig, account_uid: str) -> dict[str, Any]:
    return _request(config, "GET", f"/accounts/{account_uid}/balances")


def get_account_transactions(
    config: EnableBankingConfig,
    account_uid: str,
    *,
    date_from: str,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    continuation_key: str | None = None
    while True:
        params: dict[str, Any] = {"date_from": date_from}
        if date_to:
            params["date_to"] = date_to
        if continuation_key:
            params["continuation_key"] = continuation_key
        payload = _request(
            config,
            "GET",
            f"/accounts/{account_uid}/transactions",
            params=params,
        )
        transactions.extend(payload.get("transactions") or [])
        continuation_key = payload.get("continuation_key")
        if not continuation_key:
            break
    return transactions


def code_from_redirect_url(redirected_url: str) -> str:
    parsed = urlparse(redirected_url)
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    values = query.get("code") or fragment.get("code") or []
    if not values:
        raise EnableBankingError(
            "No `code` query parameter found in the redirected URL."
        )
    return values[0]
