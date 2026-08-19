---
name: enable-banking-revolut
description: Set up Enable Banking, connect António's Revolut account, fetch recent transactions, and sync expenses plus balances into the Notion Finances 2026 tables. Use for daily Cursor Automations, Open Banking reconnects, and Revolut expense sync.
---

# Enable Banking → Revolut → Notion

Personal AIS (account information) sync for António Abranches Pinto. Do **not** scrape Revolut. Use Enable Banking, then write into the existing Notion finance databases.

This GitHub repo is **public**. Never commit PEMs, session ids, IBANs, or raw bank dumps.

## When to use

- User asks to connect Revolut / Enable Banking / Open Banking
- Daily automation: check Revolut expenses and sync Notion
- Session expired and Revolut consent must be renewed

## Who does what

The agent does Enable Banking signup, RSA keys, production app registration, JWT minting, AIS session exchange, transaction fetch, and Notion upserts.

The **only user action** is Revolut Strong Customer Authentication in the Revolut app (Enable Banking requires this twice: once to whitelist accounts, once to create an API session). Do not ask the user to copy PEMs, JWTs, redirect codes, or session ids.

## Credentials

Prefer `~/.enablebanking/` on the machine running the skill (mode `0700`). Env vars override files when set.

| File | Purpose |
| --- | --- |
| `application.json` | `{ "app_id": "<uuid>" }` — JWT `kid` |
| `private.key` | RSA private key PEM |
| `config.json` | ASPSP + redirect URL (webhook.site) |
| `session.json` | Written automatically after AIS consent |
| `link.json` | Account-linking URL for production activation |

Optional env overrides: `ENABLE_BANKING_APPLICATION_ID`, `ENABLE_BANKING_PRIVATE_KEY` / `_PATH`, `ENABLE_BANKING_REDIRECT_URL`, `ENABLE_BANKING_SESSION_ID`, `ENABLE_BANKING_ASPSP_COUNTRY` (default `PT`).

Install deps from this skill directory:

```bash
python3 -m pip install --user -r .cursor/skills/enable-banking-revolut/requirements.txt
cd .cursor/skills/enable-banking-revolut
```

## JWT + transactions JSON

Generates an RS256 JWT from `private.key` + `app_id`, calls `GET /accounts/{uid}/transactions`, prints JSON. The JWT string is never printed.

```bash
cd .cursor/skills/enable-banking-revolut
python3 -m enablebanking_sync ping
python3 scripts/fetch_transactions.py --days 30
# equivalent:
python3 -m enablebanking_sync transactions --days 30
```

If the app is still inactive or there is no session yet:

```bash
python3 scripts/fetch_transactions.py --wait --days 30
```

`--wait` polls `GET /application` until `active`, starts `POST /auth`, captures the redirect `code` from webhook.site, `POST /sessions`, then dumps transactions.

## One-time Enable Banking setup (agent)

Do **not** send the user to the Control Panel. The agent:

1. Creates/signs in the Enable Banking Control Panel user (Firebase email link; parse the raw MIME, Gmail MCP mangles `oobCode`).
2. Generates RSA 4096 + cert, registers a **Production** AIS app with privacy/terms URLs from `docs/`.
3. Stores `app_id` + `private.key` in `~/.enablebanking/`.
4. Starts **Activate by linking accounts** (restricted production) and gives the user **only** the Revolut URL.

Current production app: `Cursor Notion finance sync`, kid `2ecac023-1c4b-4ec5-8b6a-ce2488082334`. Redirects:

- `https://webhook.site/1475019f-7adb-43ef-9f2c-af20a0e5d812`
- `https://enablebanking.com/api/auth_redirect`

Account linking URL (user opens this first):

https://tilisy.enablebanking.com/ais/start?sessionid=84554ed9-e389-4c39-b550-8d584d494f97

If PT linking fails in Revolut, retry country `LT` (Revolut Bank UAB). Share every EUR pocket that should sync (main / subscriptions / savings / Ford Fiesta if shown). Open Banking often exposes **one EUR current account**, not every pocket.

