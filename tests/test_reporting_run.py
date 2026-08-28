"""Integration tests for tools/run.py against an isolated settings + store.

Uses `isolated_settings`/`store_at` from conftest.py so nothing here reads a
hotel's own config/hotel.yaml or config/agent.yaml.
"""

from __future__ import annotations

import argparse
import json

import store_ext
from core.review import WriteBlocked, assert_write_allowed, approve
from core.store import Store as CoreStore
from run import one_pass, run_fnb_audit, run_income_audit, run_owner_report
import run as run_module


def _seeded(isolated_settings, store_at, fixtures_dir):
    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    store_ext.seed_fixtures(store, fixtures_dir)
    return settings, store


def _fresh_store(tmp_path, filename, settings, fixtures_dir):
    """A store at its own file (never sharing `store_at`'s fixed `test.db`,
    so two of these in one test are genuinely independent databases),
    seeded with the bundled fixtures."""
    store = CoreStore(settings, path=tmp_path / filename)
    store_ext.migrate(store)
    store_ext.seed_fixtures(store, fixtures_dir)
    return store


def test_all_three_jobs_draft_something_on_first_run(isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    assert run_owner_report(settings, store, provider=None, dry_run=False) == 1
    assert run_income_audit(settings, store, provider=None, dry_run=False) > 0
    assert run_fnb_audit(settings, store, provider=None, dry_run=False) == 1   # default outlet

    kinds = {i.kind for i in store.list_items(limit=100)}
    assert {"owner_report", "recon_item", "fnb_digest"} <= kinds


def test_shadow_mode_blocks_the_send_even_once_approved(isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_owner_report(settings, store, provider=None, dry_run=False)
    item = store.list_items(kind="owner_report", limit=1)[0]
    approve(store, item.id)
    item = store.get_item(item.id)
    try:
        assert_write_allowed(settings, "send_email", item)
        assert False, "expected WriteBlocked in shadow mode"
    except WriteBlocked as exc:
        assert "shadow" in str(exc)


def test_rerunning_the_owner_report_in_the_same_period_does_not_redraft(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    assert run_owner_report(settings, store, provider=None, dry_run=False) == 1
    assert run_owner_report(settings, store, provider=None, dry_run=False) == 0
    assert len(store.list_items(kind="owner_report", limit=10)) == 1


def test_rerunning_the_income_audit_never_duplicates_exceptions(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_income_audit(settings, store, provider=None, dry_run=False)
    first_count = len(store.list_items(kind="recon_item", limit=100))
    run_income_audit(settings, store, provider=None, dry_run=False)
    second_count = len(store.list_items(kind="recon_item", limit=100))
    assert first_count == second_count > 0


def test_dry_run_writes_nothing_at_all(isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_owner_report(settings, store, provider=None, dry_run=True)
    run_income_audit(settings, store, provider=None, dry_run=True)
    run_fnb_audit(settings, store, provider=None, dry_run=True)
    assert store.list_items(limit=100) == []
    n_runs = store.db.execute("SELECT COUNT(*) AS n FROM fin_runs").fetchone()["n"]
    assert n_runs == 0


def test_fnb_digest_defaults_to_one_restaurant_outlet_when_none_configured(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_fnb_audit(settings, store, provider=None, dry_run=False)
    digests = store.list_items(kind="fnb_digest", limit=10)
    assert len(digests) == 1
    assert digests[0].payload.get("outlet") == "Restaurant"
    assert digests[0].review_status == "pending_review"


# --------------------------------------------------------------------------
# SIMULATION.md Finding 1 (BLOCKER): a hotel's own CSV export in
# data/imports/ must actually change what --report / --recon compute, not
# silently leave the fixture rows in place forever.
# --------------------------------------------------------------------------

FINDAILY_HEADER = ("date,revenue_rooms,revenue_fnb,revenue_spa,revenue_other,costs_payroll,"
                   "costs_utilities,costs_fnb,costs_marketing,costs_other,occupancy_pct,"
                   "adr,revpar\n")


def test_custom_financial_daily_csv_visibly_changes_owner_report_output(
        isolated_settings, fixtures_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    (tmp_path / "data" / "imports").mkdir(parents=True)
    settings = isolated_settings(provider="mock", mode="shadow")

    baseline_store = _fresh_store(tmp_path, "baseline.db", settings, fixtures_dir)
    run_owner_report(settings, baseline_store, provider=None, dry_run=False)
    baseline_item = baseline_store.list_items(kind="owner_report", limit=1)[0]
    baseline_revenue = baseline_item.payload["revenue_total"]

    # Dates after the fixture's own 14-day window (2026-08-04..17), so
    # build_report's `week = rows[-7:]` picks this custom data up, not the
    # fixture's - a very different revenue figure so the change is visible.
    rows = "\n".join(f"2026-09-{10 + i:02d},99000,500,100,50,1000,100,100,100,100,95,400,380"
                     for i in range(7))
    (tmp_path / "data" / "imports" / "financial_daily.csv").write_text(
        FINDAILY_HEADER + rows + "\n", encoding="utf-8")

    custom_store = _fresh_store(tmp_path, "custom.db", settings, fixtures_dir)
    run_owner_report(settings, custom_store, provider=None, dry_run=False)
    loaded = store_ext.load_fin_daily(custom_store)

    assert any(r.date.startswith("2026-09-") for r in loaded), (
        "the custom CSV never reached fin_daily - the import wiring regressed")
    custom_item = custom_store.list_items(kind="owner_report", limit=1)[0]
    # the report drafted from the custom data is a different, much larger
    # number than the fixture-seeded "Hotel Aurora" headline, proving the
    # custom CSV - not the fixture - drove the computed report
    assert custom_item.payload["revenue_total"] != baseline_revenue
    assert custom_item.payload["revenue_total"] > 90_000


def test_custom_recon_csvs_visibly_change_income_audit_output(
        isolated_settings, fixtures_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    (tmp_path / "data" / "imports").mkdir(parents=True)
    settings = isolated_settings(provider="mock", mode="shadow")

    baseline_store = _fresh_store(tmp_path, "baseline.db", settings, fixtures_dir)
    run_income_audit(settings, baseline_store, provider=None, dry_run=False)
    baseline_amounts = {i.payload.get("amount_eur")
                        for i in baseline_store.list_items(kind="recon_item", limit=200)}

    # A single unmatched bank credit at a very distinctive amount - no
    # matching Stripe payment or PMS charge, so `reconcile()` must flag it
    # as `unmatched_bank` / `attention`. See tools/recon.py:classify_unmatched_bank_credit.
    imports = tmp_path / "data" / "imports"
    (imports / "bank_statement.csv").write_text(
        "date,description,reference,amount,balance,kind\n"
        "2026-09-15,Unexplained credit,REF-CUSTOM,84210.77,90000,credit\n",
        encoding="utf-8")
    (imports / "stripe_payments.csv").write_text(
        "date,created_time,amount,fee,net,status,customer_name,customer_email,"
        "card_last4,description,payout_ref,dispute_ref,refunds_ref\n", encoding="utf-8")
    (imports / "pms_charges.csv").write_text(
        "date,reservation_ref,guest_name,guest_email,amount,method,channel,room,card_last4\n",
        encoding="utf-8")

    custom_store = _fresh_store(tmp_path, "custom.db", settings, fixtures_dir)
    run_income_audit(settings, custom_store, provider=None, dry_run=False)
    custom_items = custom_store.list_items(kind="recon_item", limit=200)
    custom_amounts = {i.payload.get("amount_eur") for i in custom_items}

    assert custom_amounts != baseline_amounts, (
        "the custom recon CSVs never reached the reconciliation - the import wiring regressed")
    assert any(i.payload.get("amount_eur") == 84210.77 and i.payload.get("kind") == "unmatched_bank"
              for i in custom_items)


def test_dry_run_never_calls_the_csv_importers(
        isolated_settings, fixtures_dir, tmp_path, monkeypatch):
    """--dry-run writes nothing (build-repo.md §5) - including via the CSV
    importers, which do real INSERTs. A CSV sitting in data/imports/ must be
    ignored on a dry-run pass."""
    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    (tmp_path / "data" / "imports").mkdir(parents=True)
    rows = "\n".join(f"2026-09-{10 + i:02d},99000,500,100,50,1000,100,100,100,100,95,400,380"
                     for i in range(7))
    (tmp_path / "data" / "imports" / "financial_daily.csv").write_text(
        FINDAILY_HEADER + rows + "\n", encoding="utf-8")

    settings = isolated_settings(provider="mock", mode="shadow")
    store = _fresh_store(tmp_path, "dryrun.db", settings, fixtures_dir)
    before = len(store_ext.load_fin_daily(store))
    run_owner_report(settings, store, provider=None, dry_run=True)
    after = store_ext.load_fin_daily(store)
    assert len(after) == before, "a dry run must never write an imported row"
    assert not any(r.date.startswith("2026-09-") for r in after)


def test_one_pass_flags_the_core_runs_row_dry_run(isolated_settings, store_at, fixtures_dir):
    """build-repo.md §5: the `runs` observability table may gain a row on a
    dry run, but it must be flagged `dry_run` and no business row may
    appear alongside it."""
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    one_pass(settings, store, provider=None, force_report=True, force_recon=True,
            force_fnb=False, dry_run=True)
    row = store.db.execute(
        "SELECT stats_json FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    assert row is not None, "one_pass must still write the core runs row on a dry run"
    stats = json.loads(row["stats_json"])
    assert stats.get("dry_run") is True
    assert store.list_items(limit=100) == []


# --------------------------------------------------------------------------
# SIMULATION.md core fix: a shadow-blocked send is not a failure - the
# approval stands, so the item returns to `approved`, never `failed`.
# --------------------------------------------------------------------------

def test_shadow_blocked_send_returns_item_to_approved_not_failed(
        isolated_settings, store_at, fixtures_dir):
    from review import cmd_send

    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_owner_report(settings, store, provider=None, dry_run=False)
    item = store.list_items(kind="owner_report", limit=1)[0]
    approve(store, item.id)

    args = argparse.Namespace(limit=20)
    exit_code = cmd_send(store, settings, args)

    assert exit_code == 1   # a blocked send still exits non-zero
    reloaded = store.get_item(item.id)
    assert reloaded.review_status == "approved", (
        "a shadow-mode block must leave the item approved, not failed - "
        "see tools/review.py:cmd_send")


def test_sample_item_shows_marker_in_list_line_and_show(isolated_settings, store_at, capsys):
    """core/store.py tags an item read through a mock adapter outside `make
    demo` as `_sample` (`Item.is_sample`) - a human working the real queue
    must see that at a glance, in both `list` and `show`."""
    from review import _print_item_line, cmd_show

    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    item = store.upsert_item("email", "sample-marker-1", kind="owner_report",
                             payload={"period_label": "Week of 2026-08-24",
                                      "_sample": True})
    assert item.is_sample

    capsys.readouterr()
    _print_item_line(item)
    assert "[SAMPLE DATA]" in capsys.readouterr().out

    rc = cmd_show(store, argparse.Namespace(id=item.id))
    assert rc == 0
    assert "[SAMPLE DATA]" in capsys.readouterr().out
    store.close()
