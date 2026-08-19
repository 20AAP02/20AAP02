from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import decode, get_unverified_header

from enablebanking_sync.classify import (
    DEFAULT_NOTION_PATH,
    classify_transaction,
    load_defaults,
    load_json,
    load_rules,
    map_bank_account,
    month_url,
    open_banking_id,
)
from enablebanking_sync.client import (
    EnableBankingConfig,
    code_from_redirect_url,
    jwt_metadata,
    make_jwt,
)
from enablebanking_sync.fetch import _pending_auth_is_fresh, redact_iban
from enablebanking_sync.home import load_home_credentials
from enablebanking_sync.plan import build_expense_plans, pick_balance, plan_to_dict
from enablebanking_sync.webhook import webhook_token_from_url


def _tx(**overrides):
    payload = {
        "entry_reference": "ref-1",
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
        "booking_date": "2026-08-18",
        "transaction_amount": {"amount": "12.30", "currency": "EUR"},
        "creditor": {"name": "Auchan Energy Sa Alf"},
        "remittance_information": ["Card payment"],
    }
    payload.update(overrides)
    return payload


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rules()
        self.defaults = load_defaults()
        self.account = {"uid": "acc-1", "name": "Personal EUR", "currency": "EUR"}

    def _classify(self, transaction):
        return classify_transaction(
            transaction,
            account_uid="acc-1",
            bank_account=self.account,
            rules=self.rules,
            defaults=self.defaults,
        )

    def test_auchan_is_groceries(self) -> None:
        result = self._classify(_tx())
        self.assertFalse(result.skip)
        self.assertEqual(result.area, "Living")
        self.assertEqual(result.category, "Groceries")
        self.assertFalse(result.needs_review)
        self.assertEqual(result.matched_rule, "auchan")

    def test_uber_eats_beats_generic_uber(self) -> None:
        result = self._classify(_tx(creditor={"name": "Uber Eats"}))
        self.assertEqual(result.matched_rule, "uber-eats")
        self.assertEqual(result.category, "Dining / going out")

    def test_cursor_goes_to_subscriptions_pocket(self) -> None:
        result = self._classify(_tx(creditor={"name": "Cursor"}))
        self.assertEqual(result.account, "Revolut subscriptions")
        self.assertEqual(result.category, "Subscriptions")

    def test_guanyu_is_mandarin(self) -> None:
        result = self._classify(
            _tx(
                creditor={"name": "Guanyu Cheng"},
                remittance_information=["Aula de Mandarim"],
            )
        )
        self.assertEqual(result.category, "Mandarin")
        self.assertEqual(result.area, "Learning")

    def test_activobank_is_transfer(self) -> None:
        result = self._classify(_tx(creditor={"name": "ActivoBank"}))
        self.assertEqual(result.category, "Account transfer")

    def test_mbway_needs_review(self) -> None:
        result = self._classify(_tx(creditor={"name": "MBWay"}, remittance_information=["Sara"]))
        self.assertEqual(result.category, "Friends / shared")
        self.assertTrue(result.needs_review)

    def test_credits_are_skipped(self) -> None:
        result = self._classify(_tx(credit_debit_indicator="CRDT", debtor={"name": "Nokia"}))
        self.assertTrue(result.skip)
        self.assertEqual(result.skip_reason, "credit")

    def test_pending_is_skipped(self) -> None:
        result = self._classify(_tx(status="PDNG"))
        self.assertTrue(result.skip)
        self.assertEqual(result.skip_reason, "pending")

    def test_unknown_merchant_needs_review(self) -> None:
        result = self._classify(_tx(creditor={"name": "Colegio Sao Tomas"}))
        self.assertTrue(result.needs_review)
        self.assertEqual(result.category, "Other")

    def test_open_banking_id_prefers_entry_reference(self) -> None:
        identifier = open_banking_id("acc-1", _tx())
        self.assertEqual(identifier, "acc-1:ref-1")

    def test_map_subscription_account(self) -> None:
        self.assertEqual(
            map_bank_account({"name": "Subscriptions EUR", "product": "Pocket"}),
            "Revolut subscriptions",
        )
        self.assertEqual(map_bank_account({"name": "Main"}), "Revolut")

    def test_month_url_august_2026(self) -> None:
        notion = load_json(DEFAULT_NOTION_PATH)
        url = month_url(notion, "2026-08-18")
        self.assertIn("3af2c12c5a298117b645d0764b26bf72", url or "")


