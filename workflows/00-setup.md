# Workflow: first-run setup

Objective: get Reporting & Audit AI from a fresh clone to a working demo, then
to real data, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet). `make doctor` will
   show a `FAIL` on "hotel identity" right after setup - expected, the
   property name is still the shipped placeholder. It will also `WARN` on the
   four `data/imports/*.csv` checks and on "F&B Sales Audit" - also expected,
   there is no real data yet and the sub-agent is off by default.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   This seeds the bundled fixtures (an invented hotel, "Hotel Aurora") and
   runs all three jobs - the Weekly Owner Report, the Weekly Income Audit,
   and the F&B Sales Audit (forced on for the demo only) - in their own
   database, `data/demo/demo.db`, never `data/agent.db`. Expect to see a
   seeded-fixtures summary, the reconciliation headline
   (`7 of 13 reconciled - 6 need(s) a human - 3,285.00 in question`), a
   review-queue summary, and the line `DEMO OK`. If you do not see that, stop
   and read `workflows/99-troubleshooting.md` before going further. Inspect
   what it queued with `python3 tools/review.py list --demo` (or
   `make review ARGS="--demo"`) - your real queue in `data/agent.db` is
   never touched, however many times you re-run `make demo`.

   If you ever need a clean slate - local runtime state got confusing, or
   this working copy is old enough to predate the demo/real database split -
   `make clean` deletes `data/` entirely (database, logs, exports) and
   leaves `.env` and `config/` untouched. Safe to run at any point; just
   re-run `make demo` / `make run` afterward.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, currency, timezone). Then:
   ```bash
   cp knowledge/property.example.md   knowledge/property.md
   cp knowledge/faq.example.md        knowledge/faq.md
   cp knowledge/signature.example.md  knowledge/signature.md
   ```
   Edit the signature to your own sign-off; it is what appears on the owner
   report and the F&B digest emails.

4. **Set who reads the reports.** In `config/agent.yaml`:
   - `report.recipients`: the owner(s)' email addresses. If left empty, the
     report falls back to `contacts.manager.email` in `config/hotel.yaml`.
   - `fnb_audit.outlets`: `[{name: "...", manager_email: "..."}]` per
     restaurant outlet, if you plan to turn the Pit Boss on.

5. **Pick how the agent thinks.** `config/agent.yaml`'s `llm.provider`
   starts as `interactive`. The only reasoning step in this whole agent is a
   cosmetic 3-4 sentence controller's note - everything else is arithmetic.
   On `interactive` that note is skipped entirely rather than pausing the
   run, so you will not see a pending prompt from this agent day to day. See
   `docs/how-it-works.md` for the other three providers and when the note is
   worth turning on.

6. **Connect your real data (optional for now).** `docs/integrations.md`
   covers exactly which CSV export goes where for the owner report, the
   income audit, and the till audit - `data/imports/financial_daily.csv`,
   `bank_statement.csv`, `stripe_payments.csv`, `pms_charges.csv`, and
   `pos_sales_daily.csv`. Run `make doctor` after adding any of them.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real, `knowledge/property.md` exists, and at
   least one data source is connected, move on to `workflows/10-owner-report.md`
   or `workflows/15-income-audit.md` to run the loop for real.
