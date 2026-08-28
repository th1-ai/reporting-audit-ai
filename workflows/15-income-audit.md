# Workflow: the Weekly Income Audit (three-way reconciliation)

Objective: for every euro that moved through the bank, Stripe and the PMS
folio over the last 7 days, decide whether all three agree, and when they do
not, work the exceptions.

Deterministic end to end (`tools/recon.py`). No LLM call decides anything
here - see `docs/how-it-works.md` for the seven-step design and "Design
decisions" #1 for why this is a 7-day, three-source check rather than the
roster's "120-day, two-source" framing.

## Steps

1. **Load the three sources, if you have not already.** Export
   `data/imports/bank_statement.csv`, `stripe_payments.csv` and
   `pms_charges.csv` (see `docs/integrations.md#reconciliation`). `make demo`
   uses the bundled fixture instead.

2. **Run it.**
   ```bash
   python3 tools/run.py --once --recon
   ```
   Prints the headline, e.g. `7 of 13 reconciled - 6 need(s) a human -
   3,285.00 in question`.

3. **See what needs a human.**
   ```bash
   python3 tools/review.py list --kind recon_item --status needs_human
   python3 tools/review.py show <id>
   ```
   Each item's `draft` has a `title`, a `detail` that names the threshold or
   rung that produced the verdict, and an `action` (or `null` if there is
   none - a chargeback is never "fixed", only routed to the Disputes
   process).

4. **Apply an action.** This is not `review.py approve/edit/reject` - a
   reconciliation exception has no message to send, only a fix to apply:
   ```bash
   python3 tools/recon.py action <id> link_customer
   python3 tools/recon.py action <id> post_folio
   python3 tools/recon.py action <id> refund_duplicate
   python3 tools/recon.py action <id> charge_balance
   python3 tools/recon.py action <id> post_fx          # logged only
   python3 tools/recon.py action <id> ask_front_office  # logged only
   python3 tools/recon.py action <id> open_dispute      # logged only
   ```
   The action named on the item's `draft.action` is the right one - do not
   guess a different one. In `mode: shadow` the four writing actions are
   blocked and the item moves to `failed`, carrying the reason; re-run the
   same command once the hotel is live and it resumes from there (see
   "Reusing the review FSM for actions" in `docs/how-it-works.md`). The
   three logged-only actions always complete - nothing they do needs
   approval because nothing is posted.

5. **The near-miss and the toggle.** An unidentified bank credit's detail
   names the payments within `recon.near_miss_eur` of it that were
   deliberately *not* used to guess a match - point this out, it is the
   agent refusing to guess, not a gap. To see the identity-matching toggle in
   action, flip `recon.rules.recon-fuzzy-identity` to `false` in
   `config/agent.yaml` and re-run: the one "identity" exception splits into
   two (a folio with no payment, a payment with no folio), the same euro
   figure counted twice.

## Rules

- Running the reconciliation writes nothing. Only `python3 tools/recon.py
  action` mutates anything, and only after a human names which action.
- One item per finding (`unique_key` = a stable hash of the finding). Re-running
  the same window never raises the same exception twice.
- The FX auto-post is never automatic in this template, even when
  `recon-tolerance` explains the difference - see "Design decisions" #4 in
  `docs/how-it-works.md`.
- A chargeback (`kind: chargeback`) has no fix action. It is routed to
  `open_dispute` (logged) because it is a case to argue, not a number to fix.
