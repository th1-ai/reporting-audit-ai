"""tools/store_ext.py - this agent's own tables, plus loaders for both the
bundled fixtures (`make demo`, tests) and a hotel's own CSV exports (live).

`migrate(store)` is called once, right after `Store(settings)`, exactly as
`core/store.py` documents. Everything else here is I/O - the pure engines in
tools/owner_report.py, tools/recon.py and tools/fnb_audit.py never touch a
file or a database directly.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from owner_report import FinDay
from recon import BankLine, PmsCharge, StripePayment
from fnb_audit import PosItemRow, PosSaleRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS fin_daily (
  date TEXT PRIMARY KEY, revenue_rooms REAL DEFAULT 0, revenue_fnb REAL DEFAULT 0,
  revenue_spa REAL DEFAULT 0, revenue_other REAL DEFAULT 0, costs_payroll REAL DEFAULT 0,
  costs_utilities REAL DEFAULT 0, costs_fnb REAL DEFAULT 0, costs_marketing REAL DEFAULT 0,
  costs_other REAL DEFAULT 0, occupancy_pct REAL DEFAULT 0, adr REAL DEFAULT 0,
  revpar REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fin_bank_lines (
  id TEXT PRIMARY KEY, day_offset INTEGER NOT NULL, description TEXT, reference TEXT,
  amount_eur REAL DEFAULT 0, balance_eur REAL DEFAULT 0, kind TEXT DEFAULT 'credit'
);
CREATE TABLE IF NOT EXISTS fin_stripe_payments (
  id TEXT PRIMARY KEY, day_offset INTEGER NOT NULL, created_time TEXT DEFAULT '00:00',
  amount_eur REAL DEFAULT 0, fee_eur REAL DEFAULT 0, net_eur REAL DEFAULT 0,
  status TEXT DEFAULT 'succeeded', customer_name TEXT, customer_email TEXT,
  card_last4 TEXT, description TEXT, payout_ref TEXT, dispute_ref TEXT, refunds_ref TEXT
);
CREATE TABLE IF NOT EXISTS fin_pms_charges (
  id TEXT PRIMARY KEY, day_offset INTEGER NOT NULL, reservation_ref TEXT, guest_name TEXT,
  guest_email TEXT, amount_eur REAL DEFAULT 0, method TEXT DEFAULT 'card', channel TEXT,
  room TEXT, card_last4 TEXT
);
CREATE TABLE IF NOT EXISTS pos_sales_daily (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, item_id TEXT NOT NULL, units REAL DEFAULT 0,
  revenue REAL DEFAULT 0, covers INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pos_items (
  id TEXT PRIMARY KEY, item TEXT, venue TEXT, price REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fin_runs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
  stats_json TEXT, narrative TEXT
);
"""


def migrate(store) -> None:
    store.migrate(SCHEMA)


def _count(store, table: str) -> int:
    row = store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return row["n"] if row else 0


def _row_get(row: dict, *names: str, default=""):
    norm = {"".join(ch for ch in k.lower() if ch.isalnum()): v for k, v in row.items() if k}
    for name in names:
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        if key in norm and norm[key] not in (None, ""):
            return norm[key]
    return default


