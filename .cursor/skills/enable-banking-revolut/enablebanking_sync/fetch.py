"""Build JWT-backed Enable Banking transaction dumps as JSON."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .client import (
    EnableBankingConfig,
    EnableBankingError,
    create_session,
    get_account_transactions,
    get_application,
    get_session,
    jwt_metadata,
    start_authorization,
)
from .home import (
    clear_pending_auth,
    load_pending_auth,
    save_pending_auth,
    save_session,
    save_status,
)
from .webhook import wait_for_auth_code, webhook_token_from_url


StatusCallback = Callable[[dict[str, Any]], None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pending_auth_is_fresh(pending: dict[str, Any], max_age_seconds: int = 1500) -> bool:
    created_at = pending.get("created_at")
    if not pending.get("url") or not created_at:
        return False
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() < max_age_seconds


def redact_iban(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.replace(" ", "")
    if len(compact) <= 8:
        return "…"
    return f"{compact[:4]}…{compact[-4:]}"


def redact_account(account: dict[str, Any]) -> dict[str, Any]:
    copy = dict(account)
    account_id = copy.get("account_id")
    if isinstance(account_id, dict):
        redacted = dict(account_id)
        if redacted.get("iban"):
            redacted["iban"] = redact_iban(str(redacted["iban"]))
        copy["account_id"] = redacted
    return copy


def fetch_transactions_json(
    config: EnableBankingConfig,
    *,
    days: int = 30,
    session_id: str | None = None,
) -> dict[str, Any]:
    resolved_session = session_id or config.session_id
    if not resolved_session:
        raise EnableBankingError("No session id. Complete Revolut SCA first.")
    application = get_application(config)
    session = get_session(config, resolved_session)
    date_to = date.today()
    date_from = date_to - timedelta(days=days)
    accounts_out: list[dict[str, Any]] = []
    for account in session.get("accounts") or []:
        uid = str(account.get("uid") or "")
        transactions = get_account_transactions(
            config,
            uid,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
        accounts_out.append(
            {
                "uid": uid,
                "name": account.get("name") or account.get("product"),
                "currency": account.get("currency"),
                "iban": redact_iban(((account.get("account_id") or {}).get("iban"))),
                "transaction_count": len(transactions),
                "transactions": transactions,
            }
        )
    return {
        "ok": True,
        "jwt": jwt_metadata(config),
        "application": {
            "kid": application.get("kid") or config.application_id,
            "name": application.get("name"),
            "environment": application.get("environment"),
            "active": application.get("active"),
        },
        "session_id": resolved_session,
        "window": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
        "account_count": len(accounts_out),
        "accounts": accounts_out,
    }


def _emit(on_status: StatusCallback | None, payload: dict[str, Any]) -> None:
    save_status({**payload, "updated_at": _now_iso()})
    if on_status:
        on_status(payload)


def wait_until_application_active(
    config: EnableBankingConfig,
    *,
    timeout_seconds: int,
    poll_seconds: int = 15,
    linking_url: str | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        application = get_application(config)
        if application.get("active"):
            _emit(
                on_status,
                {
                    "state": "application_active",
                    "linking_url": linking_url,
                },
            )
            return application
        remaining = int(deadline - time.monotonic())
        _emit(
            on_status,
            {
                "state": "waiting_for_account_linking",
                "active": False,
                "seconds_remaining": max(0, remaining),
                "linking_url": linking_url,
                "user_action": (
                    "Open the linking_url on a phone with the Revolut app and "
                    "share the accounts that should sync."
                ),
            },
        )
        if remaining <= 0:
            raise EnableBankingError(
                "Timed out waiting for Enable Banking application activation. "
                "Complete Revolut account linking first."
            )
        time.sleep(min(poll_seconds, max(1, remaining)))


def ensure_ais_session(
    config: EnableBankingConfig,
    *,
    timeout_seconds: int,
    poll_seconds: int = 10,
    valid_days: int = 90,
    on_status: StatusCallback | None = None,
) -> str:
    if config.session_id:
        return config.session_id
    pending = load_pending_auth()
    if _pending_auth_is_fresh(pending):
        auth = pending
    else:
        try:
            auth = start_authorization(config, valid_days=valid_days)
        except EnableBankingError:
            if config.aspsp_country == "LT":
                raise
            fallback = EnableBankingConfig(
                application_id=config.application_id,
                private_key_pem=config.private_key_pem,
                redirect_url=config.redirect_url,
                session_id=config.session_id,
                aspsp_name=config.aspsp_name,
                aspsp_country="LT",
                psu_type=config.psu_type,
            )
            auth = start_authorization(fallback, valid_days=valid_days)
        save_pending_auth(
            {
                "authorization_id": auth.get("authorization_id"),
                "url": auth.get("url"),
                "redirect_url_used": auth.get("redirect_url_used"),
                "created_at": _now_iso(),
            }
        )
    redirect_url = str(auth.get("redirect_url_used") or config.redirect_url or "")
    token = webhook_token_from_url(redirect_url)
    _emit(
        on_status,
        {
            "state": "waiting_for_ais_consent",
            "authorization_id": auth.get("authorization_id"),
            "url": auth.get("url"),
            "redirect_url_used": redirect_url,
            "user_action": (
                "Open `url` on a phone with the Revolut app and approve account "
                "information access. Enable Banking requires this second consent "
                "after account linking; the redirect is captured automatically."
            ),
        },
    )
    code = wait_for_auth_code(
        token,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        on_poll=lambda remaining: _emit(
            on_status,
            {
                "state": "waiting_for_ais_consent",
                "authorization_id": auth.get("authorization_id"),
                "url": auth.get("url"),
                "seconds_remaining": remaining,
                "user_action": (
                    "Open `url` on a phone with the Revolut app and approve "
                    "account information access."
                ),
            },
        ),
    )
    session = create_session(config, code)
    save_session(session)
    clear_pending_auth()
    session_id = str(session.get("session_id") or "")
    if not session_id:
        raise EnableBankingError("POST /sessions did not return a session_id.")
    _emit(
        on_status,
        {
            "state": "session_created",
            "session_id": session_id,
            "account_count": len(session.get("accounts") or []),
        },
    )
    return session_id


def wait_and_fetch(
    config: EnableBankingConfig,
    *,
    days: int = 30,
    timeout_seconds: int = 14400,
    linking_url: str | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    if not config.session_id:
        wait_until_application_active(
            config,
            timeout_seconds=timeout_seconds,
            linking_url=linking_url,
            on_status=on_status,
        )
        session_id = ensure_ais_session(
            config,
            timeout_seconds=timeout_seconds,
            on_status=on_status,
        )
        config = EnableBankingConfig(
            application_id=config.application_id,
            private_key_pem=config.private_key_pem,
            redirect_url=config.redirect_url,
            session_id=session_id,
            aspsp_name=config.aspsp_name,
            aspsp_country=config.aspsp_country,
            psu_type=config.psu_type,
        )
    payload = fetch_transactions_json(config, days=days)
    _emit(on_status, {"state": "fetched", "account_count": payload["account_count"]})
    return payload
