# Property facts - Hotel Aurora

<!--
Copy this to knowledge/property.md and replace everything with your own
details. This agent never talks to a guest - this file is context for
Reporting & Audit AI (and your own Claude session) about who reads its
output, what it will not do, and how your numbers are put together. Delete
any section that does not apply. Keep it factual and current.
-->

## The basics

- Name: Hotel Aurora
- Legal entity: the name on your bank account, Stripe account and PMS -
  needed so a bank/Stripe export that shows the legal name still reconciles
  cleanly against a PMS export that shows the trading name.
- Address: 1 Example Street, 1000-001 Lisbon, Portugal
- Phone: +351 200 000 000
- Email: finance@example.com
- Website: https://example.com
- 42 rooms across 4 floors, one restaurant and bar (Salt Restaurant)
- Currency: EUR. The owner report and the reconciliation are both in EUR -
  note here if your PMS, Stripe or bank ever settle in a different currency.

## Who reads this agent's output

- **The owners** - the Monday one-page report: revenue, GOP, occupancy, ADR,
  RevPAR, and a web-traffic line if `data/imports/web_traffic.csv` is
  connected. Recipients: `report.recipients` in `config/agent.yaml`.
- **The finance team / bookkeeper** - the weekly reconciliation queue and
  its action buttons. They are the ones who actually run
  `python3 tools/recon.py action <id> <name>` once mode is live.
- **Salt Restaurant's outlet manager** - the daily till exceptions digest,
  once the F&B Sales Audit sub-agent is enabled. Recipients:
  `fnb_audit.outlets` in `config/agent.yaml`.

## What this agent does not touch

- It never edits the finance ledger or the PMS itself. Every reconciliation
  action is a button a human presses; `post_fx`, `ask_front_office` and
  `open_dispute` only log the decision, they never write anywhere.
- It never sets a rate, never books a room, never contacts a guest. If
  anyone asks it to do any of those, say plainly that this agent only
  reports and flags - see the roster's own "what it won't do" line in
  `README.md`.

## Fiscal calendar

- Financial week: Monday to Sunday. The owner report always covers the ISO
  week that just ended - see "Idempotency" in `docs/how-it-works.md`.
- Month-end close: the 3rd business day of the following month. Note here
  if the finance team wants the owner report held back until close is
  done, or which days should not count toward `report.min_history_days`.
- Financial year starts: 1 January (change if yours does not).

## Ledger categories (`fin_daily` columns)

`data/imports/financial_daily.csv` maps one-to-one onto these - see
`docs/integrations.md`. Say what each one covers for your own chart of
accounts, so anyone reading a report (including your Claude session six
months from now) knows what is actually inside a number:

| Column | What it covers here |
|---|---|
| `revenue_rooms` | Room revenue - net of OTA commission, or gross? Say which. |
| `revenue_fnb` | Salt Restaurant and bar, food and beverage only |
| `revenue_spa` | (delete this row if not applicable) |
| `revenue_other` | Parking, late check-out fees, damage charges, ... |
| `costs_payroll` | Fully loaded (with employer taxes) or gross wages? Say which. |
| `costs_utilities` | Electricity, water, gas |
| `costs_fnb` | Cost of goods sold for Salt Restaurant and bar |
| `costs_marketing` | OTA commission (if not already netted out of `revenue_rooms`), plus ads |
| `costs_other` | Everything else - repairs, subscriptions, any bank fee not already covered by the reconciliation |

## Reconciliation policy

- **GL accounts.** FX/rounding differences post to `7620`
  (`config/agent.yaml: recon.gl_fx`); payout fees are coded to `6420`
  (`recon.gl_fees`). Change both to match your own chart of accounts.
- **Tolerance.** Differences under EUR 5 or 0.5%
  (`recon.tolerance_eur` / `recon.tolerance_pct`) are treated as FX/rounding
  noise - a human still has to press `post_fx`, the agent never posts it
  automatically. See `docs/how-it-works.md` "Design decisions" #4.
- **Settlement window.** Takings not yet in the bank within 2 days
  (`recon.settlement_days`) are labelled "pending", not treated as a
  mismatch.
- **Escalation.** Anything that lands on `needs_human` above [your
  threshold, e.g. EUR 200] gets flagged to [name/role] the same day rather
  than waiting for the Monday review. Say who, and how (email, or a
  WhatsApp/Slack ping via `messaging_webhook` - see `docs/integrations.md`).
- **Sign-off.** Who has the final say on a `refund_duplicate` or
  `charge_balance` action before it is approved? Name the role here so
  anyone opening `python3 tools/review.py list --kind recon_item` knows who
  to check with first.

## F&B outlets (only if the sub-agent is on)

- Salt Restaurant - lunch, dinner, late bar. Manager: [name/email], matching
  `fnb_audit.outlets` in `config/agent.yaml`.