def _num(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return default


def _int(value, default=0) -> int:
    try:
        return int(float(str(value) or default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# fixtures (make demo, tests) - JSON in, tables filled once
# --------------------------------------------------------------------------
def seed_fixtures(store, fixtures_dir: Path) -> dict:
    """Load the bundled fixtures into the tables above, if they are empty.

    Safe to call on every run: each table is only filled the first time, so a
    hotel's own imported data is never overwritten by re-running `make demo`.
    """
    loaded = {}
    plan = [
        ("fin_daily", "financial_daily.json", _insert_fin_daily),
        ("fin_bank_lines", "bank_lines.json", _insert_bank_lines),
        ("fin_stripe_payments", "stripe_payments.json", _insert_stripe_payments),
        ("fin_pms_charges", "pms_charges.json", _insert_pms_charges),
        ("pos_sales_daily", "pos_sales_daily.json", _insert_pos_sales),
        ("pos_items", "pos_items.json", _insert_pos_items),
    ]
    for table, filename, inserter in plan:
        if _count(store, table) > 0:
            loaded[table] = _count(store, table)
            continue
        path = fixtures_dir / filename
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        inserter(store, rows)
        loaded[table] = len(rows)
    return loaded


def _insert_fin_daily(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO fin_daily (date, revenue_rooms, revenue_fnb, revenue_spa, "
            "revenue_other, costs_payroll, costs_utilities, costs_fnb, costs_marketing, "
            "costs_other, occupancy_pct, adr, revpar) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["date"], r.get("revenue_rooms", 0), r.get("revenue_fnb", 0),
             r.get("revenue_spa", 0), r.get("revenue_other", 0), r.get("costs_payroll", 0),
             r.get("costs_utilities", 0), r.get("costs_fnb", 0), r.get("costs_marketing", 0),
             r.get("costs_other", 0), r.get("occupancy_pct", 0), r.get("adr", 0),
             r.get("revpar", 0)))


def _insert_bank_lines(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO fin_bank_lines (id, day_offset, description, reference, "
            "amount_eur, balance_eur, kind) VALUES (?,?,?,?,?,?,?)",
            (r["id"], r["day_offset"], r.get("description", ""), r.get("reference", ""),
             r.get("amount_eur", 0), r.get("balance_eur", 0), r.get("kind", "credit")))


def _insert_stripe_payments(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO fin_stripe_payments (id, day_offset, created_time, "
            "amount_eur, fee_eur, net_eur, status, customer_name, customer_email, card_last4, "
            "description, payout_ref, dispute_ref, refunds_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["day_offset"], r.get("created_time", "00:00"), r.get("amount_eur", 0),
             r.get("fee_eur", 0), r.get("net_eur", 0), r.get("status", "succeeded"),
             r.get("customer_name", ""), r.get("customer_email", ""), r.get("card_last4", ""),
             r.get("description", ""), r.get("payout_ref", ""), r.get("dispute_ref", ""),
             r.get("refunds_ref", "")))


def _insert_pms_charges(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO fin_pms_charges (id, day_offset, reservation_ref, "
            "guest_name, guest_email, amount_eur, method, channel, room, card_last4) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["day_offset"], r.get("reservation_ref", ""), r.get("guest_name", ""),
             r.get("guest_email", ""), r.get("amount_eur", 0), r.get("method", "card"),
             r.get("channel", ""), r.get("room", ""), r.get("card_last4", "")))


def _insert_pos_sales(store, rows: list[dict]) -> None:
    for r in rows:
        row_id = r.get("id") or f"{r['date']}:{r['item_id']}"
        store.db.execute(
            "INSERT OR IGNORE INTO pos_sales_daily (id, date, item_id, units, revenue, covers) "
            "VALUES (?,?,?,?,?,?)",
            (row_id, r["date"], r["item_id"], r.get("units", 0), r.get("revenue", 0),
             r.get("covers", 0)))


def _insert_pos_items(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute("INSERT OR IGNORE INTO pos_items (id, item, venue, price) "
                        "VALUES (?,?,?,?)",
                        (r["id"], r.get("item", ""), r.get("venue", ""), r.get("price", 0)))


# --------------------------------------------------------------------------
# loaders: table rows -> engine dataclasses (tools/run.py uses these)
# --------------------------------------------------------------------------
def load_fin_daily(store) -> list[FinDay]:
    rows = store.db.execute("SELECT * FROM fin_daily ORDER BY date ASC").fetchall()
    return [FinDay(date=r["date"], revenue_rooms=r["revenue_rooms"], revenue_fnb=r["revenue_fnb"],
                   revenue_spa=r["revenue_spa"], revenue_other=r["revenue_other"],
                   costs_payroll=r["costs_payroll"], costs_utilities=r["costs_utilities"],
                   costs_fnb=r["costs_fnb"], costs_marketing=r["costs_marketing"],
                   costs_other=r["costs_other"], occupancy_pct=r["occupancy_pct"], adr=r["adr"],
                   revpar=r["revpar"]) for r in rows]


def load_bank_lines(store) -> list[BankLine]:
    rows = store.db.execute("SELECT * FROM fin_bank_lines ORDER BY day_offset ASC").fetchall()
    return [BankLine(id=r["id"], day_offset=r["day_offset"], description=r["description"] or "",
                     reference=r["reference"] or "", amount_eur=r["amount_eur"],
                     balance_eur=r["balance_eur"], kind=r["kind"]) for r in rows]


def load_stripe_payments(store) -> list[StripePayment]:
    rows = store.db.execute("SELECT * FROM fin_stripe_payments ORDER BY day_offset ASC").fetchall()
    return [StripePayment(id=r["id"], day_offset=r["day_offset"], created_time=r["created_time"],
                          amount_eur=r["amount_eur"], fee_eur=r["fee_eur"], net_eur=r["net_eur"],
                          status=r["status"], customer_name=r["customer_name"] or "",
                          customer_email=r["customer_email"] or "", card_last4=r["card_last4"] or "",
                          description=r["description"] or "", payout_ref=r["payout_ref"] or "",
                          dispute_ref=r["dispute_ref"] or "", refunds_ref=r["refunds_ref"] or "")
           for r in rows]


def load_pms_charges(store) -> list[PmsCharge]:
    rows = store.db.execute("SELECT * FROM fin_pms_charges ORDER BY day_offset ASC").fetchall()
    return [PmsCharge(id=r["id"], day_offset=r["day_offset"],
                      reservation_ref=r["reservation_ref"] or "", guest_name=r["guest_name"] or "",
                      guest_email=r["guest_email"] or "", amount_eur=r["amount_eur"],
                      method=r["method"], channel=r["channel"] or "", room=r["room"] or "",
                      card_last4=r["card_last4"] or "") for r in rows]


def load_pos_sales(store) -> list[PosSaleRow]:
    rows = store.db.execute("SELECT * FROM pos_sales_daily ORDER BY date ASC").fetchall()
    return [PosSaleRow(date=r["date"], item_id=r["item_id"], units=r["units"],
                       revenue=r["revenue"], covers=r["covers"]) for r in rows]


def load_pos_items(store) -> list[PosItemRow]:
    rows = store.db.execute("SELECT * FROM pos_items").fetchall()
    return [PosItemRow(id=r["id"], item=r["item"] or "", venue=r["venue"] or "",
                       price=r["price"]) for r in rows]


# --------------------------------------------------------------------------
# live path: a hotel's own CSV exports in data/imports/
# --------------------------------------------------------------------------
def import_financial_daily_csv(store, path: Path) -> int:
    """`date,revenue_rooms,...` rows, one per day. Accumulates - never clears."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    n = 0
    for r in rows:
        d = str(_row_get(r, "date"))[:10]
        if not d:
            continue
        store.db.execute(
            "INSERT INTO fin_daily (date, revenue_rooms, revenue_fnb, revenue_spa, "
            "revenue_other, costs_payroll, costs_utilities, costs_fnb, costs_marketing, "
            "costs_other, occupancy_pct, adr, revpar) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET revenue_rooms=excluded.revenue_rooms, "
            "revenue_fnb=excluded.revenue_fnb, revenue_spa=excluded.revenue_spa, "
            "revenue_other=excluded.revenue_other, costs_payroll=excluded.costs_payroll, "
            "costs_utilities=excluded.costs_utilities, costs_fnb=excluded.costs_fnb, "
            "costs_marketing=excluded.costs_marketing, costs_other=excluded.costs_other, "
            "occupancy_pct=excluded.occupancy_pct, adr=excluded.adr, revpar=excluded.revpar",
            (d, _num(_row_get(r, "revenue_rooms")), _num(_row_get(r, "revenue_fnb")),
             _num(_row_get(r, "revenue_spa")), _num(_row_get(r, "revenue_other")),
             _num(_row_get(r, "costs_payroll")), _num(_row_get(r, "costs_utilities")),
             _num(_row_get(r, "costs_fnb")), _num(_row_get(r, "costs_marketing")),
             _num(_row_get(r, "costs_other")), _num(_row_get(r, "occupancy_pct")),
             _num(_row_get(r, "adr")), _num(_row_get(r, "revpar"))))
        n += 1
    return n


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def import_recon_window_csv(store, imports_dir: Path, window_days: int = 7) -> dict:
    """Bank/Stripe/PMS-charge exports use real dates; this anchors day_offset=0
    on the latest bank statement date and clears the previous window first -
    these three tables are a rolling snapshot, not an accumulating ledger."""
    bank_rows = _read_csv(imports_dir / "bank_statement.csv")
    stripe_rows = _read_csv(imports_dir / "stripe_payments.csv")
    pms_rows = _read_csv(imports_dir / "pms_charges.csv")
    if not bank_rows:
        return {"fin_bank_lines": 0, "fin_stripe_payments": 0, "fin_pms_charges": 0}
    dates = [str(_row_get(r, "date"))[:10] for r in bank_rows if _row_get(r, "date")]
    anchor = max(date.fromisoformat(d) for d in dates)

    def offset(d: str) -> int:
        return (date.fromisoformat(d[:10]) - anchor).days

    for table in ("fin_bank_lines", "fin_stripe_payments", "fin_pms_charges"):
        store.db.execute(f"DELETE FROM {table}")
    for r in bank_rows:
        d = str(_row_get(r, "date"))[:10]
        store.db.execute(
            "INSERT INTO fin_bank_lines (id, day_offset, description, reference, amount_eur, "
            "balance_eur, kind) VALUES (?,?,?,?,?,?,?)",
            (str(_row_get(r, "id", default=d)), offset(d), _row_get(r, "description"),
             _row_get(r, "reference"), _num(_row_get(r, "amount_eur", "amount")),
             _num(_row_get(r, "balance_eur", "balance")), _row_get(r, "kind", default="credit")))
    for r in stripe_rows:
        d = str(_row_get(r, "date"))[:10]
        if not d:
            continue
        store.db.execute(
            "INSERT INTO fin_stripe_payments (id, day_offset, created_time, amount_eur, "
            "fee_eur, net_eur, status, customer_name, customer_email, card_last4, description, "
            "payout_ref, dispute_ref, refunds_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(_row_get(r, "id")), offset(d), _row_get(r, "created_time", default="00:00"),
             _num(_row_get(r, "amount_eur", "amount")), _num(_row_get(r, "fee_eur", "fee")),
             _num(_row_get(r, "net_eur", "net")), _row_get(r, "status", default="succeeded"),
             _row_get(r, "customer_name"), _row_get(r, "customer_email"),
             _row_get(r, "card_last4"), _row_get(r, "description"), _row_get(r, "payout_ref"),
             _row_get(r, "dispute_ref"), _row_get(r, "refunds_ref")))
    for r in pms_rows:
        d = str(_row_get(r, "date"))[:10]
        if not d:
            continue
        store.db.execute(
            "INSERT INTO fin_pms_charges (id, day_offset, reservation_ref, guest_name, "
            "guest_email, amount_eur, method, channel, room, card_last4) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(_row_get(r, "id")), offset(d), _row_get(r, "reservation_ref"),
             _row_get(r, "guest_name"), _row_get(r, "guest_email"),
             _num(_row_get(r, "amount_eur", "amount")), _row_get(r, "method", default="card"),
             _row_get(r, "channel"), _row_get(r, "room"), _row_get(r, "card_last4")))
    return {"fin_bank_lines": len(bank_rows), "fin_stripe_payments": len(stripe_rows),
           "fin_pms_charges": len(pms_rows)}


def import_pos_csv(store, imports_dir: Path) -> dict:
    sales = _read_csv(imports_dir / "pos_sales_daily.csv")
    items = _read_csv(imports_dir / "pos_items.csv")
    for r in sales:
        d, item_id = str(_row_get(r, "date"))[:10], str(_row_get(r, "item_id"))
        if not d or not item_id:
            continue
        store.db.execute(
            "INSERT OR REPLACE INTO pos_sales_daily (id, date, item_id, units, revenue, covers) "
            "VALUES (?,?,?,?,?,?)",
            (f"{d}:{item_id}", d, item_id, _num(_row_get(r, "units")),
             _num(_row_get(r, "revenue")), _int(_row_get(r, "covers"))))
    for r in items:
        item_id = str(_row_get(r, "id"))
        if not item_id:
            continue
        store.db.execute("INSERT OR REPLACE INTO pos_items (id, item, venue, price) "
                        "VALUES (?,?,?,?)",
                        (item_id, _row_get(r, "item"), _row_get(r, "venue"),
                         _num(_row_get(r, "price"))))
    return {"pos_sales_daily": len(sales), "pos_items": len(items)}
