"""tools/owner_report.py - the Weekly Owner Report engine. Pure functions only.

No I/O here: everything takes plain data in and returns dataclasses out, so it
is trivial to test and safe to call from tools/run.py, tools/demo.py or a test.
Every number is arithmetic over `FinDay` rows - the LLM never touches one.
See docs/how-it-works.md for the design and the callout rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

REVENUE_FIELDS = ("revenue_rooms", "revenue_fnb", "revenue_spa", "revenue_other")
COST_FIELDS = ("costs_payroll", "costs_utilities", "costs_fnb", "costs_marketing",
              "costs_other")
REVENUE_LABELS = {"revenue_rooms": "Rooms", "revenue_fnb": "F&B",
                  "revenue_spa": "Spa", "revenue_other": "Other"}
COST_LABELS = {"costs_payroll": "Payroll", "costs_utilities": "Utilities",
              "costs_fnb": "F&B cost", "costs_marketing": "Marketing",
              "costs_other": "Other"}


@dataclass
class FinDay:
    """One row of the daily ledger. All money in the hotel's own currency."""

    date: str
    revenue_rooms: float = 0.0
    revenue_fnb: float = 0.0
    revenue_spa: float = 0.0
    revenue_other: float = 0.0
    costs_payroll: float = 0.0
    costs_utilities: float = 0.0
    costs_fnb: float = 0.0
    costs_marketing: float = 0.0
    costs_other: float = 0.0
    occupancy_pct: float = 0.0
    adr: float = 0.0
    revpar: float = 0.0


@dataclass
class LineTotals:
    by_line: dict = field(default_factory=dict)
    total: float = 0.0


@dataclass
class Callout:
    title: str
    tone: str      # success | warn | neutral
    text: str


@dataclass
class WebTraffic:
    sessions: float = 0.0
    sessions_prior: float = 0.0
    connected: bool = False


@dataclass
class OwnerReportResult:
    period_label: str
    prior_period_label: str
    days: int
    prior_days: int
    revenue: LineTotals
    revenue_prior: LineTotals
    revenue_delta_pct: float
    costs: LineTotals
    costs_prior: LineTotals
    gop: float
    gop_prior: float
    gop_margin_pct: float
    gop_margin_prior_pct: float
    cost_ratio: float
    cost_ratio_prior: float
    occupancy: float
    occupancy_prior: float
    adr: float
    adr_prior: float
    revpar: float
    revpar_prior: float
    callouts: list = field(default_factory=list)
    web_traffic: WebTraffic | None = None
    standing_line: str = ("Every figure below is taken straight from the daily "
                          "ledger - no estimates, no roll-forwards.")
    warnings: list = field(default_factory=list)


def deltapct(now: float, before: float) -> float:
    """Percent change. Returns 0 rather than NaN/Infinity on a zero base."""
    if not before:
        return 0.0
    return round((now - before) / before * 100, 1)


def period_label(rows: list[FinDay]) -> str:
    if not rows:
        return ""
    start = rows[0].date
    end = rows[-1].date

    def short(d: str) -> str:
        from datetime import date as _date
        try:
            dt = _date.fromisoformat(d)
            return f"{dt.day} {dt.strftime('%b')}"
        except ValueError:
            return d
    return f"{short(start)} - {short(end)}"


def totals_of(rows: list[FinDay], fields: tuple, labels: dict) -> LineTotals:
    by_line = {labels[f]: round(sum(getattr(r, f) for r in rows), 2) for f in fields}
    return LineTotals(by_line=by_line, total=round(sum(by_line.values()), 2))


def mean_of(rows: list[FinDay], field_name: str) -> float:
    if not rows:
        return 0.0
    return round(sum(getattr(r, field_name) for r in rows) / len(rows), 2)


def biggest_revenue_mover(week: LineTotals, prior: LineTotals) -> Callout:
    """Largest absolute swing across the revenue lines, by EUR."""
    best_line, best_now, best_before, best_swing = "", 0.0, 0.0, -1.0
    for line, now in week.by_line.items():
        before = prior.by_line.get(line, 0.0)
        swing = abs(now - before)
        if swing > best_swing:
            best_line, best_now, best_before, best_swing = line, now, before, swing
    pct = deltapct(best_now, best_before)
    tone = "success" if best_now >= best_before else "warn"
    share = round(best_now / week.total * 100, 1) if week.total else 0.0
    direction = "up" if best_now >= best_before else "down"
    text = (f"{best_line} moved the week - it came in at {best_now:,.2f} against "
           f"{best_before:,.2f}, a swing of {best_swing:,.2f} ({direction} {abs(pct)}%) "
           f"and the largest single movement on the revenue side. It accounts for "
           f"{share}% of total revenue this week.")
    return Callout(title=f"{best_line} moved the week", tone=tone, text=text)


def cost_line_to_watch(week: LineTotals, prior: LineTotals, week_revenue: float,
                       prior_revenue: float) -> Callout:
    """The line with the largest *adverse* (upward) movement, signed."""
    worst_line, worst_now, worst_before, worst_move = "", 0.0, 0.0, float("-inf")
    for line, now in week.by_line.items():
        before = prior.by_line.get(line, 0.0)
        move = now - before
        if move > worst_move:
            worst_line, worst_now, worst_before, worst_move = line, now, before, move
    share_now = round(worst_now / week_revenue * 100, 1) if week_revenue else 0.0
    share_before = round(worst_before / prior_revenue * 100, 1) if prior_revenue else 0.0
    tone = "warn" if worst_move > 0 else "success"
    text = (f"{worst_line} moved {worst_move:+,.2f} week over week, from "
           f"{share_before}% of revenue to {share_now}% of revenue.")
    return Callout(title=f"{worst_line} is the cost line to watch", tone=tone, text=text)


