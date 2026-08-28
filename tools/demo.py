#!/usr/bin/env python3
"""tools/demo.py - one full cycle on the bundled fixtures. No credentials needed.

    make demo
    python3 tools/demo.py

Seeds `fixtures/inbound/*.json` into its own database (`data/demo/demo.db`),
never `data/agent.db`, then runs all three jobs - the Weekly Owner Report,
the Weekly Income Audit, and the F&B Sales Audit (forced on for the demo,
regardless of `subagents.fnb_sales_audit.enabled`, so you can see every part
of the family in one pass) - with `load_settings(demo=True)`: mock LLM
provider, shadow mode, mock adapters, whatever config/hotel.yaml says.
Nothing leaves this machine, and a hotel that runs `make demo` before
connecting real data never gets a fixture draft mixed into (or deduping
against) its real queue - see docs/how-it-works.md, "Idempotency".
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings, sub_data_dir  # noqa: E402
from core.log import Run, summary_line  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402
from run import run_fnb_audit, run_income_audit, run_owner_report  # noqa: E402


def main() -> int:
    settings = load_settings(demo=True)
    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    try:
        store_ext.migrate(store)
        seeded = store_ext.seed_fixtures(store, REPO_ROOT / "fixtures" / "inbound")
        print("Seeded fixtures:")
        for table, n in seeded.items():
            print(f"  {table}: {n} row(s)")
        print()

        stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0}
        with Run("demo", settings, store) as run:
            drafted = run_owner_report(settings, store, provider=None, dry_run=False)
            stats["processed"] += 1
            stats["drafted"] += drafted
            print(f"Weekly Owner Report: {'drafted' if drafted else 'already drafted'}, "
                 f"queued for review.\n")

            attention = run_income_audit(settings, store, provider=None, dry_run=False)
            stats["processed"] += 1
            stats["needs_human"] += attention
            print()

            drafted_fnb = run_fnb_audit(settings, store, provider=None, dry_run=False)
            stats["processed"] += 1
            stats["drafted"] += drafted_fnb
            print(f"F&B Sales Audit: {drafted_fnb} digest(s) queued for review "
                 f"(forced on for the demo).\n")
            run.stats = dict(stats)

        summary = queue_summary(store)
        print("Review queue:")
        for status, count in sorted(summary["by_status"].items()):
            print(f"  {status}: {count}")
        print(f"\n{summary['waiting_on_human']} item(s) waiting on a human. "
             f"Run `make review ARGS=\"--demo\"` to see them (this demo always runs "
             f"in its own database, data/demo/demo.db - your real queue in "
             f"data/agent.db is untouched; plain `make review` works that one).\n")

        print(summary_line(stats, settings.mode))
        print("DEMO OK")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
