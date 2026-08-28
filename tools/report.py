#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --since 2026-08-01

Reads `fin_runs` (one row per report/recon/pos run) and the `items` queue -
no recomputation, just what actually happened. This is the evidence behind
the roster's ROI line: "-95% Weekly reporting hours" and, for the Pit Boss,
"+3% F&B margin recovered" - see docs/benefits.md for how to read it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store  # noqa: E402


def runs_by_kind(store, since: str | None) -> dict:
    sql = "SELECT * FROM fin_runs"
    params: list = []
    if since:
        sql += " WHERE created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at ASC"
    out: dict[str, list] = {"report": [], "recon": [], "pos": []}
    for row in store.db.execute(sql, params).fetchall():
        kind = row["kind"]
        out.setdefault(kind, []).append({
            "created_at": row["created_at"],
            "stats": json.loads(row["stats_json"] or "{}"),
            "narrative": row["narrative"],
        })
    return out


def edit_rate(store) -> tuple[int, int]:
    """(edited count, sent+auto_sent count) across owner_report and fnb_digest items."""
    edited = store.db.execute(
        "SELECT COUNT(*) AS n FROM items WHERE kind IN ('owner_report','fnb_digest') "
        "AND review_status IN ('edited','sent')").fetchone()
    total_sent = store.db.execute(
        "SELECT COUNT(*) AS n FROM items WHERE kind IN ('owner_report','fnb_digest') "
        "AND review_status IN ('edited','sent','auto_sent')").fetchone()
    was_edited = store.db.execute(
        "SELECT COUNT(DISTINCT item_id) AS n FROM events WHERE action='status:edited'").fetchone()
    return (was_edited["n"] if was_edited else 0, total_sent["n"] if total_sent else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=None, help="ISO date/time - only runs on or after this")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        runs = runs_by_kind(store, args.since)
        print(f"Reporting & Audit AI - activity report{f' since {args.since}' if args.since else ''}")
        print("=" * 60)

        reports = runs.get("report", [])
        print(f"\nWeekly Owner Report: {len(reports)} run(s)")
        for r in reports[-3:]:
            s = r["stats"]
            print(f"  {r['created_at']}  {s.get('period', '?')}  revenue {s.get('revenue', 0):,.2f}"
                 f"  GOP margin {s.get('gop_margin_pct', 0)}%")

        recons = runs.get("recon", [])
        print(f"\nWeekly Income Audit: {len(recons)} run(s)")
        for r in recons[-3:]:
            print(f"  {r['created_at']}  {r['stats'].get('headline', '')}")

        pos_runs = runs.get("pos", [])
        print(f"\nF&B Sales Audit: {len(pos_runs)} run(s)")
        for r in pos_runs[-3:]:
            s = r["stats"]
            print(f"  {r['created_at']}  {s.get('flags', 0)} flag(s), "
                 f"{s.get('total_void_value_eur', 0):,.2f} voided value")

        edited, sent = edit_rate(store)
        pct = round(edited / sent * 100, 1) if sent else 0.0
        print(f"\nHuman edit rate on reports/digests: {edited}/{sent} ({pct}%)")

        recon_counts = store.db.execute(
            "SELECT review_status, COUNT(*) AS n FROM items WHERE kind='recon_item' "
            "GROUP BY review_status").fetchall()
        if recon_counts:
            print("\nReconciliation items by status:")
            for row in recon_counts:
                print(f"  {row['review_status']}: {row['n']}")

        usage = store.usage_totals(since=args.since)
        print(f"\nLLM usage (controller's note only): {usage['calls']} call(s), "
             f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens, "
             f"${usage['cost_usd']:.4f}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
