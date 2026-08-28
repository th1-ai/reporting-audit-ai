# Workflow: working the review queue

Objective: turn a queued report, digest, or reconciliation exception into a
decision, and, once approved, actually send or apply it.

Nothing leaves the building without this. `mode: shadow` blocks every write
except an item you have explicitly approved or edited - see `docs/safety.md`.

## Two different queues, one command to see them

```bash
python3 tools/review.py list                         # everything waiting
python3 tools/review.py list --kind owner_report      # just the Monday report
python3 tools/review.py list --kind fnb_digest        # just the F&B digests
python3 tools/review.py list --kind recon_item --status needs_human
python3 tools/review.py show <id>
```

**Communications** (`owner_report`, `fnb_digest`) are drafts to approve, edit
or reject, then send - see `workflows/10-owner-report.md` and
`workflows/20-fnb-sales-audit.md`.

**Reconciliation exceptions** (`recon_item`) are not messages - each one has
an *action* to apply instead, via `python3 tools/recon.py action <id> <name>`.
Do not try to `approve`/`send` a `recon_item` through this tool; `send` will
refuse it and tell you to use `recon.py` - see `workflows/15-income-audit.md`.

## Deciding on a communication

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
python3 tools/review.py reject <id> --reason "wrong tone"
```

`edit` records the before/after pair as a `learnings` row for the record,
even though this agent has no coach layer to learn from it automatically.

## Sending

```bash
python3 tools/review.py send
```

Claims everything `approved`/`edited` (both kinds together), calls the email
adapter, and records the result. In `mode: shadow` this only ever works for
nothing - shadow blocks every send regardless of approval; see
`docs/safety.md` for exactly why that is not a bug. A shadow-mode block is
**not** a failure: the item goes straight back to `approved` (never `failed`),
so nothing needs re-approving - the next `python3 tools/review.py send` once
`mode: live` is set just sends it.

## A failed send

```bash
python3 tools/review.py retry <id>
```

re-queues it after the cause is fixed - usually a missing recipient
(`report.recipients` / `fnb_audit.outlets[].manager_email` in
`config/agent.yaml`) or a mailbox credential (`make doctor` will say which).
A shadow-mode block never reaches `failed`, so `retry` is for a real error,
not for waiting out shadow mode.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
- `python3 tools/review.py stale` is the go-live step that clears everything
  approved during shadow mode - it was recorded but never sent, and is
  probably out of date by then.
