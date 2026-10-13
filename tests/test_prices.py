import os
from dataclasses import replace
from pathlib import Path

import pytest

from shopping_assistant.db import Database
from shopping_assistant.models import Item, Tier
from shopping_assistant.prices.collect import collect, is_item, tier_and_brand
from shopping_assistant.prices.sites import SITES, Site
from shopping_assistant.prices.tiles import Listing, parse_tile
from shopping_assistant.units import parse_sizes, size_in

FIXTURES = Path(__file__).parent / "fixtures"
MILK = Item("milk", "Semi-skimmed milk", size=4, unit="pint", keywords=("semi skimmed milk",),
            exclude=("organic",))


# --- sizes ----------------------------------------------------------------

@pytest.mark.parametrize("name, sizes", [
    ("Yorkshire Tea 80 Tea Bags 250g", [(80, "each"), (250, "g")]),
    ("Walkers Crisps 6 x 25g", [(150, "g"), (6, "each")]),
    ("Tesco Semi Skimmed Milk 2.272L, 4 Pints", [(2272, "ml")]),
    ("Andrex Toilet Tissue 9 Rolls", [(9, "each")]),
    ("Fairy Washing Up Liquid 1015Ml", [(1015, "ml")]),
    ("Loose Bananas", []),
])
def test_parse_sizes(name, sizes):
    assert parse_sizes(name) == sizes


def test_size_in_converts_to_the_items_unit():
    assert size_in("Milk 2 Pints", "pint") == 1136
    assert size_in("Milk 1.136L", "pint") == 1136
    assert size_in("Kale 200g", "kg") == 200


# --- tile text --------------------------------------------------------------

def tile(text, loyalty=("clubcard price",), name="Product"):
    return parse_tile("x", "1", name, text, loyalty_words=loyalty)


def test_shelf_and_loyalty_price():
    t = tile("Product\n£1.65\n£0.73/litre\nClubcard Price\n£1.50 Clubcard Price\nAdd")
    assert (t.price, t.loyalty_price, t.unit_price) == (1.65, 1.50, "£0.73/litre")


def test_pence_and_member_price():
    t = tile("85p\n17p each\nMember Price 75p", loyalty=("member price",))
    assert (t.price, t.loyalty_price) == (0.85, 0.75)


def test_multibuy_offers():
    assert (tile("Any 4 for £4.50\n£1.40").multibuy_qty, tile("Any 4 for £4.50\n£1.40").multibuy_price) == (4, 4.5)
    t = tile("£1.00\nBuy 3 for 2")
    assert (t.multibuy_qty, t.multibuy_price) == (3, 2.0)
    t = tile("£1.00\nAny 2 for £1.50 Clubcard Price")  # members-only multibuy
    assert (t.multibuy_qty, t.multibuy_price, t.loyalty_price) == (2, 1.5, None)


def test_was_now_takes_the_current_price():
    t = tile("Was £3.25 Now £2.95\n£3.69 / 100 Tea bags\n£2.50 with Nectar", loyalty=("with nectar",))
    assert (t.price, t.loyalty_price) == (2.95, 2.50)


def test_unavailable():
    assert not tile("Currently unavailable\n£1.20").available


# --- deciding what a product is -------------------------------------------

def test_is_item_checks_words_exclusions_and_size():
    assert is_item(MILK, "Tesco Semi Skimmed Milk 2.272L, 4 Pints")
    assert is_item(MILK, "Cravendale Semi Skimmed Milk 2L")  # 2L is close enough to 4 pints
    assert not is_item(MILK, "Tesco Semi Skimmed Milk 568ml, 1 Pint")  # far too small
    assert not is_item(MILK, "M&S Organic Semi Skimmed Milk 4 Pints")  # excluded word
    assert not is_item(MILK, "Tesco Whole Milk 4 Pints")


@pytest.mark.parametrize("chain, name, expected", [
    ("tesco", "Tesco Finest Mature Cheddar", (Tier.PREMIUM_OWN, "")),
    ("tesco", "Stockwell & Co Baked Beans", (Tier.VALUE, "")),
    ("tesco", "Heinz Baked Beans 415G", (Tier.BRANDED, "heinz")),
    ("tesco", "Walkers Salt & Vinegar", (Tier.BRANDED, "walkers")),
    ("aldi", "Semi Skimmed Milk 4 Pints", (Tier.OWN, "")),  # Aldi tiles often omit the brand
    ("aldi", "Yorkshire Tea 80 Tea Bags", (Tier.BRANDED, "yorkshire tea")),
    ("ms", "M&S Collection Lemon Curd", (Tier.PREMIUM_OWN, "")),
    ("sainsburys", "Sainsbury's Taste the Difference Butter", (Tier.PREMIUM_OWN, "")),
    ("waitrose", "Essential Waitrose Kale 200g", (Tier.VALUE, "")),
    ("coop", "Co-op Irresistible Crisps", (Tier.PREMIUM_OWN, "")),
])
def test_tier_and_brand(chain, name, expected):
    assert tier_and_brand(SITES[chain], name) == expected


