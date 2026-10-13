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


# --- real tile text captured from the sites (October 2026) ------------------

from shopping_assistant.prices.browser import tiles_to_listings  # noqa: E402

REAL_TILES = {
    "sainsburys": [
        {"href": "https://www.sainsburys.co.uk/groceries/product/sainsburys-british-semi-skimmed-milk-2-27l-4-pint",
         "name": "Sainsbury's British Semi Skimmed Milk 2.27L (4 pint)",
         "text": "Add to Favourites\nSainsbury's British Semi Skimmed Milk 2.27L (4 pint)\n4.6/5(2147)\nBRITISH\n"
                 "ALDI PRICE MATCH*\n£1.6573p / ltr\nAdd"},
        {"href": "https://www.sainsburys.co.uk/groceries/product/cravendale-filtered-fresh-semi-skimmed-milk-2l",
         "name": "Cravendale Filtered Fresh Semi Skimmed Milk 2L Fresher for Longer",
         "text": "Nectar price\nAdd to Favourites\nCravendale Filtered Fresh Semi Skimmed Milk 2L Fresher for Longer\n"
                 "4.7/5(326)\nCHILLED\n£2.00£1.00 / ltr\n£2.65£1.33 / ltr\nAdd"},
    ],
    "aldi": [
        {"href": "https://www.aldi.co.uk/product/cowbelle-semi-skimmed-milk-1-7-fat-000000000000416770",
         "name": "COWBELLE\n\nSemi Skimmed Milk 1.7% Fat\n\n2.272 l\n\n(£0.73/1 L)\n\n£1.65",
         "text": "COWBELLE\n\nSemi Skimmed Milk 1.7% Fat\n\n2.272 l\n\n(£0.73/1 L)\n\n£1.65"},
    ],
    "waitrose": [
        {"href": "https://www.waitrose.com/ecom/products/essential-free-range-white-eggs/856706-653765-653766",
         "name": "view product details for",
         "text": "0 in trolley\n\n0 Essential Free Range White Eggs in trolley.\nview product details for\n"
                 "Essential Free Range White Eggs\n6s\n4.5 out of 5 stars\n(1800)\n\nItem price\n\n£1.40\n\n"
                 "Price per unit\n\n23.3p each\nAdd"},
        {"href": "https://www.waitrose.com/ecom/products/cravendale-filtered-semi-skimmed-milk/062593-31787-31788",
         "name": "view product details for",
         "text": "0 in trolley\n\n0 Cravendale Filtered Fresher for Longer Semi-Skimmed Milk in trolley.\n"
                 "view product details for\nCravendale Filtered Fresher for Longer Semi-Skimmed Milk\n2litre\n"
                 "4.5 out of 5 stars\n(312)\n\nAny 2 for £4 Mix & Match\n\nItem price\n\n£2.90\n\nPrice per unit\n\n"
                 "£1.45/litre\nAdd"},
    ],
    "ms": [
        {"href": "https://www.ocado.com/products/m-s-curly-kale/518558011", "name": "M&S Curly Kale",
         "text": "M&S Curly Kale\nLife 3d+\nProduct life guaranteed for 3 days excluding delivery day.\nVegetarian\n"
                 "(103)\nRating, 3.8 out of 5 from 103 reviews.\nM&S Curly Kale\n125g\n"
                 "Ordinarily £14.40 per kilo\n(£14.40 per kilo)\nPrice\n£1.80\nAdd"},
    ],
}


def real(chain):
    return [(l.id, l.name, l.price, l.loyalty_price, l.multibuy_qty, l.multibuy_price)
            for l in tiles_to_listings(SITES[chain], REAL_TILES[chain])]


def test_sainsburys_split_prices_and_nectar_badge():
    assert real("sainsburys") == [
        ("sainsburys-british-semi-skimmed-milk-2-27l-4-pint", "Sainsbury's British Semi Skimmed Milk 2.27L (4 pint)",
         1.65, None, None, None),
        ("cravendale-filtered-fresh-semi-skimmed-milk-2l", "Cravendale Filtered Fresh Semi Skimmed Milk 2L Fresher for Longer",
         2.65, 2.00, None, None),
    ]


def test_aldi_whole_tile_link_gets_brand_and_size_in_name():
    assert real("aldi") == [("000000000000416770", "Cowbelle Semi Skimmed Milk 1.7% Fat 2.272 l", 1.65, None, None, None)]
    assert tier_and_brand(SITES["aldi"], "Cowbelle Semi Skimmed Milk 1.7% Fat 2.272 l") == (Tier.OWN, "")


def test_waitrose_skips_boilerplate_and_takes_size_line():
    assert real("waitrose") == [
        ("856706-653765-653766", "Essential Free Range White Eggs 6s", 1.40, None, None, None),
        ("062593-31787-31788", "Cravendale Filtered Fresher for Longer Semi-Skimmed Milk 2litre", 2.90, None, 2, 4.0),
    ]


def test_ocado_size_line_and_ordinarily_is_not_the_price():
    assert real("ms") == [("518558011", "M&S Curly Kale 125g", 1.80, None, None, None)]


def test_products_without_a_pack_size_are_skipped_when_the_item_has_one():
    bananas = Item("bananas", "Bananas", size=5, unit="each", keywords=("bananas",))
    assert not is_item(bananas, "Waitrose Fairtrade Loose Bananas")
    assert is_item(bananas, "Essential Fairtrade Bananas 5s")


@pytest.mark.parametrize("name, count", [
    ("Sainsbury's British Free Range Eggs Medium x12", 12),
    ("Stamford Street Co. 10 x Mixed Size British Free Range Eggs 536g", 10),
    ("PG Tips Original Tea Bags 80s 232g", 80),
])
def test_counts_written_different_ways(name, count):
    assert size_in(name, "each") == count
