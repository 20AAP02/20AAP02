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

## Secrets (required)

| Secret | Purpose |
| --- | --- |
| `ENABLE_BANKING_APPLICATION_ID` | Enable Banking application UUID (`kid` in the JWT) |
| `ENABLE_BANKING_PRIVATE_KEY` | Full RSA private key PEM (use `\n` for newlines if needed) |
| `ENABLE_BANKING_REDIRECT_URL` | Exact redirect URL registered on the application |
| `ENABLE_BANKING_SESSION_ID` | Session id from `session` (after Revolut SCA) |

Optional: `ENABLE_BANKING_ASPSP_NAME` (default `Revolut`), `ENABLE_BANKING_ASPSP_COUNTRY` (default `LT`).

Install deps from this skill directory:

```bash
python3 -m pip install --user -r .cursor/skills/enable-banking-revolut/requirements.txt
cd .cursor/skills/enable-banking-revolut
```

## One-time Enable Banking setup

User-only steps (SCA / Control Panel). Do them in this order:

1. Sign in at [enablebanking.com/sign-in](https://enablebanking.com/sign-in/).
2. Open **API applications** and register a **Production** app (sandbox cannot see live Revolut).
   - Name: something like `Cursor Notion finance sync`
   - Redirect URL: a HTTPS URL you control, or any URL you will paste back after SCA (it must be whitelisted)
   - Download the generated `.pem` — the filename is the application id
3. Application starts **Inactive**. Click **Activate by linking accounts**, choose **Revolut**, complete SCA in the Revolut app. This only whitelists the accounts; it does **not** create an API session.
4. Put application id + PEM + redirect URL into Cursor environment secrets.
5. Confirm JWT works:

```bash
python3 -m enablebanking_sync ping
python3 -m enablebanking_sync aspsps --country LT --name Revolut
```

If Revolut is not listed under `LT`, retry `--country GB`. Use the `name` + `country` pair Enable Banking returns.

## Connect Revolut (creates the API session)

1. Start authorisation (prints a URL):

```bash
python3 -m enablebanking_sync connect --name Revolut --country LT --valid-days 90
```

2. Open `url` on a phone with the Revolut app. Approve account information access for the EUR pockets that should sync (main / subscriptions / savings / Ford Fiesta if shown).
3. After redirect, paste the browser URL:

```bash
python3 -m enablebanking_sync session --redirect-url 'PASTE_FULL_REDIRECT_URL'
```

4. Save returned `session_id` as `ENABLE_BANKING_SESSION_ID`.
5. Write each account `uid` onto the matching Notion **Accounts** row (`Open banking uid`):
   - Personal EUR current → [Revolut — main](https://www.notion.so/3af2c12c5a2981589537d732696f8da5)
   - Subscriptions pocket → [Revolut — subscriptions](https://www.notion.so/3af2c12c5a298103bd63f2fa3199e926)
   - Savings → [Revolut — savings](https://www.notion.so/3af2c12c5a29818196f0c94da6c32ce1)
   - Ford Fiesta → [Revolut — Ford Fiesta](https://www.notion.so/3af2c12c5a29818494e1e8395d6cc5a1)

Revolut Open Banking often exposes **one EUR current account**, not every pocket. Map whatever comes back; do not invent extra accounts.

EU Revolut consent lasts about **90–180 days**. If `plan` / `accounts` returns 401/403 on the session, run `connect` again and replace `ENABLE_BANKING_SESSION_ID`.

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

Create a scheduled automation (daily) on this repo with Notion MCP + the Enable Banking secrets. Prompt:

```
Follow the enable-banking-revolut skill.

Fetch Revolut transactions from Enable Banking for the last 3 days and sync new debits into my Notion Expenses table. Update Revolut balances on the Accounts table. Skip anything already stored under Open banking id. Do not create credits as expenses. Summarise what you added, especially Needs review rows. If the Enable Banking session is expired, stop and tell me to reconnect — do not try to complete SCA yourself.
```

## Safety

- Do not print the private key or JWT.
- Do not commit `artifacts/` JSON that contains IBANs or full transaction dumps.
- If Enable Banking is unconfigured, explain the Control Panel steps; do not fake transactions.