class FakeSearcher:
    def __init__(self, results):
        self.results = results

    def search(self, site, query):
        result = self.results[site.chain]
        if isinstance(result, Exception):
            raise result
        return result


def test_collect_keeps_matching_products_and_survives_a_failing_site():
    good = Listing("tesco", "1", "Tesco Semi Skimmed Milk 2.272L, 4 Pints", price=1.65, loyalty_price=1.5)
    wrong = Listing("tesco", "2", "Tesco Whole Milk 4 Pints", price=1.65)
    gone = Listing("tesco", "3", "Cravendale Semi Skimmed Milk 2L", price=2.25, available=False)
    outcomes = collect([MILK], [SITES["tesco"], SITES["aldi"]],
                       FakeSearcher({"tesco": [good, wrong, gone], "aldi": TimeoutError("page took too long")}))
    tesco, aldi = outcomes
    assert tesco.found == 3 and [p.id for p in tesco.products] == ["1"]
    [p] = tesco.products
    assert (p.tier, p.size, p.unit, p.loyalty_price) == (Tier.OWN, 2272, "ml", 1.5)
    assert aldi.error.startswith("TimeoutError")


def test_replace_products_and_history(tmp_path):
    db = Database(tmp_path / "shop.db")
    db.upsert_item(MILK)
    site = SITES["tesco"]
    from shopping_assistant.prices.collect import to_product
    first = to_product(site, MILK, Listing("tesco", "1", "Tesco Semi Skimmed Milk 4 Pints", price=1.65))
    other = to_product(site, MILK, Listing("tesco", "2", "Arla Semi Skimmed Milk 4 Pints", price=1.95))
    db.replace_products("tesco", "milk", [first, other])
    db.replace_products("tesco", "milk", [replace(first, price=1.55)])
    assert [(p.id, p.price) for p in db.products("milk")] == [("1", 1.55)]
    assert db.conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 2  # same day: overwritten


def test_old_databases_are_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE items (id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]', size REAL, unit TEXT NOT NULL DEFAULT 'each',
            keywords TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE products (chain TEXT NOT NULL, id TEXT NOT NULL, item_id TEXT NOT NULL, name TEXT NOT NULL,
            brand TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'own', size REAL,
            unit TEXT NOT NULL DEFAULT 'each', price REAL NOT NULL, promo_price REAL, loyalty_price REAL,
            multibuy_qty INTEGER, multibuy_price REAL, updated_at TEXT, PRIMARY KEY (chain, id));
        INSERT INTO items (id, name) VALUES ('milk', 'Milk');
        INSERT INTO products (chain, id, item_id, name, price) VALUES ('tesco', '1', 'milk', 'Milk', 1.65);
    """)
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.items()["milk"].search == ""
    assert [p.price for p in db.products()] == [1.65]


# --- the browser end, against hand-made pages ------------------------------

@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    pytest.importorskip("playwright")
    from shopping_assistant.prices.browser import BrowserFetcher
    if not os.environ.get("SHOP_CHROMIUM") and Path("/opt/pw-browsers/chromium").exists():
        os.environ["SHOP_CHROMIUM"] = "/opt/pw-browsers/chromium"
    try:
        fetcher = BrowserFetcher(tmp_path_factory.mktemp("profile"), delay=(0, 0))
    except Exception as e:
        pytest.skip(f"no browser available: {e}")
    yield fetcher
    fetcher.close()


def local(site: Site, fixture: str) -> Site:
    return replace(site, search_url=(FIXTURES / fixture).as_uri() + "?q={query}")


def test_browser_reads_tiles_with_separate_links(browser):
    site = local(SITES["tesco"], "search_separate_links.html")
    listings = {l.id: l for l in browser.search(site, "semi skimmed milk")}
    assert set(listings) == {"254656543", "299882211", "311122233", "300000001"}
    milk = listings["254656543"]
    assert (milk.name, milk.price, milk.loyalty_price) == ("Tesco Semi Skimmed Milk 2.272L, 4 Pints", 1.65, 1.5)
    assert (listings["299882211"].multibuy_qty, listings["299882211"].multibuy_price) == (2, 4.0)
    assert listings["311122233"].price == 0.75
    assert not listings["300000001"].available
    # And end to end: only the 4-pint and 2L semi-skimmed are this item.
    [outcome] = collect([MILK], [site], browser)
    assert sorted(p.id for p in outcome.products) == ["254656543", "299882211"]


def test_browser_reads_whole_tile_links_and_filters_to_ms(browser):
    site = local(SITES["ms"], "search_whole_tile_links.html")
    listings = browser.search(site, "semi skimmed milk")
    assert [(l.id, l.name, l.price) for l in listings] == [
        ("512345", "M&S British Semi Skimmed Milk 4 Pints", 1.70),
        ("598765", "M&S Organic Semi Skimmed Milk 2 Pints", 1.35),
    ]
    assert (listings[1].multibuy_qty, listings[1].multibuy_price) == (3, 2.70)
