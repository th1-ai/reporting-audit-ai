# Connecting your systems

Every connector here is one of three things, and the table says which.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: CSV, IMAP/SMTP, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working right now:

```bash
make doctor
```

## What this agent actually reads and writes

Unlike most agents in this family, the three main jobs do **not** read a live
PMS, mailbox or POS API directly. `fin_daily`, the reconciliation window
(bank/Stripe/PMS charges) and the till feed are bulk, periodic exports -
that is how a finance team actually works with them - so they arrive as CSV
files in `data/imports/` and are loaded into `data/agent.db` by
`tools/store_ext.py`, not through `core/adapters`. The `core/adapters`
registry is used for exactly two things: **sending** the owner report and
the F&B digest (Email), and the **four writing** reconciliation actions
(PMS, Payments). See `docs/how-it-works.md` for the full data model.

### The CSV exports (universal - always works, start here)

<a id="reconciliation"></a>
<a id="pos"></a>

| File | Feeds | Columns |
|---|---|---|
| `data/imports/financial_daily.csv` | Weekly Owner Report | `date, revenue_rooms, revenue_fnb, revenue_spa, revenue_other, costs_payroll, costs_utilities, costs_fnb, costs_marketing, costs_other, occupancy_pct, adr, revpar` |
| `data/imports/web_traffic.csv` | Owner report's web traffic section (optional) | `date, sessions, users` - a GA4/Google Ads export, or any analytics export with those columns |
| `data/imports/bank_statement.csv` | Weekly Income Audit | `date, description, reference, amount, balance, kind` (`kind`: `payout \| chargeback \| bank_fee \| cash \| terminal \| payable \| credit`) |
| `data/imports/stripe_payments.csv` | Weekly Income Audit | `date, created_time, amount, fee, net, status, customer_name, customer_email, card_last4, description, payout_ref, dispute_ref, refunds_ref` |
| `data/imports/pms_charges.csv` | Weekly Income Audit | `date, reservation_ref, guest_name, guest_email, amount, method, channel, room, card_last4` |
| `data/imports/pos_sales_daily.csv` | F&B Sales Audit (sub-agent) | `date, item_id, units, revenue, covers` |
| `data/imports/pos_items.csv` | F&B Sales Audit (sub-agent) | `id, item, venue, price` |

Headers are matched case-insensitively; `amount`/`amount_eur` and
`fee`/`fee_eur` both work. `financial_daily.csv` **accumulates** - each
import adds or updates rows by date, so a weekly export just keeps
extending the ledger. The three reconciliation files are a **rolling
snapshot** - each import replaces the previous window, with `day_offset`
computed from the latest date in `bank_statement.csv` (see
`tools/store_ext.py:import_recon_window_csv`).

**You do not run a separate import command.** `tools/run.py` re-imports
every file above automatically, right before the matching job reads the
table - `store_ext.import_financial_daily_csv()` before the owner report,
`store_ext.import_recon_window_csv()` before the income audit,
`store_ext.import_pos_csv()` before the F&B audit. Save the export over the
old file in `data/imports/` and the next `python3 tools/run.py --once
--report` / `--recon` / `--fnb` picks it up - `make doctor` also runs these
same loaders (against a throwaway copy, never your real database) so it can
tell you the exact row count it found, not just that the file exists.
`--dry-run` skips the import step along with every other write, so a dry
run always reflects whatever was imported on the last real pass.

Bank statements usually arrive as a PDF from the bank; OCR-ing that PDF into
`bank_statement.csv` is outside this template's scope - ask your Claude
session to write a small OCR step for your bank's statement layout, or use
whatever export your bank offers instead of the PDF.

### Email - `systems.email.adapter`

<a id="email"></a>

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Writes to `data/exports/sent_email.jsonl`. What `make demo` uses. |
| `imap` | universal | mailbox + app password | Any provider. **Start here for real sends.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

This agent only ever calls `send()` - it never reads a mailbox.

**`imap` (start here).** In `.env`:

```
EMAIL_ADDRESS=finance@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
```

**`gmail` setup (about ten minutes, once).** Choose this over `imap` when
you want Gmail labels, threads as Gmail understands them, or you are on
Google Workspace with IMAP disabled by policy.

1. In [Google Cloud Console](https://console.cloud.google.com/) create a
   project and enable the **Gmail API**.
2. Configure the OAuth consent screen (**Internal** if you are on Google
   Workspace, **External** + your own address as a test user otherwise).
3. Create an OAuth client of type **Desktop app** and download the JSON.
4. Save it as `credentials.json` in this repo's root folder (it is
   gitignored - never commit it).
5. Install the client libraries:
   ```bash
   .venv/bin/pip install google-api-python-client google-auth-oauthlib
   ```
6. Set `systems.email.adapter: gmail` in `config/hotel.yaml`, then run
   `make doctor`. The first run opens a browser once for you to sign in and
   writes `token.json` next to `credentials.json`. After that it refreshes
   silently - you will not be asked again.

