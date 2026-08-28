# Workflow: F&B Sales Audit ("the Pit Boss") - sub-agent

Objective: profile every outlet's till feed shift by shift, flag the shifts
whose voids break from that outlet's own baseline, and send each outlet
manager a daily exceptions digest with the transactions attached.

Off by default. Turn it on once you have a real POS export - see
`docs/sub-agents.md` for what it adds and why it stays optional.

## Turning it on

1. In `config/agent.yaml`:
   ```yaml
   subagents:
     fnb_sales_audit:
       enabled: true
   fnb_audit:
     outlets:
       - {name: "Salt Restaurant", manager_email: "salt-manager@example.com"}
   ```
2. Export your POS till feed to `data/imports/pos_sales_daily.csv` (and
   `pos_items.csv` for prices) - see `docs/integrations.md#pos`.
3. `make doctor` - "F&B Sales Audit" turns `ok` once outlets are configured
   and the CSV is present.

## Steps

1. **Run it.**
   ```bash
   python3 tools/run.py --once --fnb
   ```
   `--fnb` forces this job; otherwise it runs nightly per
   `config/agent.yaml: schedule.fnb_audit` once enabled.

2. **Read the honest-derivation note first.** The demo POS feed has no void,
   comp or discount columns - almost no export does. `tools/fnb_audit.py`
   *derives* a till-level profile from units, revenue and covers, and says so
   in the digest. It is profiling, never a claim that the POS reported voids
   it does not have. See the module docstring and `docs/sub-agents.md`.

3. **See the digest.**
   ```bash
   python3 tools/review.py list --kind fnb_digest
   python3 tools/review.py show <id>
   ```
   Each flag names the multiple of baseline, the standard deviations out, the
   voided value, and the fact that the day's item count came in below what
   its covers imply - never an accusation, never a name.

4. **Decide and send**, exactly like the owner report:
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```

5. **The toggle.** Turn `fnb_audit.rules.pos-void-watch` off and re-run: the
   digest still shows the profile, but says plainly that nothing was
   compared against a baseline and "voids at 4x baseline would go unseen."

## Rules

- "It flags; you decide." Every alert carries the underlying shift's
  transactions in `draft`; the engine never names a person - there is no
  server dimension in this template (see `docs/sub-agents.md` for how to add
  one).
- Flags are not persisted beyond the run's `fin_runs` snapshot - there is no
  acknowledge/dismiss state in v1. See "Design decisions" #5 in
  `docs/how-it-works.md`.
- The parent's owner report does not read this sub-agent's flags. Wiring
  `total_void_value_eur` into the owner report's F&B commentary is the
  documented first integration - see `docs/sub-agents.md`.
