# Sub-agents in this repo

## F&B Sales Audit AI - "The Pit Boss"

**Does.** Plugs into the restaurant POS and audits item-level sales across
every outlet daily: voids, discounts, comps, and refunds by server and by
shift. Flags the anomaly patterns that look like leakage or fraud, tracks
menu-item performance, and sends each outlet manager a daily exceptions
digest.

**Won't.** Flags patterns; it doesn't accuse. Every alert arrives with the
underlying transactions attached so a manager makes the call.

**Off by default.** The Weekly Owner Report and the Weekly Income Audit are
useful to any hotel, restaurant or not. The Pit Boss is useful once you have
a real restaurant POS export - turn it on then. `workflows/20-fnb-sales-audit.md`
covers the exact steps.

```yaml
# config/agent.yaml
subagents:
  fnb_sales_audit:
    enabled: true
fnb_audit:
  outlets:
    - {name: "Salt Restaurant", manager_email: "salt-manager@example.com"}
```

## What it adds on top of the parent

A daily, per-outlet, shift-level view the weekly owner report cannot give
you - by the time a leakage pattern shows up in a week's `costs_fnb` line,
it has already been averaged away with everything else that happened that
week. The Pit Boss catches it the next morning, addressed to the person who
can actually do something about it.

## The honest-derivation rule

The bundled fixture's POS feed (`pos_sales_daily`) has units, revenue and
covers per item per day - **no void, comp, discount or refund columns**,
because almost no mainstream POS export carries them either. Rather than
invent data, `tools/fnb_audit.py` *derives* a till-level profile
arithmetically from the real rows:

- each trading day is split into three shifts on fixed cover shares (Lunch
  34% / Dinner 46% / Late 20%);
- a baseline void count per shift comes from a fixed void rate per shift;
- an "attach shortfall" term compares the day's actual units sold against
  the 30-day mean units-per-cover. Where a day rang up materially fewer
  items than its covers imply, the missing items are attributed to the
  **late shift only**, as rung-then-voided lines - the classic
  low-supervision pattern. Shortfall below `fnb_audit.shortfall_floor`
  (default 3 items) is treated as ordinary noise, so only genuine outliers
  spike.

This is presented as **profiling of the till feed**, never as if the POS
exported void records it does not have. If your real POS export does carry
actual void/comp/discount events, that is a strictly better signal - wire it
in and this derivation step becomes unnecessary; see
`docs/integrations.md#pos`.

## The hand-off that does not exist yet

In this template, the parent and the child never exchange data: the
Auditor's weekly report reads `fin_daily`, the Pit Boss reads
`pos_sales_daily`, and no flag reaches the owner report's F&B commentary.
The obvious first integration, left for a hotel or their Claude session to
build: feed the week's `total_void_value_eur` and any open high-severity
flags from `tools/fnb_audit.py`'s result into `tools/owner_report.py`'s
F&B callout, so a leaking week shows up in both places with one consistent
number.

## Known limitations (carried over deliberately, not bugs)

- **No server dimension.** The promise says "by server and by shift"; this
  template's data model only has shifts. Add `server_id` to
  `pos_sales_daily.csv` and a corresponding field on `PosSaleRow` to enable
  per-server profiling with staff-anonymised codes (never names) - see
  `docs/safety.md`.
- **Refunds are in the promise, not in the engine.** Only voids, comps and
  discounts are modeled; comps and discounts are flat percentages, never
  individually flagged.
- **Menu-item performance tracking is promised, not built.** `pos_items`
  carries item, price and venue; nothing in this template ranks items by
  margin or void rate yet.
- **Flags do not persist.** Each run produces flags fresh from the data;
  there is no acknowledge/investigate/dismiss lifecycle and no way to see
  whether a flagged pattern recurred. A `fin_pos_flags` table with a status
  column is the natural next step if you want that history.
- **The baseline includes the outliers it is trying to find.** `baseline`
  and `sigma` are computed over the whole window, so a sustained leak
  inflates its own baseline and can hide. A trimmed mean or a clean
  reference period is a defensible improvement if voids run persistently
  high.
