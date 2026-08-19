"""Load Enable Banking credentials from ~/.enablebanking or env overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_HOME = Path.home() / ".enablebanking"
DEFAULT_REDIRECT_URL = "https://webhook.site/1475019f-7adb-43ef-9f2c-af20a0e5d812"
DEFAULT_ASPSP_NAME = "Revolut"
DEFAULT_ASPSP_COUNTRY = "PT"
DEFAULT_PSU_TYPE = "personal"


def _read_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def home_dir() -> Path:
    override = _read_env("ENABLE_BANKING_HOME")
    return Path(override).expanduser() if override else DEFAULT_HOME


@dataclass(frozen=True)
class HomeCredentials:
    application_id: str | None
    private_key_pem: str | None
    redirect_url: str | None
    session_id: str | None
    aspsp_name: str
    aspsp_country: str
    psu_type: str
    linking_url: str | None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _application_id_from_home(home: Path) -> str | None:
    data = _read_json(home / "application.json")
    for key in ("app_id", "kid", "application_id", "id"):
        value = data.get(key)
        if value:
            return str(value)
    raw = data.get("raw")
    if isinstance(raw, dict):
        for key in ("app_id", "kid", "id"):
            value = raw.get(key)
            if value:
                return str(value)
    return None


def _private_key_from_home(home: Path) -> str | None:
    path = home / "private.key"
    if not path.is_file():
        return None
    pem = path.read_text(encoding="utf-8").strip()
    return pem or None


def _config_from_home(home: Path) -> dict[str, Any]:
    return _read_json(home / "config.json")


def _session_id_from_home(home: Path) -> str | None:
    data = _read_json(home / "session.json")
    value = data.get("session_id")
    return str(value) if value else None


def _linking_url_from_home(home: Path) -> str | None:
    data = _read_json(home / "link.json")
    value = data.get("url")
    return str(value) if value else None


def load_home_credentials(home: Path | None = None) -> HomeCredentials:
    resolved = home or home_dir()
    cfg = _config_from_home(resolved)
    return HomeCredentials(
        application_id=_application_id_from_home(resolved),
        private_key_pem=_private_key_from_home(resolved),
        redirect_url=(
            str(cfg["redirect_url"])
            if cfg.get("redirect_url")
            else DEFAULT_REDIRECT_URL
        ),
        session_id=_session_id_from_home(resolved),
        aspsp_name=str(cfg.get("aspsp_name") or DEFAULT_ASPSP_NAME),
        aspsp_country=str(cfg.get("aspsp_country") or DEFAULT_ASPSP_COUNTRY),
        psu_type=str(cfg.get("psu_type") or DEFAULT_PSU_TYPE),
        linking_url=_linking_url_from_home(resolved),
    )


def ensure_home_layout(home: Path | None = None) -> Path:
    resolved = home or home_dir()
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = resolved / "config.json"
    if not config_path.is_file():
        config_path.write_text(
            json.dumps(
                {
                    "aspsp_name": DEFAULT_ASPSP_NAME,
                    "aspsp_country": DEFAULT_ASPSP_COUNTRY,
                    "psu_type": DEFAULT_PSU_TYPE,
                    "redirect_url": DEFAULT_REDIRECT_URL,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
    return resolved


def save_session(session: dict[str, Any], home: Path | None = None) -> Path:
    resolved = ensure_home_layout(home)
    accounts = []
    for item in session.get("accounts") or []:
        accounts.append(
            {
                "uid": item.get("uid"),
                "name": item.get("name") or item.get("product"),
                "currency": item.get("currency"),
                "iban": ((item.get("account_id") or {}).get("iban")),
                "mapped_account": item.get("mapped_account"),
            }
        )
    payload = {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "valid_until": (session.get("access") or {}).get("valid_until"),
        "accounts": accounts,
    }
    path = resolved / "session.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def save_status(status: dict[str, Any], home: Path | None = None) -> Path:
    resolved = ensure_home_layout(home)
    path = resolved / "fetch_status.json"
    path.write_text(json.dumps(status, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def save_pending_auth(payload: dict[str, Any], home: Path | None = None) -> Path:
    resolved = ensure_home_layout(home)
    path = resolved / "pending_ais.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load_pending_auth(home: Path | None = None) -> dict[str, Any]:
    return _read_json((home or home_dir()) / "pending_ais.json")


def clear_pending_auth(home: Path | None = None) -> None:
    path = (home or home_dir()) / "pending_ais.json"
    if path.is_file():
        path.unlink()
