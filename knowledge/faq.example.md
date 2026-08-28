# FAQ - Hotel Aurora

<!--
Copy this to knowledge/faq.md. This agent never talks to a guest, so this is
not a guest FAQ - it is the questions your owners, your finance team, or
your own Claude session are likely to ask about how a number in the report
or the reconciliation was actually produced. One question per heading,
answered in the words you would actually use. Add your own as they come up.
-->

## Why did the owner report change the definitions for ADR and RevPAR?

They are not recomputed from weekly totals. Both are the arithmetic mean of
the daily `adr`/`revpar` values already in `fin_daily`, matching however
your own finance team already calculates them day to day. See
`docs/how-it-works.md` "Design decisions" #3.

## Why does the reconciliation only cover 7 days, not 120?

The three-way match (bank statement, Stripe, PMS folio charges) needs all
three sources lined up for the same window; widening `recon.window_days`
alone does not turn it into a two-way check against a finance sheet. See
`docs/how-it-works.md` "Design decisions" #1.

## Can the agent post the FX difference to the ledger by itself?

No, never in this template, even when the in-tolerance rule explains it. A
human always presses `post_fx`. See `docs/how-it-works.md` "Design
decisions" #4 and `docs/safety.md`.

## An amount is sitting "unallocated" - why didn't the agent just match it to the closest guess?

Because it would rather be honestly unsure than confidently wrong. An
unidentified bank credit stays unallocated, and the near-misses it declined
to use are named in the item detail - see `python3 tools/review.py show <id>`.

## Why does a recon item say "pending" instead of flagging it straight away?

Takings can take a couple of days to actually land in the bank
(`recon.settlement_days`, default 2). Inside that window a not-yet-settled
amount is labelled `pending`, not treated as missing.

## Our bank statement only exists as a PDF. Now what?

OCR-ing that PDF into `bank_statement.csv` is outside this template's scope.
Ask your Claude session to write a small OCR step for your bank's statement
layout, or check whether your bank offers a CSV/OFX export instead.

## I changed `data/imports/financial_daily.csv` and the report didn't change. Why?

Most likely the report for that ISO week was already drafted -
`run_owner_report` drafts once per week and returns the existing item on a
re-run instead of redrafting it (see `docs/how-it-works.md`
"Idempotency"). Reject the drafted item first, or wait for next Monday's
run, then re-import.

## Do I need to run a separate import command before every real pass?

No. `tools/run.py` re-imports the matching CSV in `data/imports/`
automatically, every time, right before the job reads the table - see
`docs/integrations.md`. Just save your export over the old file and run
the job.

## Why does a "logged only" recon action (`post_fx`, `ask_front_office`, `open_dispute`) say "sent" when nothing was emailed?

"Sent" there means the decision was recorded as an event, not that a
message went anywhere - there is nothing to email for those three. See
"Reusing the review FSM for actions" in `docs/how-it-works.md`.

## What happens to an approved report if `python3 tools/review.py send` runs while we are still in shadow mode?

Nothing leaves the mailbox - the send is blocked and the item goes straight
back to `approved`, not `failed`, so nothing needs re-approving. The next
`send` once mode is live just sends it. See `docs/safety.md`.