Scopes requested (least privilege that still lets the agent send and, if you
keep the default, add labels):

| Scope | Lets the agent |
|---|---|
| `gmail.readonly` | read messages and threads |
| `gmail.send` | send, including replies |
| `gmail.modify` | mark read, add labels |

If you would rather the agent never touch labels, drop `gmail.modify` from
`SCOPES` in `core/adapters/email_gmail.py` - `mark_read`/`label` then report
as unavailable, which is honest and harmless; sending still works.

Both adapters end the same way: the signature (`knowledge/signature.md`) is
appended to every send automatically - see `Email.with_signature()` in
`core/adapters/base.py`.

### PMS - `systems.pms.adapter` (write-only here)

<a id="pms"></a>

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | `add_note()` is a no-op that logs. |
| `csv` | universal | nothing extra | `add_note()` appends to `data/exports/pms_writes.csv` for you to apply by hand. |
| `cloudbeds` | built | OAuth app + refresh token | `add_note()` really posts to the reservation. |

The only PMS call in this repo is the `post_folio` reconciliation action,
which calls `add_note(reservation_ref, ...)`. It never reads reservations
through this adapter - folio charges come from `pms_charges.csv` above.

### Payments (Stripe) - stub

| System | Status | Notes |
|---|---|---|
| Payments | stub | `link_customer`, `refund_duplicate` and `charge_balance` all raise "not built yet" until you implement a Stripe adapter. |

Stripe payment data for the reconciliation comes from `stripe_payments.csv`
above, not a live API call - that keeps the read side working on day one.
The three actions that *write* to Stripe (repoint a customer, refund a
duplicate, charge an outstanding balance) are genuinely a live-API job. See
"Implement your own" below; `core/adapters/base.py`'s `Payments` class is the
shape to extend, and `refund_duplicate` already calls `payments.refund(...)`
so implementing that one method unblocks it immediately.

### Messaging, Sheets - not used by the core loop

<a id="messaging"></a>
<a id="sheets"></a>

`systems.messaging` and `systems.sheets` are configured (mock by default)
because `make doctor` checks every adapter, but neither is called by
`tools/run.py`, `tools/review.py` or `tools/recon.py` in v1. Two obvious,
easy extensions if you want them:

- **Messaging** - a WhatsApp/Slack ping to the finance team when the
  reconciliation headline shows a high "in question" figure. `messaging_webhook`
  is the zero-setup way to try it.
- **Sheets** - append each week's owner-report headline to a running
  spreadsheet (`get_sheets(settings).append("owner_report_log", [...])`), so
  the report is versioned somewhere besides email.

Ask your Claude session to wire either of these in - both are a few lines in
`tools/run.py`, guarded the same way every other write in this repo is.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `procurement`, `locks` and
`courier` are stubs, unused by this agent.

## Implement your own

<a id="implement-your-own"></a>

Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a Payments adapter for Stripe. I have `STRIPE_API_KEY` in `.env`.
> Copy the shape of `core/adapters/pms_cloudbeds.py`, implement `ping`,
> `capabilities`, `list_charges` and `refund` first (all four actions in
> `tools/recon.py` route through these two methods and `AdapterNotImplemented`
> for the rest), register it in `core/adapters/__init__.py`'s `payments`
> family, and stop so I can check `make doctor` before you add `link_customer`
> or `charge_balance` as new guarded methods.

### The five steps

**1. Copy the closest existing adapter.** `core/adapters/pms_cloudbeds.py` for
a real API with OAuth; `core/adapters/pms_csv.py` for the "logs a to-do list"
honest-write shape if you would rather stage changes for a human first.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

**3. Implement the reads**, if any - `list_charges(date_from, date_to)` for
Payments. Put fields you do not map into a dict on the result rather than
dropping them.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("payment")
def refund(self, charge_id: str, amount: float) -> dict:
    ...
```

Not optional - without it the adapter can write while the agent is in shadow
mode, which defeats the whole safety model.

**5. Register it.** One line in `core/adapters/__init__.py`'s registry
table, then `systems.payments...` is not a real config key today (Payments
has no `systems.` entry - it is reached through `core.adapters.get_stub`).
Ask your Claude session to add a `get_payments()` accessor alongside
`get_pms`/`get_email` if you want it configurable the same way; until then,
`tools/recon.py:_apply_write` already calls `get_stub("payments", settings)`,
so a new class registered under `STUBS["payments"]` in
`core/adapters/domain_stub.py` is picked up with no other code change.

### Rules that matter

- **`ping()` never raises.**
- **Every write is decorated with `@guarded_write`.** No exceptions.
- **Never log a credential.**
- **Write a test.** Copy one of `tests/test_reporting_recon.py`'s cases:
  feed a fixture in, check the `ReconItem`/`Payout` that comes out - no
  network needed for the engine tests, and a small adapter test can use
  `unittest.mock` for the HTTP layer.

### `core/` is shared

`core/` is identical in all 28 agents in this family. A hotel-specific tweak
belongs in `tools/` or your own adapter file, never in `core/`.
