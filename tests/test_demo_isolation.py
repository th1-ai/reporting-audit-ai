"""Regression test: `make demo` must never touch the real database.

Before this fix, `tools/demo.py` opened `Store(settings)` - the same
`data/agent.db` a real `make run` pass uses - so a hotel that ran `make demo`
before connecting real data got fixture drafts mixed into (and deduping
against, via `store.upsert_unique`) its real queue. See
factory/reports/reporting-audit-ai-fix1.md, "Fix pass 2 (demo isolation)".

Uses `isolated_settings` from conftest.py so this never reads or writes this
repo's own `config/` or `data/` - both tests below run inside one temp repo
root (`AGENT_REPO_ROOT`), so "run the demo, then connect real data" is
exercised in the same sandbox a hotel would actually see.
"""

from __future__ import annotations

import store_ext
from core.config import data_dir, sub_data_dir
from core.store import Store
from run import run_fnb_audit, run_income_audit, run_owner_report

import demo as demo_module


def test_demo_seeds_its_own_database_never_the_real_one(isolated_settings):
    isolated_settings()  # sets AGENT_CONFIG_DIR / AGENT_REPO_ROOT for this sandbox

    assert demo_module.main() == 0

    demo_db = sub_data_dir("demo") / "demo.db"
    real_db = data_dir() / "agent.db"
    assert demo_db.exists(), "make demo must seed data/demo/demo.db"
    assert not real_db.exists(), "make demo must never create/touch data/agent.db"


def test_real_pass_after_demo_has_no_demo_items_and_matches_a_from_scratch_run(
        isolated_settings, fixtures_dir):
    isolated_settings()
    assert demo_module.main() == 0   # a hotel running `make demo` first, as the README tells them to

    real_settings = isolated_settings(provider="mock", mode="shadow")
    real_store = Store(real_settings)   # the default path: data/agent.db
    try:
        store_ext.migrate(real_store)
        assert real_store.list_items(limit=100) == [], \
            "the real queue must start empty - nothing leaked from the demo pass"
        assert store_ext.load_fin_daily(real_store) == [], \
            "the real fin_daily table must start empty - no demo fixture rows leaked"

        # Connect real data exactly as workflows/00-setup.md walks a hotel
        # through, using the same bundled fixtures a from-scratch real run
        # uses (test_reporting_run.py's test_all_three_jobs_draft_something_on_first_run)
        # - the point being the numbers below are identical either way.
        store_ext.seed_fixtures(real_store, fixtures_dir)
        assert run_owner_report(real_settings, real_store, provider=None, dry_run=False) == 1
        attention = run_income_audit(real_settings, real_store, provider=None, dry_run=False)
        assert attention > 0
        assert run_fnb_audit(real_settings, real_store, provider=None, dry_run=False) == 1

        items = real_store.list_items(limit=100)
        kinds = {i.kind for i in items}
        assert {"owner_report", "recon_item", "fnb_digest"} <= kinds
        assert len(real_store.list_items(kind="owner_report", limit=10)) == 1, \
            "no extra owner_report item - the demo pass did not dedupe against this one"
    finally:
        real_store.close()

    # And the demo's own database is untouched by the real pass - still there,
    # still whatever `make demo` wrote, isolated in both directions.
    demo_db = sub_data_dir("demo") / "demo.db"
    assert demo_db.exists()
