"""SQLite storage for the catalogue, prices, preferences, the shopping list and history."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .matching import match_item, normalise
from .models import (DeliveryTerms, Item, ListEntry, Product, Purchase, Settings, Store, Tier,
                     TravelCosts)
from .preferences import Preferences

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]', size REAL, unit TEXT NOT NULL DEFAULT 'each',
    keywords TEXT NOT NULL DEFAULT '[]', search TEXT NOT NULL DEFAULT '',
    exclude TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS stores (
    id TEXT PRIMARY KEY, chain TEXT NOT NULL, name TEXT NOT NULL, distance_miles REAL,
    pickup INTEGER NOT NULL DEFAULT 1, loyalty_member INTEGER NOT NULL DEFAULT 0,
    delivery TEXT  -- JSON DeliveryTerms, NULL if the store doesn't deliver
);
CREATE TABLE IF NOT EXISTS products (
    chain TEXT NOT NULL, id TEXT NOT NULL, item_id TEXT NOT NULL, name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'own', size REAL,
    unit TEXT NOT NULL DEFAULT 'each', price REAL NOT NULL, promo_price REAL,
    loyalty_price REAL, multibuy_qty INTEGER, multibuy_price REAL, url TEXT NOT NULL DEFAULT '',
    updated_at TEXT, PRIMARY KEY (chain, id, item_id)
);
CREATE TABLE IF NOT EXISTS price_history (
    chain TEXT NOT NULL, id TEXT NOT NULL, date TEXT NOT NULL, price REAL NOT NULL,
    loyalty_price REAL, multibuy TEXT, PRIMARY KEY (chain, id, date)
);
CREATE TABLE IF NOT EXISTS pref_blacklist (product_key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pref_blocked (item_id TEXT, chain TEXT, PRIMARY KEY (item_id, chain));
CREATE TABLE IF NOT EXISTS pref_own_label (item_id TEXT, chain TEXT, ok INTEGER NOT NULL,
    PRIMARY KEY (item_id, chain));
CREATE TABLE IF NOT EXISTS pref_brands (item_id TEXT, brand TEXT, PRIMARY KEY (item_id, brand));
CREATE TABLE IF NOT EXISTS list_entries (
    id INTEGER PRIMARY KEY, item_id TEXT NOT NULL UNIQUE, qty REAL NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'low', note TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY, date TEXT NOT NULL, chain TEXT NOT NULL, product_name TEXT NOT NULL,
    qty REAL NOT NULL, price REAL NOT NULL, item_id TEXT, source TEXT NOT NULL,
    txn_id TEXT NOT NULL, line_no INTEGER NOT NULL, UNIQUE (source, txn_id, line_no)
);
CREATE TABLE IF NOT EXISTS name_map (product_name TEXT PRIMARY KEY, item_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        self.conn.executescript(SCHEMA)

    def _migrate(self) -> None:
        """Bring databases made by older versions up to date."""
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        if columns and "search" not in columns:
            self.conn.execute("ALTER TABLE items ADD COLUMN search TEXT NOT NULL DEFAULT ''")
            self.conn.execute("ALTER TABLE items ADD COLUMN exclude TEXT NOT NULL DEFAULT '[]'")
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(products)")}
        if columns and "url" not in columns:
            self.conn.execute("ALTER TABLE products RENAME TO products_old")
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT INTO products (chain, id, item_id, name, brand, tier, size, unit, price, promo_price, "
                "loyalty_price, multibuy_qty, multibuy_price, updated_at) SELECT chain, id, item_id, name, brand, "
                "tier, size, unit, price, promo_price, loyalty_price, multibuy_qty, multibuy_price, updated_at "
                "FROM products_old")
            self.conn.execute("DROP TABLE products_old")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- catalogue -------------------------------------------------------

    def upsert_item(self, item: Item) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item.id, item.name, item.category, json.dumps(list(item.tags)), item.size, item.unit,
             json.dumps(list(item.keywords)), item.search, json.dumps(list(item.exclude))),
        )
        self.conn.commit()

    def items(self) -> dict[str, Item]:
        return {
            r["id"]: Item(r["id"], r["name"], r["category"], tuple(json.loads(r["tags"])), r["size"],
                          r["unit"], tuple(json.loads(r["keywords"])), r["search"], tuple(json.loads(r["exclude"])))
            for r in self.conn.execute("SELECT * FROM items ORDER BY name")
        }

    def find_item(self, text: str) -> Item | None:
        items = self.items()
        if text in items:
            return items[text]
        by_name = {i.name.casefold(): i for i in items.values()}
        if text.casefold() in by_name:
            return by_name[text.casefold()]
        item_id = match_item(text, items.values())
        if item_id:
            return items[item_id]
        # Short names typed by hand ("coffee" for "Ground coffee").
        words = set(normalise(text).split())
        fits = [i for i in items.values()
                if words and any(words <= set(normalise(n).split()) for n in (i.name, *i.keywords))]
        return min(fits, key=lambda i: len(i.name)) if fits else None

    def upsert_store(self, store: Store) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO stores VALUES (?, ?, ?, ?, ?, ?, ?)",
            (store.id, store.chain, store.name, store.distance_miles, int(store.pickup),
             int(store.loyalty_member), json.dumps(asdict(store.delivery)) if store.delivery else None),
        )
        self.conn.commit()

    def stores(self) -> list[Store]:
        return [
            Store(r["id"], r["chain"], r["name"], r["distance_miles"], bool(r["pickup"]),
                  DeliveryTerms(**json.loads(r["delivery"])) if r["delivery"] else None,
                  bool(r["loyalty_member"]))
            for r in self.conn.execute("SELECT * FROM stores ORDER BY name")
        ]

    def upsert_products(self, products: Iterable[Product], commit: bool = True) -> int:
        products = list(products)
        now = _now()
        self.conn.executemany(
            "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(p.chain, p.id, p.item_id, p.name, p.brand, p.tier.value, p.size, p.unit, p.price, p.promo_price,
              p.loyalty_price, p.multibuy_qty, p.multibuy_price, p.url, now) for p in products])
        self.conn.executemany(
            "INSERT OR REPLACE INTO price_history VALUES (?, ?, ?, ?, ?, ?)",
            [(p.chain, p.id, now[:10], p.price, p.loyalty_price,
              f"{p.multibuy_qty} for {p.multibuy_price:.2f}" if p.multibuy_qty else None) for p in products])
        if commit:
            self.conn.commit()
        return len(products)

    def replace_products(self, chain: str, item_id: str, products: Iterable[Product]) -> int:
        """Swap what we know about an item at a chain for a fresh search's results."""
        self.conn.execute("DELETE FROM products WHERE chain = ? AND item_id = ?", (chain, item_id))
        n = self.upsert_products(products, commit=False)
        self.conn.commit()
        return n

    def products(self, item_id: str | None = None) -> list[Product]:
        query, args = "SELECT * FROM products", ()
        if item_id:
            query, args = query + " WHERE item_id = ? ORDER BY chain, price", (item_id,)
        return [
            Product(r["id"], r["chain"], r["item_id"], r["name"], r["price"], r["brand"], Tier(r["tier"]),
                    r["size"], r["unit"], r["promo_price"], r["loyalty_price"], r["multibuy_qty"],
                    r["multibuy_price"], r["url"])
            for r in self.conn.execute(query, args)
        ]

    def prices_updated(self) -> dict[str, str]:
        """When each chain's prices were last refreshed."""
        return dict(self.conn.execute("SELECT chain, MAX(updated_at) FROM products GROUP BY chain").fetchall())

    def load_catalogue(self, directory: str | Path) -> dict[str, int]:
        """Load items.json, stores.json and prices.csv from a directory (each optional)."""
        directory = Path(directory)
        counts = {}
        if (directory / "items.json").exists():
            raw = json.loads((directory / "items.json").read_text())
            for r in raw:
                self.upsert_item(Item(r["id"], r["name"], r.get("category", ""), tuple(r.get("tags", ())),
                                      r.get("size"), r.get("unit", "each"), tuple(r.get("keywords", ())),
                                      r.get("search", ""), tuple(r.get("exclude", ()))))
            counts["items"] = len(raw)
        if (directory / "stores.json").exists():
            raw = json.loads((directory / "stores.json").read_text())
            for r in raw:
                delivery = DeliveryTerms(**r["delivery"]) if r.get("delivery") else None
                self.upsert_store(Store(r["id"], r["chain"], r["name"], r.get("distance_miles"),
                                        r.get("pickup", True), delivery, r.get("loyalty_member", False)))
            counts["stores"] = len(raw)
        if (directory / "prices.csv").exists():
            counts["products"] = self.upsert_products(read_prices_csv(directory / "prices.csv"))
        return counts

    # --- preferences -----------------------------------------------------

    def preferences(self, settings: Settings | None = None) -> Preferences:
        settings = settings or self.settings()
        brands: dict[str, set[str]] = {}
        for r in self.conn.execute("SELECT * FROM pref_brands"):
            brands.setdefault(r["item_id"], set()).add(r["brand"])
        return Preferences(
            blacklisted_products={r[0] for r in self.conn.execute("SELECT product_key FROM pref_blacklist")},
            blocked={(r[0], r[1]) for r in self.conn.execute("SELECT item_id, chain FROM pref_blocked")},
            own_label_ok={(r[0], r[1]): bool(r[2]) for r in self.conn.execute("SELECT * FROM pref_own_label")},
            brands=brands,
            default_own_label_ok=settings.default_own_label_ok,
        )

    def set_own_label(self, item_id: str, chain: str, ok: bool) -> None:
        self.conn.execute("INSERT OR REPLACE INTO pref_own_label VALUES (?, ?, ?)", (item_id, chain, int(ok)))
        self.conn.commit()

    def add_brand(self, item_id: str, brand: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO pref_brands VALUES (?, ?)", (item_id, brand.casefold()))
        self.conn.commit()

    def block(self, item_id: str, chain: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO pref_blocked VALUES (?, ?)", (item_id, chain))
        self.conn.commit()

    def blacklist(self, product_key: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO pref_blacklist VALUES (?)", (product_key,))
        self.conn.commit()

    def clear_preferences(self, item_id: str) -> None:
        for table in ("pref_blocked", "pref_own_label", "pref_brands"):
            self.conn.execute(f"DELETE FROM {table} WHERE item_id = ?", (item_id,))
        self.conn.execute("DELETE FROM pref_blacklist WHERE product_key IN "
                          "(SELECT chain || ':' || id FROM products WHERE item_id = ?)", (item_id,))
        self.conn.commit()

    # --- settings --------------------------------------------------------

    def settings(self) -> Settings:
        raw = {r[0]: json.loads(r[1]) for r in self.conn.execute("SELECT key, value FROM settings")}
        travel = TravelCosts()
        for key in ("per_mile", "per_stop", "max_distance_miles"):
            if key in raw:
                setattr(travel, key, raw[key])
        settings = Settings(travel=travel)
        for key in ("max_channels", "default_own_label_ok"):
            if key in raw:
                setattr(settings, key, raw[key])
        return settings

    def set_setting(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    # --- shopping list ---------------------------------------------------

    def add_to_list(self, item_id: str, qty: float = 1.0, status: str = "low", note: str = "",
                    added_by: str = "") -> None:
        existing = self.conn.execute("SELECT qty, status FROM list_entries WHERE item_id = ?", (item_id,)).fetchone()
        if existing:
            # Saying it's run out upgrades "running low"; quantities don't stack on repeat adds.
            status = "out" if "out" in (status, existing["status"]) else status
            self.conn.execute("UPDATE list_entries SET qty = ?, status = ?, note = ? WHERE item_id = ?",
                              (max(qty, existing["qty"]), status, note, item_id))
        else:
            self.conn.execute(
                "INSERT INTO list_entries (item_id, qty, status, note, added_by, added_at) VALUES (?,?,?,?,?,?)",
                (item_id, qty, status, note, added_by, _now()))
        self.conn.commit()

    def remove_from_list(self, item_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM list_entries WHERE item_id = ?", (item_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def clear_list(self) -> None:
        self.conn.execute("DELETE FROM list_entries")
        self.conn.commit()

    def shopping_list(self) -> list[ListEntry]:
        return [
            ListEntry(r["item_id"], r["qty"], r["status"], r["note"], r["added_by"], r["id"])
            for r in self.conn.execute("SELECT * FROM list_entries ORDER BY status = 'low', added_at")
        ]

    # --- purchase history ------------------------------------------------

    def add_purchases(self, purchases: Iterable[Purchase]) -> tuple[int, int]:
        """Store purchases, matching names to items. Returns (new rows, rows left unmatched)."""
        items = list(self.items().values())
        mapped = dict(self.conn.execute("SELECT product_name, item_id FROM name_map").fetchall())
        known = {normalise(p.name): p.item_id for p in self.products()}
        added = unmatched = 0
        for p in purchases:
            item_id = (p.item_id or mapped.get(p.product_name) or known.get(normalise(p.product_name))
                       or match_item(p.product_name, items))
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO purchases (date, chain, product_name, qty, price, item_id, source, "
                "txn_id, line_no) VALUES (?,?,?,?,?,?,?,?,?)",
                (p.date, p.chain, p.product_name, p.qty, p.price, item_id, p.source, p.txn_id, p.line_no))
            if cur.rowcount:
                added += 1
                unmatched += item_id is None
        self.conn.commit()
        return added, unmatched

    def purchases(self) -> list[Purchase]:
        return [
            Purchase(r["date"], r["chain"], r["product_name"], r["qty"], r["price"], r["item_id"], r["source"],
                     r["txn_id"], r["line_no"])
            for r in self.conn.execute("SELECT * FROM purchases ORDER BY date")
        ]

    def unmatched_names(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT product_name, COUNT(*) n FROM purchases WHERE item_id IS NULL "
            "GROUP BY product_name ORDER BY n DESC, product_name")]

    def map_name(self, product_name: str, item_id: str) -> int:
        """Remember that `product_name` is `item_id`; returns how many history rows were updated."""
        self.conn.execute("INSERT OR REPLACE INTO name_map VALUES (?, ?)", (product_name, item_id))
        cur = self.conn.execute("UPDATE purchases SET item_id = ? WHERE product_name = ?", (item_id, product_name))
        self.conn.commit()
        return cur.rowcount


def read_prices_csv(path: str | Path) -> list[Product]:
    """Prices CSV: chain,id,item_id,name,brand,tier,size,unit,price[,promo_price,loyalty_price,multibuy]

    `multibuy` is written like "3 for 5.00".
    """
    def num(value: str | None) -> float | None:
        value = (value or "").strip().lstrip("£")
        return float(value) if value else None

    products = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            mq = mp = None
            if (r.get("multibuy") or "").strip():
                qty, _, price = r["multibuy"].partition(" for ")
                mq, mp = int(qty), float(price.strip().lstrip("£"))
            products.append(Product(
                id=r["id"], chain=r["chain"].lower(), item_id=r["item_id"], name=r["name"], price=num(r["price"]),
                brand=r.get("brand", ""), tier=Tier(r.get("tier") or "own"), size=num(r.get("size")),
                unit=r.get("unit") or "each", promo_price=num(r.get("promo_price")),
                loyalty_price=num(r.get("loyalty_price")), multibuy_qty=mq, multibuy_price=mp,
            ))
    return products