def gop_margin_callout(gop_margin: float, prior_margin: float, cost_ratio: float,
                       cost_ratio_prior: float) -> Callout:
    diff = round(gop_margin - prior_margin, 1)
    tone = "success" if diff >= 0 else "warn"
    verb = "held" if diff >= 0 else "slipped"
    text = (f"Gross operating margin {verb} {abs(diff)} points, from {prior_margin}% to "
           f"{gop_margin}%. Costs absorbed {cost_ratio}% of revenue this week against "
           f"{cost_ratio_prior}% last week.")
    return Callout(title=f"Gross operating margin {verb}", tone=tone, text=text)


def build_report(rows: list["FinDay"], *, web_traffic: WebTraffic | None = None,
                 min_history_days: int = 7) -> OwnerReportResult:
    """Build the full owner report from ascending daily rows.

    Uses the last 14 rows: `week = rows[-7:]`, `prior = rows[-14:-7]`. With
    fewer than 14 rows the prior period is whatever is left (possibly empty),
    and a warning is attached rather than raising - see docs/how-it-works.md
    "Design decisions" #7.
    """
    warnings: list[str] = []
    if len(rows) < min_history_days:
        warnings.append(
            f"only {len(rows)} day(s) of history - the report below is partial. "
            f"Load at least {min_history_days} days into fin_daily for a full week.")
    week = rows[-7:]
    prior = rows[-14:-7]
    if len(prior) < len(week):
        warnings.append(
            f"only {len(prior)} prior day(s) available - week-over-week deltas "
            f"compare against a short or empty prior period.")

    revenue = totals_of(week, REVENUE_FIELDS, REVENUE_LABELS)
    revenue_prior = totals_of(prior, REVENUE_FIELDS, REVENUE_LABELS)
    costs = totals_of(week, COST_FIELDS, COST_LABELS)
    costs_prior = totals_of(prior, COST_FIELDS, COST_LABELS)
    gop = round(revenue.total - costs.total, 2)
    gop_prior = round(revenue_prior.total - costs_prior.total, 2)
    gop_margin = round(gop / revenue.total * 100, 1) if revenue.total else 0.0
    gop_margin_prior = round(gop_prior / revenue_prior.total * 100, 1) if revenue_prior.total else 0.0
    cost_ratio = round(costs.total / revenue.total * 100, 1) if revenue.total else 0.0
    cost_ratio_prior = round(costs_prior.total / revenue_prior.total * 100, 1) if revenue_prior.total else 0.0

    callouts = []
    if week and revenue.total:
        callouts.append(biggest_revenue_mover(revenue, revenue_prior))
        callouts.append(cost_line_to_watch(costs, costs_prior, revenue.total, revenue_prior.total))
        callouts.append(gop_margin_callout(gop_margin, gop_margin_prior, cost_ratio, cost_ratio_prior))

    return OwnerReportResult(
        period_label=period_label(week), prior_period_label=period_label(prior),
        days=len(week), prior_days=len(prior),
        revenue=revenue, revenue_prior=revenue_prior,
        revenue_delta_pct=deltapct(revenue.total, revenue_prior.total),
        costs=costs, costs_prior=costs_prior,
        gop=gop, gop_prior=gop_prior,
        gop_margin_pct=gop_margin, gop_margin_prior_pct=gop_margin_prior,
        cost_ratio=cost_ratio, cost_ratio_prior=cost_ratio_prior,
        occupancy=mean_of(week, "occupancy_pct"), occupancy_prior=mean_of(prior, "occupancy_pct"),
        adr=mean_of(week, "adr"), adr_prior=mean_of(prior, "adr"),
        revpar=mean_of(week, "revpar"), revpar_prior=mean_of(prior, "revpar"),
        callouts=callouts, web_traffic=web_traffic, warnings=warnings)


def render_email_body(hotel_name: str, result: OwnerReportResult) -> str:
    """Plain-markdown email body a human can read and send as-is."""
    lines = [f"# {hotel_name} - Weekly Owner Report", "",
             f"**{result.period_label}** (vs prior week {result.prior_period_label})", "",
             result.standing_line, ""]
    for w in result.warnings:
        lines.append(f"> Note: {w}")
    lines += ["", "## Headline",
             f"- Total revenue: {result.revenue.total:,.2f} ({result.revenue_delta_pct:+.1f}%)",
             f"- GOP: {result.gop:,.2f} ({result.gop_margin_pct}% margin)",
             f"- Occupancy: {result.occupancy}% · ADR: {result.adr:,.2f} · RevPAR: {result.revpar:,.2f}",
             "", "## Revenue by line"]
    for line, now in result.revenue.by_line.items():
        before = result.revenue_prior.by_line.get(line, 0.0)
        lines.append(f"- {line}: {now:,.2f} (was {before:,.2f})")
    lines += ["", "## Costs by line"]
    for line, now in result.costs.by_line.items():
        before = result.costs_prior.by_line.get(line, 0.0)
        lines.append(f"- {line}: {now:,.2f} (was {before:,.2f})")
    if result.web_traffic is not None:
        wt = result.web_traffic
        lines += ["", "## Web traffic"]
        if wt.connected:
            lines.append(f"- Sessions: {wt.sessions:,.0f} ({deltapct(wt.sessions, wt.sessions_prior):+.1f}%)")
        else:
            lines.append("- Web traffic is not connected yet - see docs/integrations.md.")
    lines += ["", "## What stood out"]
    for c in result.callouts:
        lines.append(f"- **{c.title}.** {c.text}")
    return "\n".join(lines)
