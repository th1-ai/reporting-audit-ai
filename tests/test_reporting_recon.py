"""tools/recon.py - pure engine tests against the bundled reconciliation
fixtures. No settings, no store."""

from __future__ import annotations

import json
from pathlib import Path

import recon

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "inbound"
RULES = {"recon-fuzzy-identity": True, "recon-tolerance": True,
        "recon-ota-virtual": True, "recon-settlement-window": True}


def _load():
    bank = [recon.BankLine(**r) for r in json.loads((FIXTURES / "bank_lines.json").read_text())]
    stripe = [recon.StripePayment(**r)
             for r in json.loads((FIXTURES / "stripe_payments.json").read_text())]
    charges = [recon.PmsCharge(**r)
              for r in json.loads((FIXTURES / "pms_charges.json").read_text())]
    return bank, stripe, charges


def test_payout_decomposition_ties_to_the_bank_line():
    bank, stripe, _ = _load()
    payouts = recon.decompose_payouts(bank, stripe)
    by_ref = {p.payout_ref: p for p in payouts}
    assert by_ref["PO-1001"].ties is True
    assert by_ref["PO-1002"].ties is True
    # gross - fees == net, to the cent, for both payouts
    for p in payouts:
        assert round(p.gross - p.fees, 2) == round(p.net, 2)


def test_duplicate_detection_flags_only_the_later_charge_within_the_window():
    _, stripe, _ = _load()
    dupes = recon.detect_duplicates(stripe, window_minutes=15)
    assert dupes == {"sp-3"}   # sp-2 is the original, sp-3 the retry 5 minutes later


def test_match_ladder_rungs_in_order_folio_then_email_then_card():
    bank, stripe, charges = _load()
    by_id = {c.id: c for c in charges}
    # pc-1 matches sp-1 by folio reference in the Stripe description
    matched, rung = recon.match_ladder(by_id["pc-1"], stripe, RULES)
    assert rung == "folio" and matched.id == "sp-1"
    # pc-3 (AUR-20138) has no folio ref in any description and a different
    # payer email, so it only matches on rung 3 (card + date + amount)
    matched, rung = recon.match_ladder(by_id["pc-3"], stripe, RULES)
    assert rung == "card" and matched.id == "sp-4"


def test_fuzzy_identity_toggle_off_splits_one_match_into_two_exceptions():
    bank, stripe, charges = _load()
    on = recon.reconcile(bank, stripe, charges, RULES)
    off_rules = dict(RULES, **{"recon-fuzzy-identity": False})
    off = recon.reconcile(bank, stripe, charges, off_rules)
    identity_items = [i for i in on.items if i.kind == "identity"]
    assert len(identity_items) == 1
    unmatched_when_off = [i for i in off.items
                          if i.kind in ("unmatched_charge", "no_folio")
                          and i.amount_eur == 2924.0]
    assert len(unmatched_when_off) == 2   # the same EUR 2,924 counted twice


def test_fx_within_tolerance_is_explained_and_posts_to_the_configured_gl():
    bank, stripe, charges = _load()
    result = recon.reconcile(bank, stripe, charges, RULES)
    fx_items = [i for i in result.items if i.kind == "fx"]
    assert len(fx_items) == 1
    assert fx_items[0].severity == "explained"
    assert fx_items[0].action is None   # auto-explained, no human action needed


def test_fx_tolerance_off_turns_the_same_difference_into_an_attention_item():
    bank, stripe, charges = _load()
    off_rules = dict(RULES, **{"recon-tolerance": False})
    result = recon.reconcile(bank, stripe, charges, off_rules)
    fx_items = [i for i in result.items if i.kind == "fx"]
    assert fx_items[0].severity == "attention"
    assert fx_items[0].action == "post_fx"


def test_unmatched_bank_credit_names_near_misses_and_never_guesses():
    bank, stripe, charges = _load()
    result = recon.reconcile(bank, stripe, charges, RULES)
    unmatched = [i for i in result.items if i.kind == "unmatched_bank"]
    assert len(unmatched) == 1
    assert unmatched[0].action == "ask_front_office"
    assert "near-miss" in unmatched[0].detail


def test_summary_headline_matches_the_item_counts():
    bank, stripe, charges = _load()
    result = recon.reconcile(bank, stripe, charges, RULES)
    clean = sum(1 for i in result.items if i.severity == "clean")
    explained = sum(1 for i in result.items if i.severity == "explained")
    attention = sum(1 for i in result.items if i.severity == "attention")
    assert f"{clean + explained} of {len(result.items)} reconciled" in result.headline
    assert f"{attention} need(s) a human" in result.headline
    assert attention > 0
