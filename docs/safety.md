# Guardrails and safety

This agent touches your finances - the ledger, the bank, Stripe, the PMS
folio. Everything below is built in, not optional.

## The hard boundary

**"Reports and flags; never edits the books or the PMS itself."** That is
the roster's own promise, and it is enforced in code, not just in prose:

- The Weekly Owner Report and the F&B Sales Audit never write anywhere. They
  compute a result and queue a draft.
- The Weekly Income Audit's `reconcile()` function writes nothing at all,
  ever - running it is always safe to re-run, on demand, as often as you
  like.
- Only the five reconciliation action buttons write anything, and only after
  a human names which one - see `docs/how-it-works.md` "Reusing the review
  FSM for actions".
- The FX auto-post that the source system this is built from performs
  automatically is **not** automatic here, even when the tolerance rule
  explains the difference. A human always presses `post_fx`. See "Design
  decisions" #4 in `docs/how-it-works.md`.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | Every report and digest is drafted and queued, never sent - a blocked send leaves the item `approved` (not `failed`), so it is ready to go the moment you flip to live. Every reconciliation action is recorded (the item moves to `approved`, then the write attempt fails with the guard's own message and the item lands on `failed`, resumable with the same action once live) but nothing reaches the PMS or Stripe. |
| `live` | An **approved** report or digest is actually emailed the next time `python3 tools/review.py send` runs. An **approved** reconciliation action actually attempts its write. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it
back to `shadow` stops every outbound send and every reconciliation write
immediately, mid-schedule, with no other change. `config/agent.yaml` can be
stricter, never looser.

Two more brakes:

- `python3 tools/run.py --once --dry-run` computes every job. No business
  data is written: no queued item, no `fin_runs` row, no cursor update, and
  the CSV importers are skipped so nothing in `data/agent.db` changes. The
  one exception is the run log - `core.log.Run`'s own `runs` row, written on
  every pass in this family, dry-run or not - which the run log records as a
  `dry_run` entry (`stats_json` carries `"dry_run": true`), not as agent
  data. Safe to run repeatedly on a fresh clone.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions
  that need a human even in live mode: `send_email`, `send_message`,
  `pms_write`, `payment`, `publish`. Shortening it is how you hand the agent
  more rope, one action at a time - there is no reason to for this agent.

Every outbound action goes through one function,
`core/review.py:assert_write_allowed`. There is no second path, and
`tools/recon.py`'s five actions call it exactly like an email send does.

## What this agent will never do

- Auto-post a number to the ledger. Every posting is a human-pressed button.
- Guess an allocation. An unidentified bank credit stays unallocated and
  names the near-misses it declined to use, rather than matching to the
  closest amount.
- Accuse a person. The F&B Sales Audit states a deviation from that outlet's
  own baseline (a multiple and a standard-deviation count), never an
  allegation, and never names a server - there is no server dimension in
  this template's data model.
- Treat "two of three systems agree" as an error, when it is the honest
  answer. Cash, OTA virtual cards and in-transit takings are labeled "not
  applicable" or "pending", never as a mismatch.
- Offer a fix for a chargeback. A dispute is routed to `open_dispute`
  (logged only) because it is a case to argue, not a number to reconcile.
- Send while `mode: shadow`, or send an item nobody approved.

## Data handling

**What leaves this machine.** With `llm.provider: anthropic` or
`claude-code`, the *only* thing that ever reaches a model is the cosmetic
controller's note prompt - a short JSON summary (period, headline,
counts), never a raw bank line, card number or guest email. See
`prompts/finance-note.md`. With `mock` or `interactive`, nothing leaves the
machine at all - and `interactive` is skipped entirely for this note rather
than pausing a run for a one-liner (`docs/how-it-works.md`).

**What is stored.** `data/agent.db` (SQLite): the ledger, the reconciliation
window, the till feed, every queued item and every event. `data/logs/*.jsonl`.
`data/exports/`. All gitignored. No cloud service, no telemetry.

**Card data.** This agent only ever sees a card's last four digits and the
brand, already provided that way by the payment processor's export - it
never receives or stores a full card number, so `core/redact.py`'s PAN
detector should never have anything to catch here. If you extend an
adapter to pull raw card data, redact it before it reaches `data/agent.db`.

**Retention.** `privacy.retention_days` (default 365) controls how long
processed items stay in the database.

## GDPR and confidentiality, in practice

This agent's data is financial, not primarily guest-personal - but folio
charges and Stripe payments do carry guest names and emails.

- **You are the controller.** This runs on your machine, under your control.
  TH1 does not receive it.
- **Your model provider is a processor**, only for the controller's note.
  Check their data processing terms if that matters for your register.
- **Minimise what reaches the note prompt.** It is already limited to
  aggregate figures - do not extend it to include raw guest rows.
- **Right to erasure.** A guest's card or booking details staying in a
  closed-out reconciliation item is a normal accounting retention need, not
  a live processing purpose - but if asked, ask your Claude session:
  *"Delete every item in data/agent.db whose payload mentions this guest
  email, and tell me how many rows you removed."*

This is a practical summary, not legal advice.

## Telling people this was AI-prepared

The EU AI Act (Article 50) requires telling a person when they are
interacting with an AI system, unless it is obvious. This agent does not
interact with a person in real time and does not talk to guests at all - its
output goes to your own owners and outlet managers as a periodic report,
which is a different situation from a guest-facing chat reply. It is still
good practice to say plainly that the report was prepared automatically, so
`knowledge/signature.md` carries a line to that effect by default:

> Prepared automatically from the daily ledger and the bank, Stripe and PMS
> records, reviewed by our team before it was sent.

If you ever extend this agent to message a guest directly (for example, a
guest-facing payment query), add a guest-facing disclosure line before doing
so, along the lines of: *"This message was prepared with AI assistance and
reviewed by our team. Reply any time to reach a person directly."* Keep an
escape hatch in it - a guest who wants a human should never have to work out
how to get one.

## Subscription or API: an honest note

**Your Claude Code subscription** (`llm.provider: claude-code` or
`interactive`). Flat monthly cost. For this agent specifically, the volume
is tiny - one controller's note per job, a few times a week - so this is
almost certainly the right choice, and `interactive` costs nothing extra at
all since the note call is skipped rather than made.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token. There is
no realistic volume reason to need this for a reporting agent that makes at
most a handful of calls a week; consider it only if you also run several
other agents through the same API key and want one consistent path.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`.
   Every outbound send and every reconciliation write stops on the next
   pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>-*.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
