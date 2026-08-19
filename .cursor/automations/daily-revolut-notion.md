# Daily Revolut → Notion expense sync

Create this as a **Cursor Automation** (there is no API to register one from a cloud agent).

## Create in the UI

1. Open [cursor.com/automations/new](https://cursor.com/automations/new)
2. **Name:** `Daily Revolut → Notion expenses`
3. **Trigger:** Scheduled · cron `CRON_TZ=Europe/Lisbon 0 7 * * *` (07:00 Lisbon, every day)
4. **Repository:** `github.com/20AAP02/20AAP02` · branch `main` (merge PR #2 first so the skill is on `main`; until then use `cursor/enable-banking-revolut-skill-1780`)
5. **Tools:** Notion MCP (required). Do **not** enable pull-request creation for this run — it must only update Notion.
6. **Environment:** the Cloud Agent environment that includes `~/.enablebanking/` (private key + session) **or** Cursor secrets `ENABLE_BANKING_APPLICATION_ID`, `ENABLE_BANKING_PRIVATE_KEY`, `ENABLE_BANKING_REDIRECT_URL`, `ENABLE_BANKING_SESSION_ID`, `ENABLE_BANKING_ASPSP_COUNTRY=PT`
7. Paste the prompt below. Save and enable.

## Prompt

```
Follow the enable-banking-revolut skill in this repo.

Do not edit git, do not open a pull request, and do not print PEMs, JWTs, IBANs, or session ids.

1. Load Enable Banking credentials from ~/.enablebanking/ or ENABLE_BANKING_* env vars.
2. Query Notion Expenses (collection://efe51b0d-7e9b-4cd0-bb70-59b02672ad99) for the last 3 days. Collect existing "Open banking id" values and soft keys (Date, Name, Amount).
3. From the skill directory run:
   python3 -m enablebanking_sync plan --days 3
   (write a temp --existing JSON if that helps dedup.)
4. Create only expenses_to_create with Notion create-pages on data_source_id efe51b0d-7e9b-4cd0-bb70-59b02672ad99, using notion_properties as-is.
5. Skip expenses_already_synced. Do not create credits. Never delete rows. Never overwrite an existing expense's category.
6. On Accounts (collection://364e1ffa-6d82-4578-b4de-44e40e0e3863): set Open banking uid on Revolut — main when empty. Only update Balance / As of from AIS when the AIS figure is for that same mapped row and is not wildly below the Notion balance (Open Banking does not see Revolut pockets; do not replace a ~€900 main balance with a ~€200 current-account ITAV).
7. Optionally refresh the Finances 2026 callout with Last open-banking sync <date>.
8. Reply with created / already synced / needs review. List Needs review merchants.

If GET /sessions or transactions returns 401/403, run python3 -m enablebanking_sync transactions --wait, give António the Revolut URL, and stop. Do not invent transactions.
```
