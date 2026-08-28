"""tools/recon.py `action` command - the five reconciliation actions, and how
they reuse the review FSM (see docs/how-it-works.md)."""

from __future__ import annotations

from types import SimpleNamespace

import store_ext
from recon import cmd_action
from run import run_income_audit


def _args(item_id: str, name: str, note: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=item_id, name=name, note=note)


def _reconciled_store(isolated_settings, store_at, fixtures_dir):
    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    store_ext.seed_fixtures(store, fixtures_dir)
    run_income_audit(settings, store, provider=None, dry_run=False)
    return settings, store


def _first_attention_item_with_action(store, action_name: str):
    for item in store.list_items(kind="recon_item", limit=100):
        if item.review_status == "needs_human" and (item.draft or {}).get("action") == action_name:
            return item
    raise AssertionError(f"no needs_human item with action '{action_name}' in the fixture")


def test_a_write_action_in_shadow_mode_lands_on_failed_not_silently_sent(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _reconciled_store(isolated_settings, store_at, fixtures_dir)
    item = _first_attention_item_with_action(store, "post_folio")
    code = cmd_action(store, settings, _args(item.id, "post_folio"))
    assert code == 0
    updated = store.get_item(item.id)
    assert updated.review_status == "failed"
    assert "shadow" in (updated.error or "")


def test_a_log_only_action_completes_regardless_of_mode(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _reconciled_store(isolated_settings, store_at, fixtures_dir)
    item = _first_attention_item_with_action(store, "ask_front_office")
    code = cmd_action(store, settings, _args(item.id, "ask_front_office", note="checking"))
    assert code == 0
    updated = store.get_item(item.id)
    assert updated.review_status == "sent"


def test_failed_write_action_can_be_retried_by_running_it_again(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _reconciled_store(isolated_settings, store_at, fixtures_dir)
    item = _first_attention_item_with_action(store, "post_folio")
    cmd_action(store, settings, _args(item.id, "post_folio"))
    assert store.get_item(item.id).review_status == "failed"
    # failed -> approved is a legal retry in the core FSM; still shadow, so it
    # fails again rather than raising an IllegalTransition
    code = cmd_action(store, settings, _args(item.id, "post_folio"))
    assert code == 0
    assert store.get_item(item.id).review_status == "failed"


def test_unknown_action_name_is_rejected(isolated_settings, store_at, fixtures_dir):
    settings, store = _reconciled_store(isolated_settings, store_at, fixtures_dir)
    item = _first_attention_item_with_action(store, "post_folio")
    code = cmd_action(store, settings, _args(item.id, "delete_everything"))
    assert code == 2
    assert store.get_item(item.id).review_status == "needs_human"   # untouched


def test_action_on_an_item_not_waiting_for_one_is_rejected(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _reconciled_store(isolated_settings, store_at, fixtures_dir)
    clean_items = [i for i in store.list_items(kind="recon_item", limit=100)
                  if i.review_status == "skipped"]
    assert clean_items, "fixture should have at least one clean/explained item"
    code = cmd_action(store, settings, _args(clean_items[0].id, "post_folio"))
    assert code == 1
