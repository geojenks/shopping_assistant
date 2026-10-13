"""Command line: `python -m shopping_assistant <command>` (or `shop <command>` once installed)."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from . import history, report, suggestions
from .db import Database
from .importers import read_csv_history, read_tesco_export
from .optimizer import plan_options, shortlist

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
DEFAULT_DB = os.environ.get("SHOP_DB", "shopping.db")


def _item(db: Database, text: str):
    item = db.find_item(text)
    if item is None:
        sys.exit(f"Don't know an item called {text!r}. Add it to items.json and run `load`.")
    return item


def cmd_init(db: Database, args) -> None:
    if args.sample:
        counts = db.load_catalogue(SAMPLE_DIR)
        db.add_purchases(read_csv_history(SAMPLE_DIR / "history.csv"))
        print(f"Loaded sample data: {counts}")
    print(f"Database ready at {args.db}")


def cmd_load(db: Database, args) -> None:
    print(db.load_catalogue(args.directory))


def cmd_import(db: Database, args) -> None:
    reader = {"tesco": read_tesco_export, "csv": read_csv_history}[args.kind]
    added, unmatched = db.add_purchases(reader(args.file))
    print(f"Imported {added} new lines ({unmatched} not yet matched to an item; see `shop unmatched`).")


def cmd_unmatched(db: Database, args) -> None:
    for name, n in db.unmatched_names()[: args.limit]:
        print(f"{n:4}  {name}")


def cmd_map(db: Database, args) -> None:
    item = _item(db, args.item)
    print(f"{args.product_name!r} is now {item.name}; updated {db.map_name(args.product_name, item.id)} lines.")


def cmd_list(db: Database, args) -> None:
    items = db.items()
    if args.action == "add":
        for text in args.items:
            item = _item(db, text)
            db.add_to_list(item.id, args.qty, "out" if args.out else "low", added_by=args.who or "")
            print(f"+ {item.name}{' (run out)' if args.out else ''}")
    elif args.action == "remove":
        for text in args.items:
            item = _item(db, text)
            print(("- " if db.remove_from_list(item.id) else "not on list: ") + item.name)
    elif args.action == "clear":
        db.clear_list()
        print("List cleared.")
    elif args.action == "due":
        found = history.due(db.purchases(), date.today())
        listed = {e.item_id for e in db.shopping_list()}
        for habit in found:
            if habit.item_id in listed:
                continue
            name = items[habit.item_id].name if habit.item_id in items else habit.item_id
            late = -habit.due_in(date.today())
            when = f"{late:.0f} days overdue" if late > 0 else f"due in {-late:.0f} days"
            print(f"  {name}: usually every {habit.every_days:.0f} days, last {habit.last_bought} ({when})")
            if args.add:
                db.add_to_list(habit.item_id, habit.usual_qty, "low", note="usual", added_by="history")
        if args.add and found:
            print("Added to the list.")
    else:
        entries = db.shopping_list()
        if not entries:
            print("The list is empty.")
        for e in entries:
            name = items[e.item_id].name if e.item_id in items else e.item_id
            flag = "OUT " if e.status == "out" else "low "
            print(f"  {flag} {e.qty:g} × {name}" + (f"  ({e.note})" if e.note else ""))


def cmd_pref(db: Database, args) -> None:
    if args.action == "show":
        prefs = db.preferences()
        for (item_id, chain), ok in sorted(prefs.own_label_ok.items()):
            print(f"  {item_id}: own label at {chain} {'is fine' if ok else 'is not OK'}")
        for item_id, brands in sorted(prefs.brands.items()):
            print(f"  {item_id}: wants {', '.join(sorted(brands))}")
        for item_id, chain in sorted(prefs.blocked):
            print(f"  {item_id}: never from {chain}")
        for key in sorted(prefs.blacklisted_products):
            print(f"  never buy product {key}")
        return
    item = _item(db, args.item)
    chain = (args.chain or "*").lower()
    if args.action == "sub-ok":
        db.set_own_label(item.id, chain, True)
    elif args.action == "no-sub":
        db.set_own_label(item.id, chain, False)
    elif args.action == "brand":
        db.add_brand(item.id, args.chain)  # the second argument is the brand here
    elif args.action == "block":
        db.block(item.id, chain)
    elif args.action == "blacklist":
        matches = [p for p in db.products() if p.item_id == item.id and p.chain == chain]
        if not matches:
            sys.exit(f"No {chain} products known for {item.name}.")
        # Blacklist the one we'd currently pick: the cheapest acceptable product there.
        prefs = db.preferences()
        allowed = [p for p in matches if prefs.allows(p)] or matches
        product = min(allowed, key=lambda p: p.price)
        db.blacklist(product.key)
        print(f"Won't buy {product.name} ({product.key}) again.")
        return
    elif args.action == "reset":
        db.clear_preferences(item.id)
    print("Saved.")


def cmd_plan(db: Database, args) -> None:
    entries = db.shopping_list()
    if not entries:
        sys.exit("The list is empty: add things with `shop list add ...` or `shop list due --add`.")
    settings = db.settings()
    if args.max_shops:
        settings.max_channels = args.max_shops
    plans = plan_options(entries, db.items(), db.products(), db.stores(), db.preferences(settings), settings)
    picked = shortlist(plans, limit=args.options)
    print(report.render_text(picked, show_all=args.all))
    if args.html:
        Path(args.html).write_text(report.render_html(picked), encoding="utf-8")
        print(f"\nWrote {args.html}")


def cmd_suggest(db: Database, args) -> None:
    item = _item(db, args.item)
    bought = {p.item_id for p in db.purchases() if p.item_id}
    found = suggestions.related(item, db.items().values(), db.products(), db.preferences(), bought)
    if not found:
        print(f"Nothing related to {item.name} in the catalogue yet.")
    for s in found:
        print(f"  {s.item.name}: {s.reason}")


def cmd_set(db: Database, args) -> None:
    value = {"true": True, "false": False}.get(args.value.lower())
    if value is None:
        value = float(args.value) if "." in args.value else int(args.value)
    db.set_setting(args.key, value)
    print(f"{args.key} = {value}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shop", description="Plan the cheapest sensible supermarket shop.")
    p.add_argument("--db", default=DEFAULT_DB, help="database file (default: $SHOP_DB or shopping.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create the database")
    s.add_argument("--sample", action="store_true", help="load the sample catalogue, prices and history")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("load", help="load items.json / stores.json / prices.csv from a directory")
    s.add_argument("directory")
    s.set_defaults(func=cmd_load)

    s = sub.add_parser("import", help="import purchase history")
    s.add_argument("kind", choices=["tesco", "csv"])
    s.add_argument("file")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("unmatched", help="history product names not yet matched to an item")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_unmatched)

    s = sub.add_parser("map", help="say which item a receipt product name is")
    s.add_argument("product_name")
    s.add_argument("item")
    s.set_defaults(func=cmd_map)

    s = sub.add_parser("list", help="the shopping list")
    s.add_argument("action", nargs="?", default="show", choices=["show", "add", "remove", "clear", "due"])
    s.add_argument("items", nargs="*")
    s.add_argument("--qty", type=float, default=1.0)
    s.add_argument("--out", action="store_true", help="run out completely (vs running low)")
    s.add_argument("--who", help="who added it")
    s.add_argument("--add", action="store_true", help="with `due`: put the due items on the list")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("pref", help="substitution preferences",
                       description="sub-ok ITEM [CHAIN] | no-sub ITEM [CHAIN] | brand ITEM BRAND | "
                                   "block ITEM CHAIN | blacklist ITEM CHAIN | reset ITEM | show")
    s.add_argument("action", choices=["show", "sub-ok", "no-sub", "brand", "block", "blacklist", "reset"])
    s.add_argument("item", nargs="?")
    s.add_argument("chain", nargs="?", help="supermarket chain, or the brand for `brand`")
    s.set_defaults(func=cmd_pref)

    s = sub.add_parser("plan", help="compare ways to buy the list")
    s.add_argument("--html", help="also write a comparison page to this file")
    s.add_argument("--all", action="store_true", help="show every item, not just differences")
    s.add_argument("--options", type=int, default=5)
    s.add_argument("--max-shops", type=int, help="most shops/deliveries in one plan")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("suggest", help="related things to try")
    s.add_argument("item")
    s.set_defaults(func=cmd_suggest)

    s = sub.add_parser("set", help="settings: per_stop, per_mile, max_distance_miles, max_channels, "
                                   "default_own_label_ok")
    s.add_argument("key", choices=["per_stop", "per_mile", "max_distance_miles", "max_channels",
                                   "default_own_label_ok"])
    s.add_argument("value")
    s.set_defaults(func=cmd_set)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "pref" and args.action != "show" and not args.item:
        sys.exit("Which item?")
    if args.command == "pref" and args.action in ("brand", "block", "blacklist") and not args.chain:
        sys.exit(f"`pref {args.action}` needs a second argument.")
    db = Database(args.db)
    try:
        args.func(db, args)
    finally:
        db.close()
