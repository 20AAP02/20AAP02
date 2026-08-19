"""Turn Enable Banking payloads into a Notion sync plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .classify import (
    Classification,
    classify_transaction,
    load_defaults,
    load_rules,
    month_url,
)


PREFERRED_BALANCE_TYPES = (
    "ITAV",
    "CLBD",
    "XPCD",
    "VALU",
    "OTHR",
)


@dataclass(frozen=True)
class AccountBalancePlan:
    uid: str
    name: str
    iban: str | None
    currency: str | None
    mapped_account: str
    balance: float | None
    as_of: str | None


@dataclass(frozen=True)
class ExpensePlan:
    classification: Classification
    month_url: str | None
    duplicate_of: str | None


def pick_balance(balances_payload: dict[str, Any]) -> tuple[float | None, str | None]:
    balances = balances_payload.get("balances") or []
    by_type = {
        str(item.get("balance_type")): item for item in balances if item.get("balance_amount")
    }
    chosen = None
    for balance_type in PREFERRED_BALANCE_TYPES:
        if balance_type in by_type:
            chosen = by_type[balance_type]
            break
    if chosen is None and balances:
        chosen = balances[0]
    if chosen is None:
        return None, None
    amount = (chosen.get("balance_amount") or {}).get("amount")
    as_of = chosen.get("reference_date") or chosen.get("last_committed_transaction")
    return (float(amount) if amount is not None else None, as_of)


def build_account_plan(
    account: dict[str, Any],
    balances_payload: dict[str, Any],
    mapped_account: str,
) -> AccountBalancePlan:
    balance, as_of = pick_balance(balances_payload)
    identification = account.get("account_id") or account.get("identification") or {}
    iban = None
    if isinstance(identification, dict):
        iban = identification.get("iban")
    return AccountBalancePlan(
        uid=str(account.get("uid") or account.get("account_uid") or ""),
        name=str(account.get("name") or account.get("product") or "Revolut"),
        iban=iban,
        currency=(account.get("currency") if isinstance(account.get("currency"), str) else None),
        mapped_account=mapped_account,
        balance=balance,
        as_of=str(as_of)[:10] if as_of else None,
    )


def build_expense_plans(
    *,
    account: dict[str, Any],
    transactions: list[dict[str, Any]],
    notion_config: dict[str, Any],
    existing_open_banking_ids: set[str],
    existing_soft_keys: set[tuple[str, str, float]],
) -> list[ExpensePlan]:
    rules = load_rules()
    defaults = load_defaults()
    account_uid = str(account.get("uid") or "")
    plans: list[ExpensePlan] = []
    for transaction in transactions:
        classification = classify_transaction(
            transaction,
            account_uid=account_uid,
            bank_account=account,
            rules=rules,
            defaults=defaults,
        )
        if classification.skip:
            continue
        duplicate_of = None
        if classification.open_banking_id in existing_open_banking_ids:
            duplicate_of = classification.open_banking_id
        else:
            soft_key = (
                classification.date,
                classification.name.lower(),
                round(classification.amount, 2),
            )
            if soft_key in existing_soft_keys:
                duplicate_of = "soft-match"
        plans.append(
            ExpensePlan(
                classification=classification,
                month_url=month_url(notion_config, classification.date),
                duplicate_of=duplicate_of,
            )
        )
    return plans


def plan_to_dict(
    accounts: list[AccountBalancePlan],
    expenses: list[ExpensePlan],
) -> dict[str, Any]:
    create = [expense for expense in expenses if expense.duplicate_of is None]
    skip = [expense for expense in expenses if expense.duplicate_of is not None]
    return {
        "accounts": [asdict(account) for account in accounts],
        "expenses_to_create": [_expense_dict(item) for item in create],
        "expenses_already_synced": [_expense_dict(item) for item in skip],
        "summary": {
            "accounts": len(accounts),
            "create": len(create),
            "already_synced": len(skip),
            "needs_review": sum(
                1 for item in create if item.classification.needs_review
            ),
        },
    }


def _expense_dict(plan: ExpensePlan) -> dict[str, Any]:
    classification = plan.classification
    return {
        "name": classification.name,
        "amount": classification.amount,
        "date": classification.date,
        "area": classification.area,
        "category": classification.category,
        "account": classification.account,
        "needs_review": classification.needs_review,
        "notes": classification.notes,
        "open_banking_id": classification.open_banking_id,
        "month_key": classification.month_key,
        "month_url": plan.month_url,
        "matched_rule": classification.matched_rule,
        "duplicate_of": plan.duplicate_of,
        "notion_properties": {
            "Name": classification.name,
            "Amount": classification.amount,
            "Account": classification.account,
            "Area": classification.area,
            "Category": classification.category,
            "Needs review": "__YES__" if classification.needs_review else "__NO__",
            "Reimbursement": "__NO__",
            "Notes": classification.notes,
            "Open banking id": classification.open_banking_id,
            "date:Date:start": classification.date,
            "date:Date:is_datetime": 0,
            "Month": [plan.month_url] if plan.month_url else [],
        },
    }
