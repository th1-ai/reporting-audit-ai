#!/usr/bin/env python3
"""tools/doctor.py - is Reporting & Audit AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus checks
specific to this agent: the prompt + schema files, the data sources each job
reads, and whether the F&B sub-agent is on and configured. The data-source
checks run the *same* CSV loaders `tools/run.py` calls before every pass
(`store_ext.import_financial_daily_csv` / `import_recon_window_csv` /
`import_pos_csv`), against a throwaway in-memory store - a PASS means the
file was actually parsed into rows, not just that it exists. Exits 0 when
everything passed, 1 when a FAIL line needs fixing. Never a traceback: a
config error is shown as a FAIL row like any other.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402

import store_ext  # noqa: E402


def check_prompts() -> Check:
    missing = [p for p in ("prompts/finance-note.md", "prompts/schemas/finance-note.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "finance-note.md + schema present")


def _probe_store():
    """A throwaway, in-memory store - never the hotel's own `data/agent.db` -
    so doctor can run the exact CSV loaders `tools/run.py` calls before every
    pass without touching real state."""
    from core.store import Store
    probe = Store(None, path=":memory:")
    store_ext.migrate(probe)
    return probe


def check_data_sources(settings: Settings) -> list[Check]:
    """The same paths, and the same loaders, `tools/run.py` uses before every
    pass (`store_ext.import_financial_daily_csv` / `import_recon_window_csv` /
    `import_pos_csv`) - see docs/integrations.md. A PASS here means the file
    was actually parsed into rows, not just that it exists."""
    imports = REPO_ROOT / "data" / "imports"
    checks = []
    probe = _probe_store()
    try:
        path = imports / "financial_daily.csv"
        n_daily = store_ext.import_financial_daily_csv(probe, path)
        checks.append(_import_check("data/imports/financial_daily.csv", path, n_daily,
                                    "Weekly Owner Report source"))

        recon_counts = store_ext.import_recon_window_csv(probe, imports)
        for filename, table in (("bank_statement.csv", "fin_bank_lines"),
                                ("stripe_payments.csv", "fin_stripe_payments"),
                                ("pms_charges.csv", "fin_pms_charges")):
            checks.append(_import_check(f"data/imports/{filename}", imports / filename,
                                        recon_counts.get(table, 0),
                                        "Weekly Income Audit source"))

        fnb_on = bool(settings.agent_get("subagents.fnb_sales_audit.enabled", False))
        if fnb_on:
            pos_path = imports / "pos_sales_daily.csv"
            pos_counts = store_ext.import_pos_csv(probe, imports)
            checks.append(_import_check("data/imports/pos_sales_daily.csv", pos_path,
                                        pos_counts.get("pos_sales_daily", 0),
                                        "the Pit Boss's till feed",
                                        hint_suffix=", see docs/integrations.md#pos"))
    finally:
        probe.close()
    return checks


def _import_check(label: str, path: Path, n_rows: int, used_by: str, *,
                  hint_suffix: str = "") -> Check:
    if not path.exists():
        return Check(label, WARN, f"not found - {used_by} will report 'nothing to report'",
                    f"Export it from your systems and save as {path}{hint_suffix}. "
                    f"`make demo` works without it (uses fixtures instead).")
    if n_rows == 0:
        return Check(label, WARN, f"found, but the loader could not read a single row for {used_by}",
                    "Check the column headers match docs/integrations.md - "
                    "bank_statement.csv also needs at least one dated row, since it "
                    "anchors the reconciliation window for the other two files.")
    return Check(label, PASS, f"found - {n_rows} row(s) the loader will import - {used_by}")


def check_subagents(settings: Settings) -> Check:
    fnb_on = settings.agent_get("subagents.fnb_sales_audit.enabled", False)
    outlets = settings.agent_get("fnb_audit.outlets", []) or []
    if not fnb_on:
        return Check("F&B Sales Audit", WARN, "disabled (off by default)",
                     "Set subagents.fnb_sales_audit.enabled: true in config/agent.yaml "
                     "once you have a POS export - see workflows/20-fnb-sales-audit.md.")
    if not outlets:
        return Check("F&B Sales Audit", WARN, "enabled, but fnb_audit.outlets is empty",
                     "One digest still runs (labeled 'Restaurant') but with no recipient. "
                     "Add outlets: [{name, manager_email}] in config/agent.yaml.")
    return Check("F&B Sales Audit", PASS, f"enabled, {len(outlets)} outlet(s) configured")


def check_recipients(settings: Settings) -> Check:
    recipients = settings.agent_get("report.recipients", []) or []
    manager = (settings.contacts.manager or {}).get("email", "")
    if recipients or manager:
        return Check("owner report recipients", PASS,
                     ", ".join(recipients) if recipients else manager)
    return Check("owner report recipients", WARN, "no recipients configured",
                 "Set report.recipients in config/agent.yaml, or contacts.manager.email "
                 "in config/hotel.yaml - the report will queue but `send` will fail without one.")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Reporting & Audit AI - doctor")

    checks = run_checks(settings, extra=[check_recipients, check_subagents])
    checks.append(check_prompts())
    checks += check_data_sources(settings)
    return print_table(checks, title="Reporting & Audit AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
