#!/usr/bin/env python3
"""Mint an Enable Banking JWT and dump Revolut transactions as JSON.

Credentials are read from ~/.enablebanking/ (or ENABLE_BANKING_* env vars).
The JWT itself is never printed — only kid/exp metadata.

Examples:
  python3 scripts/fetch_transactions.py --days 30
  python3 scripts/fetch_transactions.py --wait --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from enablebanking_sync.cli import load_config  # noqa: E402
from enablebanking_sync.client import EnableBankingError  # noqa: E402
from enablebanking_sync.fetch import fetch_transactions_json, wait_and_fetch  # noqa: E402
from enablebanking_sync.home import load_home_credentials  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for Revolut linking + AIS consent, then fetch",
    )
    parser.add_argument("--timeout", type=int, default=14400)
    args = parser.parse_args(argv)
    try:
        config = load_config()
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
        json.dump(payload, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    except EnableBankingError as exc:
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
