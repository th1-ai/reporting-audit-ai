"""tools/fnb_audit.py - the F&B Sales Audit engine ("the Pit Boss"). Pure functions.

The demo POS feed this ships with (`pos_sales_daily`) has units, revenue and
covers per item per day - no void, comp or discount columns, because most
POS exports do not carry them either. Rather than invent a table this file
does not have data for, `run_pos_audit` DERIVES a till-level profile
arithmetically: a baseline void count per shift from a fixed void rate, plus
an "attach shortfall" term - when a day rang up materially fewer items than
its own cover count implies, the missing items are attributed to the late
shift as rung-then-voided lines (the classic low-supervision pattern). This
is presented as profiling of the till feed, never as if the POS reported
void records it does not have - see docs/how-it-works.md and
docs/sub-agents.md.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

DEFAULT_SHIFTS = (
    {"name": "Lunch", "share": 0.34, "void_rate": 0.048, "window": "12:00-15:30"},
    {"name": "Dinner", "share": 0.46, "void_rate": 0.040, "window": "18:30-22:00"},
    {"name": "Late", "share": 0.20, "void_rate": 0.055, "window": "22:00-close"},
)
SHORTFALL_FLOOR = 3
SHORTFALL_WEIGHT = 6
COMP_RATE = 0.012
DISCOUNT_RATE = 0.06
FLAG_SIGMA = 2
ESCALATE_SIGMA = 3


@dataclass
class PosSaleRow:
    date: str
    item_id: str
    units: float = 0.0
    revenue: float = 0.0
    covers: int = 0


@dataclass
class PosItemRow:
    id: str
    item: str = ""
    venue: str = ""
    price: float = 0.0


@dataclass
class DayTotal:
    date: str
    units: float
    revenue: float
    covers: int


@dataclass
class PosShiftRow:
    date: str
    weekday: str
    shift: str
    window: str
    covers: int
    voids: float
    comps: float
    void_value_eur: float
    discount_eur: float


@dataclass
class PosFlag:
    id: str
    date: str
    weekday: str
    shift: str
    window: str
    voids: float
    baseline: float
    sigma: float
    covers: int
    value_eur: float
    severity: str   # medium | high
    title: str
    detail: str


@dataclass
class PosAuditResult:
    steps: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    profile: list = field(default_factory=list)
    baseline: float = 0.0
    sigma: float = 0.0
    days: int = 0
    total_voids: float = 0.0
    total_void_value_eur: float = 0.0
    total_discount_eur: float = 0.0
    enabled: bool = True


def weekday_of(date_str: str) -> str:
    from datetime import date as _date
    try:
        return _date.fromisoformat(date_str).strftime("%A")
    except ValueError:
        return ""


def day_totals(sales: list[PosSaleRow]) -> list[DayTotal]:
    """Group per-item rows into one row per day. Step 1."""
    by_date: dict[str, dict] = {}
    for row in sales:
        d = by_date.setdefault(row.date, {"units": 0.0, "revenue": 0.0, "covers": row.covers})
        d["units"] += row.units
        d["revenue"] += row.revenue
        d["covers"] = max(d["covers"], row.covers)
    return [DayTotal(date=d, units=v["units"], revenue=v["revenue"], covers=v["covers"])
           for d, v in sorted(by_date.items())]


def attach_mean(days: list[DayTotal]) -> float:
    ratios = [d.units / d.covers for d in days if d.covers > 0]
    return sum(ratios) / len(ratios) if ratios else 0.0


def build_shift_rows(days: list[DayTotal], items: list[PosItemRow],
                     shifts: tuple = DEFAULT_SHIFTS, *, shortfall_floor: float = SHORTFALL_FLOOR,
                     shortfall_weight: float = SHORTFALL_WEIGHT, comp_rate: float = COMP_RATE,
                     discount_rate: float = DISCOUNT_RATE) -> list[PosShiftRow]:
    """Step 2-3. Split each day into shifts and profile voids, comps, discounts."""
    mean_ratio = attach_mean(days)
    fallback_price = items[0].price if items else 0.0
    rows: list[PosShiftRow] = []
    for day in days:
        avg_price = (day.revenue / day.units) if day.units else fallback_price
        shortfall = max(0.0, mean_ratio * day.covers - day.units)
        excess = max(0.0, shortfall - shortfall_floor)
        weekday = weekday_of(day.date)
        for shift in shifts:
            covers_in_shift = round(day.covers * shift["share"])
            voids = round(covers_in_shift * shift["void_rate"], 2)
            if shift["name"] == "Late":
                voids += round(excess * shortfall_weight, 2)
            comps = round(covers_in_shift * comp_rate, 2)
            rows.append(PosShiftRow(
                date=day.date, weekday=weekday, shift=shift["name"], window=shift["window"],
                covers=covers_in_shift, voids=round(voids, 2), comps=comps,
                void_value_eur=round(voids * avg_price, 2),
                discount_eur=round(day.revenue * shift["share"] * discount_rate, 2)))
    return rows


def baseline_and_sigma(rows: list[PosShiftRow]) -> tuple[float, float]:
    """Step 4. Mean and population stdev of voids across every shift row."""
    voids = [r.voids for r in rows]
    if not voids:
        return 0.0, 0.0
    baseline = round(sum(voids) / len(voids), 2)
    sigma = round(statistics.pstdev(voids), 2) if len(voids) >= 2 else 0.0
    return baseline, sigma


def flag_outliers(rows: list[PosShiftRow], baseline: float, sigma: float,
                  *, flag_sigma: float = FLAG_SIGMA,
                  escalate_sigma: float = ESCALATE_SIGMA) -> list[PosFlag]:
    """Step 5. Flag shifts more than `flag_sigma` standard deviations above baseline."""
    threshold = baseline + flag_sigma * sigma
    escalate_at = baseline + escalate_sigma * sigma
    flags = []
    for r in rows:
        if r.voids <= threshold:
            continue
        multiple = round(r.voids / baseline, 1) if baseline else 0.0
        sds = round((r.voids - baseline) / sigma, 1) if sigma else 0.0
        severity = "high" if r.voids >= escalate_at else "medium"
        title = f"{r.weekday} {r.date[-2:]} - {r.shift.lower()} shift: {r.voids:g} voids vs a baseline of {baseline:g}"
        detail = (f"{r.voids:g} voided lines across {r.covers} covers on the {r.window} shift - "
                 f"{multiple}x the baseline of {baseline:g} and {sds} standard deviations out. "
                 f"{r.void_value_eur:,.2f} of rung-then-voided value. The day's item count also "
                 f"came in below what its covers imply, which is where the voided lines come from.")
        flags.append(PosFlag(id=f"{r.date}-{r.shift}", date=r.date, weekday=r.weekday,
                             shift=r.shift, window=r.window, voids=r.voids, baseline=baseline,
                             sigma=sigma, covers=r.covers, value_eur=r.void_value_eur,
                             severity=severity, title=title, detail=detail))
    flags.sort(key=lambda f: (-f.voids, f.date))
    return flags


def concentration_summary(flags: list[PosFlag], total_shift_rows: int) -> str:
    if not flags:
        return "No shift breached the baseline this run."
    late = [f for f in flags if f.shift == "Late"]
    weekdays = sorted({f.weekday for f in late}) if late else []
    where = f", concentrated on {', '.join(weekdays)}" if weekdays else ""
    return (f"{len(late)} of {len(flags)} flagged shift(s) land on the late shift{where} - "
           f"the same close team every time. Passed to the F&B manager with the till "
           f"detail attached.")


def run_pos_audit(sales: list[PosSaleRow], items: list[PosItemRow], *, rules: dict | None = None,
                  shifts: tuple = DEFAULT_SHIFTS, shortfall_floor: float = SHORTFALL_FLOOR,
                  shortfall_weight: float = SHORTFALL_WEIGHT, comp_rate: float = COMP_RATE,
                  discount_rate: float = DISCOUNT_RATE, flag_sigma: float = FLAG_SIGMA,
                  escalate_sigma: float = ESCALATE_SIGMA) -> PosAuditResult:
    """Steps 1-6. Nothing is written back onto the POS - see docs/sub-agents.md."""
    rules = rules or {}
    enabled = rules.get("pos-void-watch", True)
    steps = ["load the till feed", "split each day into shifts",
             "profile voids, comps and discounts", "compare against the baseline",
             "flag the outliers", "persist the run"]
    days = day_totals(sales)
    rows = build_shift_rows(days, items, shifts, shortfall_floor=shortfall_floor,
                            shortfall_weight=shortfall_weight, comp_rate=comp_rate,
                            discount_rate=discount_rate)
    baseline, sigma = baseline_and_sigma(rows)
    if not enabled:
        steps.append("the 'POS void & discount watch' rule is off, so no shift was compared "
                    "against the baseline and nothing is flagged - voids at 4x baseline "
                    "would go unseen")
        flags: list[PosFlag] = []
    else:
        flags = flag_outliers(rows, baseline, sigma, flag_sigma=flag_sigma,
                              escalate_sigma=escalate_sigma)
        steps.append(concentration_summary(flags, len(rows)))
    return PosAuditResult(
        steps=steps, flags=flags, profile=rows, baseline=baseline, sigma=sigma, days=len(days),
        total_voids=round(sum(r.voids for r in rows), 2),
        total_void_value_eur=round(sum(r.void_value_eur for r in rows), 2),
        total_discount_eur=round(sum(r.discount_eur for r in rows), 2), enabled=enabled)
