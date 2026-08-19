"""Map Enable Banking transactions onto the Notion Expenses schema."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MERCHANTS_PATH = SKILL_ROOT / "config" / "merchants.json"
DEFAULT_NOTION_PATH = SKILL_ROOT / "config" / "notion.json"


@dataclass(frozen=True)
class MerchantRule:
    rule_id: str
    needles: tuple[str, ...]
    name: str | None
    area: str
    category: str
    account: str
    needs_review: bool


@dataclass(frozen=True)
class Classification:
    skip: bool
    skip_reason: str | None
    name: str
    amount: float
    date: str
    area: str
    category: str
    account: str
    needs_review: bool
    notes: str
    open_banking_id: str
    month_key: str
    credit_debit: str
    status: str
    matched_rule: str | None
    raw_counterparty: str


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules(path: Path = DEFAULT_MERCHANTS_PATH) -> tuple[MerchantRule, ...]:
    payload = load_json(path)
    rules: list[MerchantRule] = []
    for item in payload["rules"]:
        rules.append(
            MerchantRule(
                rule_id=item["id"],
                needles=tuple(needle.lower() for needle in item["match"]),
                name=item.get("name"),
                area=item["area"],
                category=item["category"],
                account=item["account"],
                needs_review=bool(item.get("needs_review", False)),
            )
        )
    return tuple(rules)


def load_defaults(path: Path = DEFAULT_MERCHANTS_PATH) -> dict[str, Any]:
    return load_json(path)["defaults"]


def _text_blob(transaction: dict[str, Any]) -> str:
    parts: list[str] = []
    creditor = transaction.get("creditor") or {}
    debtor = transaction.get("debtor") or {}
    parts.append(str(creditor.get("name") or ""))
    parts.append(str(debtor.get("name") or ""))
    remittance = transaction.get("remittance_information") or []
    if isinstance(remittance, list):
        parts.extend(str(item) for item in remittance)
    elif remittance:
        parts.append(str(remittance))
    if transaction.get("note"):
        parts.append(str(transaction["note"]))
    if transaction.get("reference_number"):
        parts.append(str(transaction["reference_number"]))
    code = transaction.get("bank_transaction_code") or {}
    if code.get("description"):
        parts.append(str(code["description"]))
    return " ".join(part for part in parts if part).strip()


def _title_case_name(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw).strip(" -*")
    if not cleaned:
        return "Unknown merchant"
    if cleaned.lower().startswith("mbway"):
        return cleaned
    return cleaned.title() if cleaned.isupper() or cleaned.islower() else cleaned


def counterparty_name(transaction: dict[str, Any], credit_debit: str) -> str:
    if credit_debit == "DBIT":
        creditor = (transaction.get("creditor") or {}).get("name")
        if creditor:
            return str(creditor)
    else:
        debtor = (transaction.get("debtor") or {}).get("name")
        if debtor:
            return str(debtor)
    remittance = transaction.get("remittance_information") or []
    if isinstance(remittance, list) and remittance:
        return str(remittance[0])
    return "Unknown merchant"


def open_banking_id(account_uid: str, transaction: dict[str, Any]) -> str:
    entry_reference = transaction.get("entry_reference")
    if entry_reference:
        return f"{account_uid}:{entry_reference}"
    amount = (transaction.get("transaction_amount") or {}).get("amount") or "0"
    booked = transaction.get("booking_date") or transaction.get("transaction_date") or ""
    name = counterparty_name(
        transaction, transaction.get("credit_debit_indicator") or "DBIT"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{account_uid}:{booked}:{amount}:{slug}"


def map_bank_account(account: dict[str, Any]) -> str:
    haystack = " ".join(
        str(account.get(key) or "")
        for key in ("name", "product", "details", "identification", "currency")
    ).lower()
    if "subscription" in haystack:
        return "Revolut subscriptions"
    if "fiesta" in haystack or "ford" in haystack:
        return "Revolut Ford Fiesta"
    if "saving" in haystack:
        return "Revolut savings"
    if "vault" in haystack:
        return "Revolut vault"
    return "Revolut"


def _match_rule(blob: str, rules: tuple[MerchantRule, ...]) -> MerchantRule | None:
    lowered = blob.lower()
    for rule in rules:
        for needle in rule.needles:
            if needle in lowered:
                return rule
    return None


def _booking_date(transaction: dict[str, Any]) -> str:
    raw = (
        transaction.get("booking_date")
        or transaction.get("transaction_date")
        or transaction.get("value_date")
    )
    if not raw:
        return date.today().isoformat()
    return str(raw)[:10]


def classify_transaction(
    transaction: dict[str, Any],
    *,
    account_uid: str,
    bank_account: dict[str, Any],
    rules: tuple[MerchantRule, ...],
    defaults: dict[str, Any],
) -> Classification:
    status = str(transaction.get("status") or "")
    credit_debit = str(transaction.get("credit_debit_indicator") or "")
    booked = _booking_date(transaction)
    amount_raw = (transaction.get("transaction_amount") or {}).get("amount") or "0"
    amount = abs(float(amount_raw))
    counterpart = counterparty_name(transaction, credit_debit)
    blob = f"{counterpart} {_text_blob(transaction)}"
    identifier = open_banking_id(account_uid, transaction)
    notes_bits = [bit for bit in (_text_blob(transaction),) if bit]
    notes = f"To: {counterpart}"
    if notes_bits and notes_bits[0] not in notes:
        notes = f"{notes}; {notes_bits[0]}"
    month_key = booked[:7]

    if status and status not in {"BOOK", "CNCL"}:
        if status == "PDNG":
            return Classification(
                skip=True,
                skip_reason="pending",
                name=_title_case_name(counterpart),
                amount=amount,
                date=booked,
                area=str(defaults["area"]),
                category=str(defaults["category"]),
                account=map_bank_account(bank_account),
                needs_review=True,
                notes=notes,
                open_banking_id=identifier,
                month_key=month_key,
                credit_debit=credit_debit,
                status=status,
                matched_rule=None,
                raw_counterparty=counterpart,
            )
    if credit_debit == "CRDT":
        return Classification(
            skip=True,
            skip_reason="credit",
            name=_title_case_name(counterpart),
            amount=amount,
            date=booked,
            area="Giving / Transfers",
            category="Account transfer",
            account=map_bank_account(bank_account),
            needs_review=False,
            notes=notes,
            open_banking_id=identifier,
            month_key=month_key,
            credit_debit=credit_debit,
            status=status,
            matched_rule=None,
            raw_counterparty=counterpart,
        )

    rule = _match_rule(blob, rules)
    account = map_bank_account(bank_account)
    if rule:
        display_name = rule.name or _title_case_name(counterpart)
        return Classification(
            skip=False,
            skip_reason=None,
            name=display_name,
            amount=amount,
            date=booked,
            area=rule.area,
            category=rule.category,
            account=rule.account or account,
            needs_review=rule.needs_review,
            notes=notes,
            open_banking_id=identifier,
            month_key=month_key,
            credit_debit=credit_debit,
            status=status,
            matched_rule=rule.rule_id,
            raw_counterparty=counterpart,
        )

    return Classification(
        skip=False,
        skip_reason=None,
        name=_title_case_name(counterpart),
        amount=amount,
        date=booked,
        area=str(defaults["area"]),
        category=str(defaults["category"]),
        account=account,
        needs_review=True,
        notes=notes,
        open_banking_id=identifier,
        month_key=month_key,
        credit_debit=credit_debit,
        status=status,
        matched_rule=None,
        raw_counterparty=counterpart,
    )


def month_url(notion_config: dict[str, Any], booked: str) -> str | None:
    key = booked[:7]
    return notion_config.get("months", {}).get("pages", {}).get(key)
