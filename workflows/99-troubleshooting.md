# Workflow: troubleshooting

Read the whole error before doing anything - every tool here says what broke
and what to do about it.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`llm provider`: ...** Only affects the cosmetic controller's note - see
  `docs/how-it-works.md`. Nothing else in this agent calls a model.
- **An adapter shows FAIL, not WARN.** `universal`/`built` adapters fail loud
  when misconfigured (`WARN` is reserved for stubs). Read the `detail`
  column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` calls `load_settings(demo=True)`, which forces the mock
  LLM provider and mock adapters regardless of `config/hotel.yaml` - if it
  still fails, the bug is in the fixtures or the engines, not your config.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose.

## A job says "nothing to report" / "nothing to reconcile" / "nothing to audit"

The matching table is empty. Import the CSV `docs/integrations.md` names for
that job into `data/imports/`, or run `make demo` first to see it work on
the bundled fixtures.

## `python3 tools/recon.py action <id> <name>` says "blocked"

Expected in `mode: shadow` - the item moves to `failed` with the reason
attached. Re-run the exact same command once mode is live; `failed ->
approved` is a legal retry, so nothing is lost. See "Reusing the review FSM
for actions" in `docs/how-it-works.md`.

## `python3 tools/recon.py action <id> <name>` says "not built yet"

The Payments (or PMS) adapter for that action is a stub - see
`docs/integrations.md#implement-your-own`. Ask your Claude session to write
it for your system, then run `make doctor` to check it, then re-run the same
action; it resumes from `failed`.

## `python3 tools/review.py send` skips an item saying "wrong tool for kind"

A `recon_item` reached the send queue by accident (it should not - see
`docs/how-it-works.md`). Use `python3 tools/recon.py action <id> <name>`
instead; this send attempt marks it `failed` so it is easy to spot and no
data is lost.

## An item is stuck at `sending`

A process died mid-send. Every job's next pass calls
`core.store.Store.reap_stuck_sending()`, which moves anything stuck for more
than 30 minutes to `failed`. Use `python3 tools/review.py retry <id>` once
the cause is fixed.

## The numbers look wrong

Every callout and every reconciliation verdict names the threshold or rung
that produced it - re-read `python3 tools/review.py show <id>` in full before
assuming a bug. If the arithmetic really is wrong, `tests/test_reporting_*.py`
is where to add a regression case before changing the engine.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision, in order, with a run id.
`python3 tools/review.py show <id>` has the full event trail for one item.
If neither explains it, that is a real bug - describe exactly what you ran
and what you expected, and ask.
