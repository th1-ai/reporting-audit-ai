# Workflow: shadow to live

Objective: decide, together with the hotel, whether Reporting & Audit AI is
ready to actually email the owner report and the F&B digest, and let
approved reconciliation actions actually reach the PMS or Stripe - and make
the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly what
changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `WARN` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name and contacts, and
      `knowledge/property.md`, `faq.md` and `signature.md` exist and are
      accurate.
- [ ] At least a few real `make run` passes have gone through the review
      queue - not just `make demo`'s fixtures.
- [ ] A real mailbox is connected (`systems.email.adapter: imap` or
      `gmail`) and `make doctor` shows it healthy - going live on `mock`
      would only ever touch the fixtures.
- [ ] `report.recipients` (or `contacts.manager.email`) and, if the F&B
      sub-agent is on, every outlet's `manager_email`, are real addresses.
- [ ] The hotel understands that the four writing reconciliation actions
      (`link_customer`, `post_folio`, `refund_duplicate`, `charge_balance`)
      will raise "not built yet" until a real Payments/PMS adapter is wired
      up - see `docs/integrations.md`. Going live does not make those work by
      itself; it only lets an approved action attempt the write instead of
      being blocked outright.
- [ ] `python3 tools/review.py stale` has been run, so nothing approved
      during shadow goes out the moment mode flips.
- [ ] `python3 tools/review.py list` (no `--demo`) shows only items from real
      `make run` passes — `make demo` runs in its own database
      (`data/demo/demo.db`) and never reaches the real queue, but if this
      working copy is old enough to predate that isolation, run `make clean`
      first to wipe local runtime state (database, logs, exports; `.env` and
      `config/` are untouched) and re-run the real passes above.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email`, `pms_write` and
   `payment` by default - it should. Going live means **approved items get
   sent or applied**, not that anything starts happening automatically.
3. Run `make doctor` again to confirm.
4. Watch one send go through end to end:
   ```bash
   python3 tools/run.py --once --report
   python3 tools/review.py list --kind owner_report
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
5. Tell the hotel exactly what just changed: an approved report or digest now
   actually leaves the mailbox the next time `python3 tools/review.py send`
   runs (by hand or on the schedule); an approved reconciliation action now
   actually attempts its write instead of being blocked. Nothing is
   automatic before that approval.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound send and every reconciliation write on the next pass,
mid-schedule, with no other change required.
