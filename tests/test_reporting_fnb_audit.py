"""tools/fnb_audit.py - pure engine tests against the bundled till-feed
fixture (the F&B Sales Audit sub-agent, "the Pit Boss")."""

from __future__ import annotations

import json
from pathlib import Path

import fnb_audit as pb

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "inbound"


def _load():
    sales = [pb.PosSaleRow(**r) for r in json.loads((FIXTURES / "pos_sales_daily.json").read_text())]
    items = [pb.PosItemRow(**r) for r in json.loads((FIXTURES / "pos_items.json").read_text())]
    return sales, items


def test_day_totals_groups_five_items_into_one_row_per_day():
    sales, _ = _load()
    days = pb.day_totals(sales)
    assert len(days) == 14
    assert all(d.covers > 0 for d in days)


def test_shift_rows_are_three_per_day_and_ordinary_days_stay_near_the_floor():
    sales, items = _load()
    days = pb.day_totals(sales)
    rows = pb.build_shift_rows(days, items)
    assert len(rows) == 14 * 3
    ordinary = [r for r in rows if r.date != "2026-08-11"]
    # an ordinary day's late-shift voids should be close to covers * void_rate,
    # not inflated by the shortfall term
    late = [r for r in ordinary if r.shift == "Late"]
    assert all(r.voids < 20 for r in late)


def test_the_shortfall_day_flags_high_severity_on_the_late_shift():
    sales, items = _load()
    result = pb.run_pos_audit(sales, items, rules={"pos-void-watch": True})
    assert len(result.flags) >= 1
    flag = result.flags[0]
    assert flag.date == "2026-08-11"
    assert flag.shift == "Late"
    assert flag.severity == "high"
    assert "below what its covers imply" in flag.detail


def test_ordinary_shortfall_under_the_floor_does_not_amplify_voids():
    days = [pb.DayTotal(date="2026-01-01", units=100.0, revenue=1000.0, covers=50)]
    # mean ratio close to 2.0/cover, so a day at exactly that ratio has ~0 shortfall
    rows = pb.build_shift_rows(days * 5 + [pb.DayTotal(date="2026-01-06", units=99.0,
                                                        revenue=990.0, covers=50)],
                               [], shortfall_floor=3, shortfall_weight=6)
    late_last_day = [r for r in rows if r.date == "2026-01-06" and r.shift == "Late"][0]
    # shortfall of ~2 items is below the floor of 3, so no amplification
    assert late_last_day.voids < 5


def test_toggle_off_produces_no_flags_and_says_so():
    sales, items = _load()
    result = pb.run_pos_audit(sales, items, rules={"pos-void-watch": False})
    assert result.flags == []
    assert not result.enabled
    assert "4x baseline" in result.steps[-1]
    # the profile itself (voids per shift row) is unchanged by the toggle
    on = pb.run_pos_audit(sales, items, rules={"pos-void-watch": True})
    assert result.profile == on.profile


def test_concentration_summary_names_the_late_shift():
    sales, items = _load()
    result = pb.run_pos_audit(sales, items, rules={"pos-void-watch": True})
    assert "late shift" in result.steps[-1]
