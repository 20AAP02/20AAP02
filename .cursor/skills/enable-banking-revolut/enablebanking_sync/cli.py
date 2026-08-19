"""Command-line entry point for Enable Banking setup and daily sync planning."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .classify import DEFAULT_NOTION_PATH, load_json, map_bank_account
from .client import (
    EnableBankingConfig,
    EnableBankingError,
    code_from_redirect_url,
    create_session,
    get_account_balances,
    get_account_transactions,
    get_application,
    get_session,
    jwt_metadata,
    list_aspsps,
    start_authorization,
)
from .fetch import fetch_transactions_json, wait_and_fetch
from .home import (
    HomeCredentials,
    ensure_home_layout,
    load_home_credentials,
    save_session,
)
from .plan import build_account_plan, build_expense_plans, plan_to_dict


def _read_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _private_key_pem(home: HomeCredentials) -> str:
    pem = _read_env("ENABLE_BANKING_PRIVATE_KEY")
    if pem:
        return pem.replace("\\n", "\n")
    path = _read_env("ENABLE_BANKING_PRIVATE_KEY_PATH")
    if path:
        return Path(path).read_text(encoding="utf-8")
    if home.private_key_pem:
        return home.private_key_pem
    raise EnableBankingError(
        "Missing RSA private key. Put it at ~/.enablebanking/private.key "
        "or set ENABLE_BANKING_PRIVATE_KEY / ENABLE_BANKING_PRIVATE_KEY_PATH."
    )


def load_config() -> EnableBankingConfig:
    ensure_home_layout()
    home = load_home_credentials()
    application_id = _read_env("ENABLE_BANKING_APPLICATION_ID") or home.application_id
    if not application_id:
        raise EnableBankingError(
            "Missing application id. Write ~/.enablebanking/application.json "
            "or set ENABLE_BANKING_APPLICATION_ID."
        )
    return EnableBankingConfig(
        application_id=application_id,
        private_key_pem=_private_key_pem(home),
        redirect_url=_read_env("ENABLE_BANKING_REDIRECT_URL") or home.redirect_url,
        session_id=_read_env("ENABLE_BANKING_SESSION_ID") or home.session_id,
        aspsp_name=_read_env("ENABLE_BANKING_ASPSP_NAME") or home.aspsp_name,
        aspsp_country=_read_env("ENABLE_BANKING_ASPSP_COUNTRY") or home.aspsp_country,
        psu_type=_read_env("ENABLE_BANKING_PSU_TYPE") or home.psu_type,
    )


def _dump(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _require_session_id(config: EnableBankingConfig, override: str | None) -> str:
    session_id = override or config.session_id
    if not session_id:
        raise EnableBankingError(
            "No session id. Run `connect` then `session`, and set ENABLE_BANKING_SESSION_ID."
        )
    return session_id


def cmd_ping(config: EnableBankingConfig, _args: argparse.Namespace) -> int:
    application = get_application(config)
    _dump(
        {
            "ok": True,
            "application_id": application.get("application_id") or config.application_id,
            "name": application.get("name"),
            "environment": application.get("environment"),
            "active": application.get("active"),
            "redirect_urls": application.get("redirect_urls"),
            "jwt": jwt_metadata(config),
        }
    )
    return 0


def cmd_aspsps(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    country = args.country or config.aspsp_country
    aspsps = list_aspsps(config, country)
    matches = [
        item
        for item in aspsps
        if args.name.lower() in str(item.get("name") or "").lower()
    ]
    _dump({"country": country, "count": len(aspsps), "revolut_matches": matches, "aspsps": aspsps})
    return 0


def cmd_connect(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    if args.name:
        config = EnableBankingConfig(
            application_id=config.application_id,
            private_key_pem=config.private_key_pem,
            redirect_url=config.redirect_url,
            session_id=config.session_id,
            aspsp_name=args.name,
            aspsp_country=args.country or config.aspsp_country,
            psu_type=config.psu_type,
        )
    payload = start_authorization(config, valid_days=args.valid_days)
    _dump(
        {
            "authorization_id": payload.get("authorization_id"),
            "url": payload.get("url"),
            "redirect_url_used": payload.get("redirect_url_used"),
            "next": (
                "Open `url` on a device with the Revolut app, complete SCA, then run "
                "`python3 -m enablebanking_sync session --redirect-url PASTE_URL` "
                "or `--code AUTH_CODE`. Store the returned session_id as "
                "ENABLE_BANKING_SESSION_ID."
            ),
        }
    )
    return 0


def cmd_session(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    code = args.code
    if args.redirect_url:
        code = code_from_redirect_url(args.redirect_url)
    if not code:
        raise EnableBankingError("Provide --code or --redirect-url.")
    session = create_session(config, code)
    save_session(session)
    accounts = session.get("accounts") or []
    _dump(
        {
            "session_id": session.get("session_id"),
            "status": session.get("status"),
            "valid_until": (session.get("access") or {}).get("valid_until"),
            "account_count": len(accounts),
            "accounts": [
                {
                    "uid": item.get("uid"),
                    "name": item.get("name") or item.get("product"),
                    "currency": item.get("currency"),
                    "mapped_account": map_bank_account(item),
                }
                for item in accounts
            ],
            "next": (
                "Session id saved to ~/.enablebanking/session.json. "
                "Run `python3 -m enablebanking_sync transactions --days 30` "
                "or `python3 -m enablebanking_sync plan --days 3`."
            ),
        }
    )
    return 0


def _session_accounts(config: EnableBankingConfig, session_id: str) -> list[dict[str, Any]]:
    session = get_session(config, session_id)
    return session.get("accounts") or []


def cmd_accounts(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    session_id = _require_session_id(config, args.session_id)
    accounts = _session_accounts(config, session_id)
    rows = []
    for account in accounts:
        uid = str(account.get("uid") or "")
        balances = get_account_balances(config, uid)
        rows.append(
            build_account_plan(account, balances, map_bank_account(account))
        )
    _dump({"session_id": session_id, "accounts": [item.__dict__ for item in rows]})
    return 0


def _load_existing(path: str | None) -> tuple[set[str], set[tuple[str, str, float]]]:
    if not path:
        return set(), set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = {str(item) for item in payload.get("open_banking_ids") or []}
    soft_keys: set[tuple[str, str, float]] = set()
    for item in payload.get("soft_keys") or []:
        soft_keys.add((str(item[0]), str(item[1]).lower(), round(float(item[2]), 2)))
    return ids, soft_keys


def cmd_plan(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    session_id = _require_session_id(config, args.session_id)
    notion_config = load_json(DEFAULT_NOTION_PATH)
    date_to = date.today()
    date_from = date_to - timedelta(days=args.days)
    existing_ids, existing_soft = _load_existing(args.existing)
    accounts = _session_accounts(config, session_id)
    account_plans = []
    expense_plans = []
    for account in accounts:
        uid = str(account.get("uid") or "")
        balances = get_account_balances(config, uid)
        mapped = map_bank_account(account)
        account_plans.append(build_account_plan(account, balances, mapped))
        transactions = get_account_transactions(
            config,
            uid,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
        expense_plans.extend(
            build_expense_plans(
                account=account,
                transactions=transactions,
                notion_config=notion_config,
                existing_open_banking_ids=existing_ids,
                existing_soft_keys=existing_soft,
            )
        )
    payload = plan_to_dict(account_plans, expense_plans)
    payload["window"] = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "session_id": session_id,
    }
    payload["notion"] = {
        "expenses_data_source_id": notion_config["expenses"]["data_source_id"],
        "accounts_data_source_id": notion_config["accounts"]["data_source_id"],
        "finances_page_url": notion_config["finances_page_url"],
    }
    _dump(payload)
    return 0


def cmd_transactions(config: EnableBankingConfig, args: argparse.Namespace) -> int:
    home = load_home_credentials()
    if args.wait:
        payload = wait_and_fetch(
            config,
            days=args.days,
            timeout_seconds=args.timeout,
            linking_url=home.linking_url,
        )
    else:
        payload = fetch_transactions_json(config, days=args.days)
    _dump(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enablebanking_sync",
        description="Connect Enable Banking to Revolut and plan Notion expense syncs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ping = sub.add_parser("ping", help="Verify JWT credentials against GET /application")
    ping.set_defaults(func=cmd_ping)

    aspsps = sub.add_parser("aspsps", help="List ASPSPs for a country and highlight Revolut")
    aspsps.add_argument("--country", default=None)
    aspsps.add_argument("--name", default="revolut")
    aspsps.set_defaults(func=cmd_aspsps)

    connect = sub.add_parser("connect", help="Start Revolut AIS authorisation and print the URL")
    connect.add_argument("--name", default=None, help="ASPSP name override")
    connect.add_argument("--country", default=None)
    connect.add_argument("--valid-days", type=int, default=90)
    connect.set_defaults(func=cmd_connect)

    session = sub.add_parser("session", help="Exchange the bank redirect code for a session id")
    session.add_argument("--code", default=None)
    session.add_argument("--redirect-url", default=None)
    session.set_defaults(func=cmd_session)

    accounts = sub.add_parser("accounts", help="Show authorised accounts and balances")
    accounts.add_argument("--session-id", default=None)
    accounts.set_defaults(func=cmd_accounts)

    plan = sub.add_parser("plan", help="Fetch recent transactions and emit a Notion upsert plan")
    plan.add_argument("--days", type=int, default=3)
    plan.add_argument("--session-id", default=None)
    plan.add_argument(
        "--existing",
        default=None,
        help="JSON file with open_banking_ids and optional soft_keys already in Notion",
    )
    plan.set_defaults(func=cmd_plan)

    transactions = sub.add_parser(
        "transactions",
        help="Mint a JWT and GET /accounts/{uid}/transactions as JSON (token is not printed)",
    )
    transactions.add_argument("--days", type=int, default=30)
    transactions.add_argument(
        "--wait",
        action="store_true",
        help="Poll until Revolut linking + AIS consent complete, then fetch",
    )
    transactions.add_argument("--timeout", type=int, default=14400)
    transactions.set_defaults(func=cmd_transactions)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config()
        return args.func(config, args)
    except EnableBankingError as exc:
        _dump({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
