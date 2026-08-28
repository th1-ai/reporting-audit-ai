#!/usr/bin/env python3
"""tools/recon.py - the Weekly Income Audit engine, plus the `action` command.

    python3 tools/recon.py action <id> <name> [--note "..."]

The engine (`reconcile()` and friends) is pure: bank statement lines, Stripe
payments and PMS folio charges in, a list of `ReconItem` out. No I/O, no LLM -
see docs/how-it-works.md for the seven-step design this mirrors: read the
statement, decompose payouts BEFORE anything else (Stripe deposits net, not
gross), detect duplicates, climb a three-rung match ladder per folio charge,
classify, summarize, and only then are the five actions available - none of
which this function performs. `action <id> <name>` is the CLI half: it applies
one action through the same write guard as everything else in this family -
see "Reusing the review FSM for actions" in docs/how-it-works.md.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOLERANCE_EUR = 5.0
TOLERANCE_PCT = 0.5
SETTLEMENT_DAYS = 2
GL_FX = "7620"
GL_FEES = "6420"
DUPLICATE_WINDOW_MINUTES = 15
NEAR_MISS_EUR = 50.0

ACTIONS = ("link_customer", "post_folio", "refund_duplicate", "charge_balance",
          "post_fx", "ask_front_office", "open_dispute")
LOG_ONLY_ACTIONS = {"post_fx", "ask_front_office", "open_dispute"}
WRITE_GUARD = {"link_customer": "payment", "post_folio": "pms_write",
              "refund_duplicate": "payment", "charge_balance": "payment"}


@dataclass
class BankLine:
    id: str
    day_offset: int
    description: str
    reference: str = ""
    amount_eur: float = 0.0
    balance_eur: float = 0.0
    kind: str = "credit"   # payout | chargeback | bank_fee | cash | terminal | payable | credit


@dataclass
class StripePayment:
    id: str
    day_offset: int
    created_time: str = "00:00"   # "HH:MM", used to order same-key duplicates
    amount_eur: float = 0.0
    fee_eur: float = 0.0
    net_eur: float = 0.0
    status: str = "succeeded"   # succeeded | refunded | disputed
    customer_name: str = ""
    customer_email: str = ""
    card_last4: str = ""
    description: str = ""
    payout_ref: str = ""
    dispute_ref: str = ""
    refunds_ref: str = ""


@dataclass
class PmsCharge:
    id: str
    day_offset: int
    reservation_ref: str = ""
    guest_name: str = ""
    guest_email: str = ""
    amount_eur: float = 0.0
    method: str = "card"   # card | cash | terminal
    channel: str = ""
    room: str = ""
    card_last4: str = ""


@dataclass
class Payout:
    payout_ref: str
    gross: float
    fees: float
    net: float
    ties: bool
    bank_line_id: str = ""


@dataclass
class ReconItem:
    id: str
    kind: str
    severity: str        # clean | explained | attention
    amount_eur: float
    title: str
    detail: str
    action: str | None = None
    matched_by: str | None = None
    evidence: dict = field(default_factory=dict)
    day_offset: int = 0


@dataclass
class ReconResult:
    items: list = field(default_factory=list)
    payouts: list = field(default_factory=list)
    headline: str = ""
    amount_in_question: float = 0.0
    counts: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)


def read_statement(lines: list[BankLine]) -> dict:
    """Step 1. Every line is carried forward; nothing skipped because it looks boring."""
    if not lines:
        return {"credits": 0, "debits": 0, "opening": 0.0, "closing": 0.0}
    credits = sum(1 for l in lines if l.amount_eur > 0)
    debits = sum(1 for l in lines if l.amount_eur < 0)
    opening = round(lines[0].balance_eur - lines[0].amount_eur, 2)
    closing = lines[-1].balance_eur
    return {"credits": credits, "debits": debits, "opening": opening, "closing": closing}


def decompose_payouts(bank_lines: list[BankLine],
                      payments: list[StripePayment]) -> list[Payout]:
    """Step 3. Group card payments by payout, then check the net against the bank line.

    Nothing downstream can trace a card payment to the statement until this
    runs - Stripe deposits one net figure per payout, not one deposit per charge.
    """
    by_ref: dict[str, list[StripePayment]] = {}
    for p in payments:
        if p.payout_ref:
            by_ref.setdefault(p.payout_ref, []).append(p)
    bank_by_ref = {l.reference: l for l in bank_lines if l.kind == "payout"}
    out = []
    for ref, group in sorted(by_ref.items()):
        gross = round(sum(p.amount_eur for p in group), 2)
        fees = round(sum(p.fee_eur for p in group), 2)
        net = round(sum(p.net_eur for p in group), 2)
        bank_line = bank_by_ref.get(ref)
        ties = bank_line is not None and abs(bank_line.amount_eur - net) < 0.005
        out.append(Payout(payout_ref=ref, gross=gross, fees=fees, net=net, ties=ties,
                          bank_line_id=bank_line.id if bank_line else ""))
    return out


def detect_duplicates(payments: list[StripePayment],
                      window_minutes: int = DUPLICATE_WINDOW_MINUTES) -> set[str]:
    """Step 4. Same card, amount and day, second charge 0 < gap <= window, both succeeded.

    A payment named by another payment's `refunds_ref` is the retry's refund
    and cancels the duplicate rather than counting as a second leak.
    """
    def minutes(hhmm: str) -> int:
        try:
            h, m = str(hhmm).split(":")[:2]
            return int(h) * 60 + int(m)
        except (ValueError, IndexError):
            return 0

    refunded_targets = {p.refunds_ref for p in payments if p.refunds_ref}
    by_key: dict[tuple, list[StripePayment]] = {}
    for p in payments:
        if p.status != "succeeded":
            continue
        by_key.setdefault((p.card_last4, round(p.amount_eur, 2), p.day_offset), []).append(p)
    dupes: set[str] = set()
    for group in by_key.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda p: minutes(p.created_time))
        first = ordered[0]
        for later in ordered[1:]:
            gap = minutes(later.created_time) - minutes(first.created_time)
            if not (0 < gap <= window_minutes):
                continue
            if later.id in refunded_targets:
                continue
            dupes.add(later.id)
    return dupes


def amounts_close(a: float, b: float, tolerance_eur: float = TOLERANCE_EUR,
                  tolerance_pct: float = TOLERANCE_PCT) -> bool:
    band = max(tolerance_eur, abs(b) * tolerance_pct / 100)
    return abs(a - b) <= band


def match_ladder(charge: PmsCharge, pool: list[StripePayment],
                 rules: dict) -> tuple[StripePayment | None, str | None]:
    """Step 5. Try folio reference, then email, then (if enabled) card+date+amount.

    Each rung is tried only when the one above fails, and the winning rung is
    reported on the item so a hotel can see how a match was found.
    """
    if charge.reservation_ref:
        for sp in pool:
            if charge.reservation_ref and charge.reservation_ref in sp.description:
                return sp, "folio"
    if charge.guest_email:
        for sp in pool:
            if (sp.customer_email and sp.customer_email.lower() == charge.guest_email.lower()
                    and abs(sp.day_offset - charge.day_offset) <= 1):
                return sp, "email"
    if rules.get("recon-fuzzy-identity", True) and charge.card_last4:
        for sp in pool:
            if (sp.card_last4 == charge.card_last4
                    and abs(sp.day_offset - charge.day_offset) <= 1
                    and amounts_close(sp.amount_eur, charge.amount_eur)):
                return sp, "card"
    return None, None


def _payout_for(sp: StripePayment, payouts: list[Payout]) -> Payout | None:
    return next((p for p in payouts if p.payout_ref == sp.payout_ref), None)


def classify_card_charge(charge: PmsCharge, matched: StripePayment | None, matched_by: str | None,
                         payouts: list[Payout], rules: dict, tolerance_eur: float,
                         tolerance_pct: float, settlement_days: int) -> ReconItem:
    """Steps 5-6 for one card-method PMS charge."""
    base_id = f"charge:{charge.id}"
    if matched is None:
        if charge.day_offset >= -settlement_days:
            return ReconItem(
                id=base_id, kind="unsettled", severity="explained" if rules.get(
                    "recon-settlement-window", True) else "attention",
                amount_eur=charge.amount_eur, day_offset=charge.day_offset,
                title=f"{charge.reservation_ref or charge.id}: takings not yet settled",
                detail=("Two of three is the right answer today - the card payment has not "
                       "reached the bank within the settlement window yet." if rules.get(
                           "recon-settlement-window", True) else
                       "The settlement-window rule is off, so today's takings report as "
                       "missing from the bank instead of pending."),
                action=None if rules.get("recon-settlement-window", True) else "ask_front_office",
                evidence={"pms_charge_id": charge.id})
        return ReconItem(
            id=base_id, kind="unmatched_charge", severity="attention", amount_eur=charge.amount_eur,
            day_offset=charge.day_offset, matched_by=None,
            title=f"{charge.reservation_ref or charge.id}: no matching Stripe payment",
            detail="No Stripe payment matched this folio charge on any of the three rungs.",
            action="ask_front_office", evidence={"pms_charge_id": charge.id})

    diff = round(matched.amount_eur - charge.amount_eur, 2)
    payout = _payout_for(matched, payouts)
    bank_ties = payout.ties if payout else False
    evidence = {"pms_charge_id": charge.id, "stripe_id": matched.id,
               "payout_ref": matched.payout_ref, "bank_ties": bank_ties}

    if abs(diff) < 0.005:
        if matched_by == "card":
            return ReconItem(
                id=base_id, kind="identity", severity="explained", amount_eur=charge.amount_eur,
                day_offset=charge.day_offset, matched_by=matched_by,
                title=f"{charge.reservation_ref or charge.id}: all three agree, matched on card",
                detail=(f"All three agree at {charge.amount_eur:,.2f}, but only because the "
                       f"card matched. The booking email and the payment email differ - a "
                       f"company card on a personal booking, or similar. Searching by email "
                       f"alone would have found nothing here."),
                action="link_customer", evidence=evidence)
        return ReconItem(
            id=base_id, kind="clean", severity="clean" if bank_ties else "explained",
            amount_eur=charge.amount_eur, day_offset=charge.day_offset, matched_by=matched_by,
            title=f"{charge.reservation_ref or charge.id}: all three agree",
            detail=f"All three agree at {charge.amount_eur:,.2f}.", action=None, evidence=evidence)

    if amounts_close(matched.amount_eur, charge.amount_eur, tolerance_eur, tolerance_pct):
        explained = rules.get("recon-tolerance", True)
        return ReconItem(
            id=base_id, kind="fx", severity="explained" if explained else "attention",
            amount_eur=diff, day_offset=charge.day_offset, matched_by=matched_by,
            title=f"{charge.reservation_ref or charge.id}: rounding/FX difference of {diff:+,.2f}",
            detail=(f"Within tolerance ({tolerance_eur:.2f} or {tolerance_pct}%) - posted to "
                   f"GL {GL_FX}." if explained else
                   f"The tolerance rule is off, so a {diff:+,.2f} rounding difference is "
                   f"raised as a query instead of explained automatically."),
            action=None if explained else "post_fx", evidence=evidence)

    if diff < 0:
        return ReconItem(
            id=base_id, kind="amount_diff", severity="attention", amount_eur=diff,
            day_offset=charge.day_offset, matched_by=matched_by,
            title=f"{charge.reservation_ref or charge.id}: folio ahead of the card by {-diff:,.2f}",
            detail="Extras were posted to the folio after the card was swiped.",
            action="charge_balance", evidence=evidence)

    return ReconItem(
        id=base_id, kind="amount_diff", severity="attention", amount_eur=diff,
        day_offset=charge.day_offset, matched_by=matched_by,
        title=f"{charge.reservation_ref or charge.id}: card charged {diff:,.2f} more than the folio",
        detail="The card was charged more than the folio shows. Needs a human to say why.",
        action="ask_front_office", evidence=evidence)


def classify_other_rail(charge: PmsCharge, rules: dict) -> ReconItem:
    """Cash and terminal charges never touch Stripe - that is not an error."""
    ota_rule = rules.get("recon-ota-virtual", True)
    return ReconItem(
        id=f"charge:{charge.id}", kind="other_rail",
        severity="explained" if ota_rule else "attention", amount_eur=charge.amount_eur,
        day_offset=charge.day_offset,
        title=f"{charge.reservation_ref or charge.id}: {charge.method} - not applicable to Stripe",
        detail=("This money never touches Stripe." if ota_rule else
               "The OTA/virtual-card rule is off, so this reports as a missing Stripe "
               "payment instead of not applicable."),
        action=None if ota_rule else "ask_front_office", evidence={"pms_charge_id": charge.id})


def classify_stripe_orphan(sp: StripePayment) -> ReconItem:
    """A Stripe payment with no matching PMS folio charge - the posting is missing."""
    return ReconItem(
        id=f"stripe:{sp.id}", kind="no_folio", severity="attention", amount_eur=sp.amount_eur,
        day_offset=sp.day_offset,
        title=f"Stripe payment {sp.id}: no PMS folio posting",
        detail=(f"{sp.customer_name or sp.customer_email or 'A guest'} paid "
               f"{sp.amount_eur:,.2f} on Stripe but no folio charge matches it."),
        action="post_folio", evidence={"stripe_id": sp.id})


def classify_duplicate(sp: StripePayment) -> ReconItem:
    return ReconItem(
        id=f"stripe:{sp.id}", kind="duplicate", severity="attention", amount_eur=sp.amount_eur,
        day_offset=sp.day_offset,
        title=f"Stripe payment {sp.id}: suspected duplicate charge",
        detail=(f"Same card, amount and day as an earlier successful charge, "
               f"within {DUPLICATE_WINDOW_MINUTES} minutes."),
        action="refund_duplicate", evidence={"stripe_id": sp.id})


def classify_chargeback(sp: StripePayment) -> ReconItem:
    return ReconItem(
        id=f"stripe:{sp.id}", kind="chargeback", severity="attention", amount_eur=sp.amount_eur,
        day_offset=sp.day_offset,
        title=f"Stripe payment {sp.id}: disputed ({sp.dispute_ref or 'no ref'})",
        detail=("All three systems agree on the amount - they disagree on who is entitled "
               "to it, which is a case to argue, not a number to fix. See the Disputes tab."),
        action="open_dispute", evidence={"stripe_id": sp.id, "dispute_ref": sp.dispute_ref})


def classify_unmatched_bank_credit(line: BankLine, pool_amounts: list[float],
                                   near_miss_eur: float = NEAR_MISS_EUR) -> ReconItem:
    near = [a for a in pool_amounts if abs(a - line.amount_eur) <= near_miss_eur]
    detail = (f"No usable reference on this credit. There are {len(near)} payment(s) within "
             f"{near_miss_eur:.0f} of it, which is exactly the kind of near-miss that "
             f"produces a wrong answer - so this is not guessed at.") if near else (
        "No usable reference on this credit, and nothing else close enough to guess from.")
    return ReconItem(
        id=f"bank:{line.id}", kind="unmatched_bank", severity="attention",
        amount_eur=line.amount_eur, day_offset=line.day_offset,
        title=f"Unidentified bank credit: {line.amount_eur:,.2f} ({line.description[:40]})",
        detail=detail, action="ask_front_office", evidence={"bank_line_id": line.id})


def classify_outgoing(lines: list[BankLine]) -> ReconItem | None:
    if not lines:
        return None
    total = round(sum(l.amount_eur for l in lines), 2)
    return ReconItem(
        id="bank:outgoing", kind="outgoing", severity="explained", amount_eur=total,
        day_offset=lines[-1].day_offset,
        title=f"{len(lines)} payable(s) rolled up: {total:,.2f}",
        detail="Outgoing payables, cross-linked to the Invoice inbox.", action=None,
        evidence={"bank_line_ids": [l.id for l in lines]})


SEVERITY_ORDER = {"attention": 0, "explained": 1, "clean": 2}


def reconcile(bank_lines: list[BankLine], payments: list[StripePayment],
             charges: list[PmsCharge], rules: dict | None = None,
             *, tolerance_eur: float = TOLERANCE_EUR, tolerance_pct: float = TOLERANCE_PCT,
             settlement_days: int = SETTLEMENT_DAYS,
             near_miss_eur: float = NEAR_MISS_EUR) -> ReconResult:
    """Run the seven-step reconciliation. Writes nothing - see docs/how-it-works.md."""
    rules = rules or {}
    steps = ["read the bank statement", "pull Stripe and PMS for the window",
             "decompose payouts before matching anything",
             "detect duplicate charges", "climb the match ladder per folio charge",
             "classify every item", "summarize"]
    stats = read_statement(bank_lines)
    payouts = decompose_payouts(bank_lines, payments)
    dup_ids = detect_duplicates(payments)

    # `excluded` keeps a duplicate out of the match pool (it must never satisfy a
    # folio); `consumed` is only the payments that actually matched a charge, so
    # a duplicate still gets its own exception below instead of vanishing.
    excluded: set[str] = set(dup_ids)
    consumed: set[str] = set()
    items: list[ReconItem] = []
    for charge in charges:
        if charge.method != "card":
            items.append(classify_other_rail(charge, rules))
            continue
        pool = [p for p in payments
               if p.id not in excluded and p.id not in consumed and p.status == "succeeded"]
        matched, matched_by = match_ladder(charge, pool, rules)
        if matched is not None:
            consumed.add(matched.id)
        items.append(classify_card_charge(charge, matched, matched_by, payouts, rules,
                                          tolerance_eur, tolerance_pct, settlement_days))

    for sp in payments:
        if sp.id in consumed:
            continue
        if sp.id in dup_ids:
            items.append(classify_duplicate(sp))
        elif sp.status == "disputed":
            items.append(classify_chargeback(sp))
        elif sp.status == "succeeded":
            items.append(classify_stripe_orphan(sp))
        # refunded, non-duplicate payments need no exception of their own -
        # they already reduced the payout their refund belongs to.

    all_amounts = [p.amount_eur for p in payments] + [c.amount_eur for c in charges]
    payable_lines = [l for l in bank_lines if l.kind == "payable"]
    outgoing = classify_outgoing(payable_lines)
    if outgoing:
        items.append(outgoing)
    referenced_bank_ids = {p.bank_line_id for p in payouts if p.bank_line_id}
    for line in bank_lines:
        if line.kind != "credit" or line.id in referenced_bank_ids:
            continue
        items.append(classify_unmatched_bank_credit(line, all_amounts, near_miss_eur))

    items.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 0), -abs(i.amount_eur)))
    counts = {"clean": 0, "explained": 0, "attention": 0}
    amount_in_question = 0.0
    for i in items:
        counts[i.severity] = counts.get(i.severity, 0) + 1
        if i.severity == "attention":
            amount_in_question += abs(i.amount_eur)
    resolved = counts.get("clean", 0) + counts.get("explained", 0)
    headline = (f"{resolved} of {len(items)} reconciled - {counts.get('attention', 0)} "
               f"need(s) a human - {amount_in_question:,.2f} in question")
    return ReconResult(items=items, payouts=payouts, headline=headline,
                       amount_in_question=round(amount_in_question, 2), counts=counts,
                       steps=steps)


# --------------------------------------------------------------------------
# CLI: apply one of the five actions to a recon_item
# --------------------------------------------------------------------------
def _apply_write(settings, store, item, action: str):
    """Perform the guarded write for one action. Raises WriteBlocked /
    AdapterNotImplemented exactly like any other adapter write in this family."""
    from core.adapters import get_pms, get_stub
    from core.review import assert_write_allowed
    from core.adapters.base import AdapterNotImplemented

    payload = item.payload or {}
    if action == "post_folio":
        pms = get_pms(settings)
        return pms.add_note(payload.get("reservation_ref", item.id),
                            f"[reporting-audit-ai] post_folio: {item.draft or ''}", item=item)
    if action == "refund_duplicate":
        payments = get_stub("payments", settings)
        return payments.refund(payload.get("stripe_id", item.id),
                               float(payload.get("amount_eur", 0.0)), item=item)
    # link_customer / charge_balance: no base-class method fits, so the guard
    # is checked directly and the stub message explains what to build.
    assert_write_allowed(settings, WRITE_GUARD[action], item)
    raise AdapterNotImplemented("payments", method=action)


def cmd_action(store, settings, args) -> int:
    from core.review import WriteBlocked
    from core.adapters.base import AdapterError

    if args.name not in ACTIONS:
        print(f"error: unknown action '{args.name}'. Known: {', '.join(ACTIONS)}",
             file=sys.stderr)
        return 2
    item = store.get_item(args.id)
    if item is None or item.kind != "recon_item":
        print(f"error: no reconciliation item {args.id}", file=sys.stderr)
        return 1
    if item.review_status not in ("needs_human", "stale", "failed"):
        print(f"error: {args.id} is '{item.review_status}', not waiting for an action",
             file=sys.stderr)
        return 1

    store.transition(item.id, "approved", "human", {"action": args.name, "note": args.note})

    if args.name in LOG_ONLY_ACTIONS:
        store.record_event(item.id, "human", f"action:{args.name}:logged",
                           {"note": args.note})
        store.transition(item.id, "sending", "agent", {"action": args.name})
        store.mark_sent(item.id)
        print(f"logged {args.name} on {item.id} - nothing posted, decision recorded.")
        return 0

    item = store.get_item(item.id)
    store.transition(item.id, "sending", "agent", {"action": args.name})
    try:
        result = _apply_write(settings, store, item, args.name)
    except WriteBlocked as exc:
        store.mark_send_failed(item.id, str(exc))
        print(str(exc))
        print(f"\n{item.id} is 'failed' - your decision is recorded. Re-run "
             f"`python3 tools/recon.py action {item.id} {args.name}` once mode is live "
             f"and it resumes from 'failed' straight back to 'approved'.")
        return 0
    except AdapterError as exc:
        store.mark_send_failed(item.id, str(exc))
        print(f"not built yet: {exc}")
        return 0
    ref = result.get("message_id") if isinstance(result, dict) else None
    store.mark_sent(item.id, ref)
    print(f"applied {args.name} on {item.id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    from core.config import ConfigError, load_settings
    from core.store import Store, StoreError

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_action = sub.add_parser("action", help="apply one of the five reconciliation actions")
    p_action.add_argument("id")
    p_action.add_argument("name", choices=ACTIONS)
    p_action.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "action":
            return cmd_action(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
