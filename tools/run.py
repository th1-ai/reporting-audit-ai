#!/usr/bin/env python3
"""tools/run.py - Reporting & Audit AI's main loop.

    python3 tools/run.py --once                 # run whatever is due
    python3 tools/run.py --once --report         # force the weekly owner report
    python3 tools/run.py --once --recon          # force the income audit
    python3 tools/run.py --once --fnb            # force the F&B sales audit (if enabled)
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run

One pass: for each of the three jobs that is due (or forced), pull the data,
run the deterministic engine, queue a draft or a review item, and ask the LLM
for a cosmetic controller's note. Nothing is sent - workflows/80-review.md and
docs/safety.md cover the review queue and the shadow/live switch.

Exit codes: 0 ok, 1 a real error. There is no exit code 3 here: the only LLM
call is cosmetic and is skipped entirely on the `interactive` provider rather
than pausing the run for it - see docs/how-it-works.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, complete  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import store_ext  # noqa: E402
import owner_report  # noqa: E402
import recon  # noqa: E402
import fnb_audit  # noqa: E402

log = get_logger("run")
NOTE_SCHEMA = json.loads((REPO_ROOT / "prompts" / "schemas" / "finance-note.json")
                        .read_text(encoding="utf-8"))
CADENCE_DAYS = {"every-5-min": 5 / 1440, "every-15-min": 15 / 1440, "every-30-min": 30 / 1440,
               "hourly": 1 / 24, "every-4-hours": 4 / 24, "nightly": 1, "morning": 1, "weekly": 7}


def cadence_of(schedule: dict, key: str, default: str) -> str:
    """`schedule.<key>` may be a plain cadence string or a `{cadence, command}` map."""
    value = schedule.get(key, default)
    if isinstance(value, dict):
        return str(value.get("cadence") or value.get("cron") or value.get("every") or default)
    return str(value or default)


def is_due(store, key: str, cadence: str, force: bool) -> bool:
    if force:
        return True
    last = store.get_cursor(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
    return days >= CADENCE_DAYS.get(cadence, 1.0) - 0.01


def get_finance_note(settings, store, item_id: str, summary: dict, *, dry_run: bool,
                     provider: str | None, fixture_id: str) -> str | None:
    """Cosmetic 3-4 sentence note. Never gates a decision - see docs/how-it-works.md.

    Skipped entirely (not paused) on the `interactive` provider, and swallowed
    on any other failure, so a run always completes with or without it.
    """
    effective = provider or settings.llm.provider
    if dry_run or effective in ("interactive",):
        return None
    try:
        prompt = build_prompt("finance-note", settings=settings, item=summary,
                              fixture_id=fixture_id)
        result = complete("finance-note", prompt, schema=NOTE_SCHEMA, settings=settings,
                          provider=provider, store=store, item_id=item_id, effort="low")
        return (result.data or {}).get("note")
    except LLMError as exc:
        log.warn("finance note skipped", error=str(exc)[:200])
        return None


def _now() -> str:
    from core.store import utcnow
    return utcnow()


def record_run(store, kind: str, stats: dict, narrative: str | None) -> None:
    import uuid
    store.db.execute(
        "INSERT INTO fin_runs (id, created_at, kind, stats_json, narrative) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, _now(), kind, json.dumps(stats, ensure_ascii=False), narrative))


def run_owner_report(settings, store, *, provider: str | None, dry_run: bool) -> int:
    """Builds the Monday owner report. Returns 1 if an item was drafted, else 0."""
    imports_dir = REPO_ROOT / "data" / "imports"
    if not dry_run:
        imported = store_ext.import_financial_daily_csv(store, imports_dir / "financial_daily.csv")
        if imported:
            log.info("imported financial_daily.csv", rows=imported)
    rows = store_ext.load_fin_daily(store)
    if not rows:
        log.warn("no fin_daily rows - nothing to report",
                hint="import data/imports/financial_daily.csv, see docs/integrations.md")
        return 0
    web_traffic = None
    csv_path = REPO_ROOT / str(settings.agent_get("report.web_traffic_csv",
                                                   "data/imports/web_traffic.csv"))
    if csv_path.exists():
        import csv as _csv
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            wt_rows = sorted(_csv.DictReader(fh), key=lambda r: r.get("date", ""))
        if wt_rows:
            week = wt_rows[-7:]
            prior = wt_rows[-14:-7]
            web_traffic = owner_report.WebTraffic(
                sessions=sum(float(r.get("sessions", 0) or 0) for r in week),
                sessions_prior=sum(float(r.get("sessions", 0) or 0) for r in prior),
                connected=True)
    min_days = int(settings.agent_get("report.min_history_days", 7))
    result = owner_report.build_report(rows, web_traffic=web_traffic, min_history_days=min_days)

    if dry_run:
        print(f"[dry-run] would draft the owner report for {result.period_label} "
             f"(revenue {result.revenue.total:,.2f}, GOP margin {result.gop_margin_pct}%). "
             f"No business data is written; the run log records a dry_run entry.")
        return 1

    item, created = store.upsert_unique("owner_report", result.period_label,
                                        payload={"period_label": result.period_label,
                                                "revenue_total": result.revenue.total,
                                                "gop": result.gop,
                                                "gop_margin_pct": result.gop_margin_pct})
    if item.draft:
        return 0   # already drafted this period - resumable, not re-done
    body = owner_report.render_email_body(settings.hotel.name, result)
    summary = {"period": result.period_label, "revenue": result.revenue.total,
              "gop": result.gop, "gop_margin_pct": result.gop_margin_pct,
              "callouts": [c.title for c in result.callouts], "warnings": result.warnings}
    note = get_finance_note(settings, store, item.id, summary, dry_run=dry_run,
                            provider=provider, fixture_id="finance-note-report")
    if note:
        body += f"\n\n---\n*{note}*"
    recipients = settings.agent_get("report.recipients", []) or (
        [settings.contacts.manager.get("email")] if settings.contacts.manager.get("email") else [])
    subject = f"{settings.hotel.name} - Weekly Owner Report ({result.period_label})"
    store.set_fields(item.id, draft={"subject": subject, "body_md": body,
                                     "to": recipients, "note": note})
    if item.review_status == "new":
        store.transition(item.id, "pending_review", "agent", {"period": result.period_label})
    store.set_cursor("owner_report", _now())
    record_run(store, "report", summary, note)
    return 1


def run_income_audit(settings, store, *, provider: str | None, dry_run: bool) -> int:
    """Runs the three-way reconciliation. Returns the count of items needing a human."""
    imports_dir = REPO_ROOT / "data" / "imports"
    if not dry_run:
        imported = store_ext.import_recon_window_csv(store, imports_dir)
        if any(imported.values()):
            log.info("imported recon window csvs", **imported)
    bank = store_ext.load_bank_lines(store)
    if not bank:
        log.warn("no bank statement rows - nothing to reconcile",
                hint="import data/imports/bank_statement.csv, see docs/integrations.md")
        return 0
    stripe = store_ext.load_stripe_payments(store)
    charges = store_ext.load_pms_charges(store)
    charges_by_id = {c.id: c for c in charges}
    rules = settings.agent_get("recon.rules", {})
    result = recon.reconcile(
        bank, stripe, charges, rules,
        tolerance_eur=float(settings.agent_get("recon.tolerance_eur", recon.TOLERANCE_EUR)),
        tolerance_pct=float(settings.agent_get("recon.tolerance_pct", recon.TOLERANCE_PCT)),
        settlement_days=int(settings.agent_get("recon.settlement_days", recon.SETTLEMENT_DAYS)),
        near_miss_eur=float(settings.agent_get("recon.near_miss_eur", recon.NEAR_MISS_EUR)))

    if dry_run:
        print(f"[dry-run] {result.headline}. "
             f"No business data is written; the run log records a dry_run entry.")
        return sum(1 for i in result.items if i.severity == "attention")

    attention = 0
    for finding in result.items:
        item, made = store.upsert_unique("recon_item", finding.id,
                                         payload={"kind": finding.kind, "amount_eur": finding.amount_eur})
        if not made:
            continue   # this exception was already raised on a previous run
        pms_id = finding.evidence.get("pms_charge_id")
        payload = {"kind": finding.kind, "amount_eur": finding.amount_eur,
                  "reservation_ref": charges_by_id[pms_id].reservation_ref if pms_id in charges_by_id else "",
                  **finding.evidence}
        store.set_fields(item.id, payload=payload,
                         draft={"title": finding.title, "detail": finding.detail,
                               "action": finding.action, "matched_by": finding.matched_by})
        if finding.severity == "attention":
            store.transition(item.id, "needs_human", "agent",
                             {"kind": finding.kind, "amount_eur": finding.amount_eur})
            attention += 1
        else:
            store.transition(item.id, "skipped", "agent", {"kind": finding.kind})

    note = get_finance_note(settings, store, f"recon:{result.headline}",
                            {"headline": result.headline, "counts": result.counts,
                             "amount_in_question": result.amount_in_question},
                            dry_run=dry_run, provider=provider, fixture_id="finance-note-recon")
    store.set_cursor("income_audit", _now())
    record_run(store, "recon", {"headline": result.headline, **result.counts}, note)
    print(result.headline)
    return attention


def run_fnb_audit(settings, store, *, provider: str | None, dry_run: bool) -> int:
    """Runs the Pit Boss till audit and queues one digest per outlet."""
    imports_dir = REPO_ROOT / "data" / "imports"
    if not dry_run:
        imported = store_ext.import_pos_csv(store, imports_dir)
        if any(imported.values()):
            log.info("imported pos csvs", **imported)
    sales = store_ext.load_pos_sales(store)
    if not sales:
        log.warn("no pos_sales_daily rows - nothing to audit",
                hint="import data/imports/pos_sales_daily.csv, see docs/integrations.md")
        return 0
    items = store_ext.load_pos_items(store)
    rules = settings.agent_get("fnb_audit.rules", {})
    shift_cfg = settings.agent_get("fnb_audit.shifts", None)
    shifts = tuple(shift_cfg) if shift_cfg else fnb_audit.DEFAULT_SHIFTS
    result = fnb_audit.run_pos_audit(
        sales, items, rules=rules, shifts=shifts,
        shortfall_floor=float(settings.agent_get("fnb_audit.shortfall_floor",
                                                  fnb_audit.SHORTFALL_FLOOR)),
        shortfall_weight=float(settings.agent_get("fnb_audit.shortfall_weight",
                                                   fnb_audit.SHORTFALL_WEIGHT)),
        comp_rate=float(settings.agent_get("fnb_audit.comp_rate", fnb_audit.COMP_RATE)),
        discount_rate=float(settings.agent_get("fnb_audit.discount_rate", fnb_audit.DISCOUNT_RATE)),
        flag_sigma=float(settings.agent_get("fnb_audit.flag_sigma", fnb_audit.FLAG_SIGMA)),
        escalate_sigma=float(settings.agent_get("fnb_audit.escalate_sigma",
                                                fnb_audit.ESCALATE_SIGMA)))
    latest_date = sales[-1].date
    outlets = settings.agent_get("fnb_audit.outlets", []) or [
        {"name": "Restaurant", "manager_email": ""}]

    if dry_run:
        print(f"[dry-run] would draft {len(outlets)} digest(s) for {latest_date}: "
             f"{len(result.flags)} flag(s). "
             f"No business data is written; the run log records a dry_run entry.")
        return len(outlets)

    note = get_finance_note(settings, store, f"fnb:{latest_date}",
                            {"days": result.days, "baseline": result.baseline,
                             "sigma": result.sigma, "flags": len(result.flags),
                             "total_void_value_eur": result.total_void_value_eur},
                            dry_run=dry_run, provider=provider, fixture_id="finance-note-pos")

    drafted = 0
    for outlet in outlets:
        name = outlet.get("name", "Restaurant")
        item, created = store.upsert_unique("fnb_digest", f"{name}:{latest_date}",
                                            payload={"outlet": name, "date": latest_date,
                                                    "flags": len(result.flags)})
        if item.draft:
            continue
        lines = [f"# {name} - Till exceptions ({latest_date})", "",
                 f"{len(result.flags)} shift(s) flagged out of {result.days * len(shifts)} "
                 f"profiled. Baseline {result.baseline:g} voids/shift, sigma {result.sigma:g}.",
                 ""]
        if not result.flags:
            lines.append("Nothing crossed the baseline this run." if result.enabled else
                        "The void-watch rule is off - nothing was compared, nothing flagged.")
        for f in result.flags:
            lines += [f"## {f.title}", f.detail, ""]
        lines.append(result.steps[-1])
        body = "\n".join(lines)
        if note:
            body += f"\n\n---\n*{note}*"
        store.set_fields(item.id, draft={
            "subject": f"{name}: {len(result.flags)} till exception(s), {latest_date}",
            "body_md": body, "to": [outlet.get("manager_email")] if outlet.get("manager_email")
            else [], "flags": len(result.flags)})
        if item.review_status == "new":
            store.transition(item.id, "pending_review", "agent", {"outlet": name})
        drafted += 1

    store.set_cursor("fnb_audit", _now())
    record_run(store, "pos", {"days": result.days, "flags": len(result.flags),
                              "total_void_value_eur": result.total_void_value_eur}, note)
    return drafted


def one_pass(settings, store, *, provider: str | None, force_report: bool, force_recon: bool,
            force_fnb: bool, dry_run: bool) -> dict:
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": 0}
    with Run("reporting", settings, store) as run:
        store_ext.migrate(store)
        schedule = settings.agent_get("schedule", {}) or {}

        if is_due(store, "owner_report", cadence_of(schedule, "owner_report", "weekly"), force_report):
            drafted = run_owner_report(settings, store, provider=provider, dry_run=dry_run)
            stats["processed"] += 1
            stats["drafted"] += drafted

        if is_due(store, "income_audit", cadence_of(schedule, "income_audit", "weekly"), force_recon):
            attention = run_income_audit(settings, store, provider=provider, dry_run=dry_run)
            stats["processed"] += 1
            stats["needs_human"] += attention

        fnb_on = bool(settings.agent_get("subagents.fnb_sales_audit.enabled", False))
        if force_fnb and not fnb_on:
            print("F&B Sales Audit is disabled. Enable subagents.fnb_sales_audit.enabled "
                 "in config/agent.yaml first.")
        elif fnb_on and is_due(store, "fnb_audit", cadence_of(schedule, "fnb_audit", "nightly"), force_fnb):
            drafted = run_fnb_audit(settings, store, provider=provider, dry_run=dry_run)
            stats["processed"] += 1
            stats["drafted"] += drafted

        reaped = store.reap_stuck_sending()
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))
        stats["dry_run"] = dry_run
        run.stats = dict(stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run whatever is due (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write no business data, even in live mode")
    parser.add_argument("--report", action="store_true", help="force the weekly owner report")
    parser.add_argument("--recon", action="store_true", help="force the income audit")
    parser.add_argument("--fnb", action="store_true", help="force the F&B sales audit")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 3600)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 3600))
            while True:
                stats = one_pass(settings, store, provider=args.provider,
                                 force_report=args.report, force_recon=args.recon,
                                 force_fnb=args.fnb, dry_run=args.dry_run)
                print(summary_line(stats, settings.mode))
                time.sleep(poll_seconds)
        stats = one_pass(settings, store, provider=args.provider, force_report=args.report,
                         force_recon=args.recon, force_fnb=args.fnb, dry_run=args.dry_run)
        print(summary_line(stats, settings.mode))
        return 0
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