After the app is `active`, `--wait` starts AIS authorisation automatically. Give the user that second Revolut URL; capture `code` from webhook.site — do not ask them to paste it.

Confirm JWT:

```bash
python3 -m enablebanking_sync ping
```

`active` must be `true` before `aspsps` / `connect` succeed (`GET /aspsps` is 403 while inactive).

## Map accounts then sync Notion

After `session.json` exists, write each account `uid` onto the matching Notion **Accounts** row (`Open banking uid`):

- Personal EUR current → [Revolut — main](https://www.notion.so/3af2c12c5a2981589537d732696f8da5)
- Subscriptions pocket → [Revolut — subscriptions](https://www.notion.so/3af2c12c5a298103bd63f2fa3199e926)
- Savings → [Revolut — savings](https://www.notion.so/3af2c12c5a29818196f0c94da6c32ce1)
- Ford Fiesta → [Revolut — Ford Fiesta](https://www.notion.so/3af2c12c5a29818494e1e8395d6cc5a1)

EU Revolut consent lasts about **90–180 days**. If `transactions` / `plan` returns 401/403 on the session, run `--wait` again and send the user a fresh Revolut URL.

## Daily sync (automation)

Default lookback: **3 days** (covers late bookings without replaying the whole ledger).

1. Query Notion Expenses (`collection://efe51b0d-7e9b-4cd0-bb70-59b02672ad99`) for rows in the lookback window. Collect `Open banking id`, plus soft keys `(Date, Name, Amount)`.
2. If useful, write those to a temp JSON `{ "open_banking_ids": [...], "soft_keys": [["2026-08-18","Auchan",12.3]] }` and pass `--existing`.
3. Run:

```bash
python3 -m enablebanking_sync plan --days 3
```

4. Apply `expenses_to_create` with Notion `create-pages` on parent `data_source_id` `efe51b0d-7e9b-4cd0-bb70-59b02672ad99`. Use the `notion_properties` object as-is (checkbox values are `__YES__` / `__NO__`; Date uses `date:Date:start`).
5. Skip `expenses_already_synced`.
6. Update each mapped Accounts row: `Balance`, `date:As of:start` = today, and `Open banking uid` if still empty.
7. Update the gray callout on [Finances 2026](https://www.notion.so/3af2c12c5a298167bee5f0f61e555c07) with cash totals and `Last open-banking sync <date>`.
8. Reply with counts: created / already synced / needs review. List **Needs review** rows for the user.

Never overwrite existing expense categories. Never delete rows. Incoming credits (`skip_reason: credit`) are not expenses; ignore them unless the user asked to log income.

## Notion targets

- Expenses data source: `efe51b0d-7e9b-4cd0-bb70-59b02672ad99`
- Accounts data source: `364e1ffa-6d82-4578-b4de-44e40e0e3863`
- Months data source: `e2f00920-a3ef-45e5-899a-d666b327ff52` (relation `Month` on each expense)
- Dedup property: `Open banking id` = `{account_uid}:{entry_reference}`

Config files: `config/notion.json`, `config/merchants.json`. Add new merchant rules there instead of one-off code.

## Cursor Automation prompt

Create a scheduled automation (daily) on this repo with Notion MCP. Credentials should already be in `~/.enablebanking/` (or Cursor env secrets). Prompt:

```
Follow the enable-banking-revolut skill.

Fetch Revolut transactions from Enable Banking for the last 3 days and sync new debits into my Notion Expenses table. Update Revolut balances on the Accounts table. Skip anything already stored under Open banking id. Do not create credits as expenses. Summarise what you added, especially Needs review rows. If the Enable Banking session is expired, start `python3 -m enablebanking_sync transactions --wait` and send me the Revolut URL — I will only complete Revolut SCA.
```

## Safety

- Do not print the private key or JWT.
- Do not commit `artifacts/` JSON that contains IBANs or full transaction dumps.
- If Enable Banking is unconfigured, the agent recreates keys + app; the user only opens Revolut.