class PlanTests(unittest.TestCase):
    def test_pick_itav_balance(self) -> None:
        amount, as_of = pick_balance(
            {
                "balances": [
                    {
                        "balance_type": "CLBD",
                        "balance_amount": {"amount": "10.00"},
                        "reference_date": "2026-08-01",
                    },
                    {
                        "balance_type": "ITAV",
                        "balance_amount": {"amount": "1013.34"},
                        "reference_date": "2026-08-18",
                    },
                ]
            }
        )
        self.assertEqual(amount, 1013.34)
        self.assertEqual(as_of, "2026-08-18")

    def test_plan_skips_duplicates_and_credits(self) -> None:
        notion = load_json(DEFAULT_NOTION_PATH)
        account = {"uid": "acc-1", "name": "Main"}
        transactions = [
            _tx(),
            _tx(entry_reference="ref-dup", creditor={"name": "Auchan"}),
            _tx(credit_debit_indicator="CRDT", entry_reference="in-1", debtor={"name": "Salary"}),
        ]
        plans = build_expense_plans(
            account=account,
            transactions=transactions,
            notion_config=notion,
            existing_open_banking_ids={"acc-1:ref-dup"},
            existing_soft_keys=set(),
        )
        payload = plan_to_dict([], plans)
        self.assertEqual(payload["summary"]["create"], 1)
        self.assertEqual(payload["summary"]["already_synced"], 1)
        created = payload["expenses_to_create"][0]
        self.assertEqual(created["notion_properties"]["Category"], "Groceries")
        self.assertEqual(created["notion_properties"]["Needs review"], "__NO__")
        self.assertTrue(created["month_url"])


class RedirectTests(unittest.TestCase):
    def test_code_from_query(self) -> None:
        self.assertEqual(
            code_from_redirect_url("https://example.com/callback?code=abc&state=1"),
            "abc",
        )


class JwtTests(unittest.TestCase):
    def test_jwt_kid_is_application_id(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        config = EnableBankingConfig(
            application_id="11111111-2222-3333-4444-555555555555",
            private_key_pem=pem,
        )
        token = make_jwt(config)
        decoded = decode(
            token,
            key.public_key(),
            algorithms=["RS256"],
            audience="api.enablebanking.com",
            issuer="enablebanking.com",
        )
        header = get_unverified_header(token)
        self.assertEqual(decoded["aud"], "api.enablebanking.com")
        self.assertIn("exp", decoded)
        self.assertEqual(header["kid"], config.application_id)


class HomeAndFetchTests(unittest.TestCase):
    def test_load_home_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / "application.json").write_text(
                json.dumps({"app_id": "app-123"}),
                encoding="utf-8",
            )
            (home / "private.key").write_text("-----BEGIN PRIVATE KEY-----\nX\n", encoding="utf-8")
            (home / "session.json").write_text(
                json.dumps({"session_id": "sess-9"}),
                encoding="utf-8",
            )
            (home / "link.json").write_text(
                json.dumps({"url": "https://tilisy.enablebanking.com/ais/start?sessionid=abc"}),
                encoding="utf-8",
            )
            creds = load_home_credentials(home)
            self.assertEqual(creds.application_id, "app-123")
            self.assertIn("BEGIN PRIVATE KEY", creds.private_key_pem or "")
            self.assertEqual(creds.session_id, "sess-9")
            self.assertIn("tilisy.enablebanking.com", creds.linking_url or "")
            self.assertEqual(creds.aspsp_country, "PT")

    def test_redact_iban(self) -> None:
        self.assertEqual(redact_iban("LT601010012345678901"), "LT60…8901")
        self.assertIsNone(redact_iban(None))

    def test_webhook_token_from_url(self) -> None:
        token = webhook_token_from_url(
            "https://webhook.site/1475019f-7adb-43ef-9f2c-af20a0e5d812"
        )
        self.assertEqual(token, "1475019f-7adb-43ef-9f2c-af20a0e5d812")

    def test_pending_auth_freshness(self) -> None:
        fresh = {"url": "https://example", "created_at": datetime.now(timezone.utc).isoformat()}
        stale = {"url": "https://example", "created_at": "2020-01-01T00:00:00+00:00"}
        self.assertTrue(_pending_auth_is_fresh(fresh))
        self.assertFalse(_pending_auth_is_fresh(stale))

    def test_jwt_metadata_omits_token(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        config = EnableBankingConfig(
            application_id="11111111-2222-3333-4444-555555555555",
            private_key_pem=pem,
        )
        meta = jwt_metadata(config)
        self.assertEqual(meta["kid"], config.application_id)
        self.assertEqual(meta["alg"], "RS256")
        self.assertNotIn("token", meta)


if __name__ == "__main__":
    unittest.main()
