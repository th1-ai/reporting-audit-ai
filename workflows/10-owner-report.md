# Workflow: the Weekly Owner Report

Objective: produce Monday's one-page owner email - revenue, GOP, occupancy,
ADR, RevPAR, and what moved - from the daily ledger, then get it reviewed and
sent.

Every number comes from `data/agent.db`'s `fin_daily` table
(`tools/owner_report.py`). The LLM never touches a figure; it only writes a
3-4 sentence closing note. See `docs/how-it-works.md` for the exact formulas.

## Steps

1. **Load the ledger, if you have not already.** Export the last 14+ days
   of daily revenue/cost/occupancy figures to `data/imports/financial_daily.csv`
   (see `docs/integrations.md`). `make demo` uses the bundled fixture instead -
   skip this step while you are still exploring.

2. **Run it.**
   ```bash
   python3 tools/run.py --once --report
   ```
   `--report` forces this job regardless of the weekly cadence in
   `config/agent.yaml: schedule.owner_report`. Plain `python3 tools/run.py --once`
   runs whatever is due, including this one on its normal schedule.

3. **See what it drafted.**
   ```bash
   python3 tools/review.py list --kind owner_report
   python3 tools/review.py show <id>
   ```
   The draft has a subject, a markdown body (`body_md`), and the recipient
   list. Read the three callouts out loud to the hotel - each one names the
   arithmetic that produced it ("F&B came in at X against Y, a swing of Z").

4. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py reject <id> --reason "wrong period"
   ```

5. **Send it.**
   ```bash
   python3 tools/review.py send
   ```
   In `mode: shadow` (the default) this is blocked and says so - see
   `docs/safety.md`. In `mode: live`, this actually emails the owners.

## If a run says "nothing to report"

`fin_daily` has no rows yet, or fewer than `report.min_history_days`
(default 7). Import `data/imports/financial_daily.csv` (step 1) and re-run.
With 8-13 days of history the report still runs, but the prior-week
comparison is short and the report says so in a note at the top.

## Rules

- One item per calendar week (`unique_key` = the period label). Re-running
  the same week never drafts a second one - see "Idempotency" in
  `docs/how-it-works.md`.
- ADR and RevPAR are the arithmetic mean of the daily values, not revenue
  divided by rooms sold. This is a deliberate choice - see "Design
  decisions" #3 in `docs/how-it-works.md` before "fixing" it.
- Web traffic only appears if `data/imports/web_traffic.csv` exists; otherwise
  the report says plainly that it is not connected, rather than omitting the
  promise silently.
