#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit / reject / send.

    python3 tools/review.py list [--status pending_review] [--kind owner_report]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --body-file draft.txt [--subject "..."] [--note "..."]
    python3 tools/review.py reject <id> --reason "wrong tone"
    python3 tools/review.py retry <id>          # re-queue a failed send
    python3 tools/review.py send                # email everything approved/edited
    python3 tools/review.py stale               # go-live step: clear the shadow-era queue

Add `--demo` to `list` / `show` to read `data/demo/demo.db` (built by
`make demo`) instead of your real queue in `data/agent.db` - `make demo`
always runs on the shipped example fixtures, in its own isolated database,
so this is the only way to inspect one of those drafts; see README.md,
"Quick start". `approve` / `edit` / `reject` / `send` do not take `--demo`:
the demo queue is read-only, on purpose - work the real queue instead.

Handles the two email-shaped kinds this agent produces: `owner_report` (the
Monday report) and `fnb_digest` (the Pit Boss's daily exceptions email).
Reconciliation items (`recon_item`) are not sent here - they have no message
to send, only an action to apply. Use `python3 tools/recon.py action <id>
<name>` for those; `list`/`show` here work for every kind, including recon_item.

Only this tool writes `approved` / `edited` / `rejected` (core/review.py).
Nothing here bypasses `mode: shadow` - see docs/safety.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email  # noqa: E402
from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import Run  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject, retry,  # noqa: E402
                         show, stale_backlog)
from core.store import Store, StoreError  # noqa: E402


def _print_item_line(item) -> None:
    payload = item.payload or {}
    label = payload.get("period_label") or payload.get("outlet") or payload.get("kind") or ""
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled".
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {item.kind:<13} {label[:40]}{marker}")


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    print("\nRun `python3 tools/review.py show <id>` for the full draft.")
    print("For a recon_item, act on it with `python3 tools/recon.py action <id> <name>`.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (detail["item"].get("payload") or {}).get("_sample"):
        print("[SAMPLE DATA] this item was read through a mock adapter, not your "
             "property - see docs/integrations.md.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text(encoding="utf-8")
    new_draft = dict(item.draft or {})
    new_draft["body_md"] = body
    if args.subject:
        new_draft["subject"] = args.subject
    edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another send attempt")
    return 0


def cmd_send(store, settings, args) -> int:
    with Run("review-send", settings, store) as run:
        claimed = store.claim_for_send(limit=args.limit)
        if not claimed:
            print("Nothing approved or edited is waiting to send.")
            run.stats = {"sent": 0, "failed": 0}
            return 0
        email = get_email(settings)
        sent, failed, skipped = 0, 0, 0
        for item in claimed:
            if item.kind not in ("owner_report", "fnb_digest"):
                # a recon_item should never reach the send queue this way - see
                # docs/how-it-works.md "Reusing the review FSM for actions".
                store.mark_send_failed(item.id, f"'{item.kind}' is not sent from here - "
                                       "use `python3 tools/recon.py action`.")
                print(f"skipped {item.id}: wrong tool for kind '{item.kind}'")
                skipped += 1
                continue
            draft = item.draft or {}
            to = draft.get("to") or []
            if not to or not any(to):
                store.mark_send_failed(item.id, "no recipient - set report.recipients or "
                                       "fnb_audit.outlets[].manager_email in config/agent.yaml")
                print(f"failed {item.id}: no recipient configured")
                failed += 1
                continue
            try:
                result = email.send(to, draft.get("subject", ""), draft.get("body_md", ""),
                                    item=item)
            except WriteBlocked as exc:
                # Not a failure: the mode blocked it. The approval stands for go-live -
                # see core/store.py TRANSITIONS ("sending" -> "approved") and
                # tests/test_core_store_fsm.py:test_blocked_send_returns_to_approved_not_failed.
                store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
                print(f"blocked {item.id} (approval kept): {exc}")
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
                store.mark_send_failed(item.id, str(exc))
                print(f"failed {item.id}: {exc}")
                failed += 1
                continue
            store.mark_sent(item.id, result.get("message_id"))
            print(f"sent {item.id} ({item.kind})")
            sent += 1
        run.stats = {"sent": sent, "failed": failed, "skipped": skipped}
    print(f"\n{sent} sent, {failed} failed, {skipped} skipped.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    demo_parent = argparse.ArgumentParser(add_help=False)
    demo_parent.add_argument(
        "--demo", action="store_true",
        help="read data/demo/demo.db (built by `make demo`) instead of your real queue")

    p_list = sub.add_parser("list", parents=[demo_parent], help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", parents=[demo_parent], help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the draft unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the draft, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", required=True)
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="email everything approved or edited")
    p_send.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

    args = parser.parse_args(argv)
    use_demo = bool(getattr(args, "demo", False))

    try:
        settings = load_settings(demo=use_demo)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if use_demo:
        demo_db = sub_data_dir("demo") / "demo.db"
        if not demo_db.exists():
            print("no demo data yet - run `make demo` first", file=sys.stderr)
            return 1
        store = Store(settings, path=demo_db)
    else:
        store = Store(settings)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be sent.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
