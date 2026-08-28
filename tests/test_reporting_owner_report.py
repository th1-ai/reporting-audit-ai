"""tools/owner_report.py - pure engine tests. No settings, no store, no I/O
beyond reading the bundled fixture."""

from __future__ import annotations

import json

import owner_report as om


def _load_fixture():
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "fixtures" / "inbound" / "financial_daily.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [om.FinDay(**r) for r in rows]


def test_deltapct_returns_zero_on_a_zero_base():
    assert om.deltapct(100.0, 0.0) == 0.0
    assert om.deltapct(0.0, 0.0) == 0.0


def test_deltapct_ordinary_case():
    assert om.deltapct(110.0, 100.0) == 10.0
    assert om.deltapct(90.0, 100.0) == -10.0


def test_full_report_from_the_bundled_fixture_has_three_callouts_and_no_warnings():
    rows = _load_fixture()
    result = om.build_report(rows)
    assert result.days == 7
    assert result.prior_days == 7
    assert len(result.callouts) == 3
    assert result.warnings == []
    # the fixture is built with F&B down and payroll up week over week
    titles = " ".join(c.title for c in result.callouts).lower()
    assert "f&b" in titles
    assert "payroll" in titles
    assert "margin" in titles


def test_short_history_warns_instead_of_crashing():
    rows = _load_fixture()[:3]
    result = om.build_report(rows, min_history_days=7)
    assert result.days == 3
    assert any("history" in w for w in result.warnings)
    assert any("prior" in w for w in result.warnings)


def test_biggest_revenue_mover_picks_the_largest_absolute_swing_and_tags_tone():
    week = om.LineTotals(by_line={"Rooms": 5000.0, "F&B": 3000.0}, total=8000.0)
    prior = om.LineTotals(by_line={"Rooms": 4900.0, "F&B": 4200.0}, total=9100.0)
    callout = om.biggest_revenue_mover(week, prior)
    assert "F&B" in callout.title
    assert callout.tone == "warn"   # F&B fell


def test_cost_line_to_watch_is_the_largest_adverse_signed_move():
    week = om.LineTotals(by_line={"Payroll": 4000.0, "Marketing": 300.0}, total=4300.0)
    prior = om.LineTotals(by_line={"Payroll": 3500.0, "Marketing": 500.0}, total=4000.0)
    callout = om.cost_line_to_watch(week, prior, 10000.0, 9500.0)
    assert "Payroll" in callout.title
    assert callout.tone == "warn"


def test_render_email_body_carries_the_standing_line_and_hotel_name():
    rows = _load_fixture()
    result = om.build_report(rows)
    body = om.render_email_body("Hotel Aurora", result)
    assert "Hotel Aurora" in body
    assert "no estimates, no roll-forwards" in body
    assert result.period_label in body
