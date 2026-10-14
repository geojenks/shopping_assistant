"""The food bank: every product on a supermarket's shelves, with the aisle and shelf it sits on.

Crawled from the shop's own aisle pages (Aldi first: about 2,000 lines). It lets you
browse real products by aisle, find things by name instantly without a live search,
and tells search results apart by what kind of thing they are ("almond butter" the
spread sits on "Jams, Spreads & Syrups", not "Breakfast Pastries").
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable

from .matching import normalise

ALDI_HOME = "https://www.aldi.co.uk/"
ALDI_SHELF = re.compile(r"aldi\.co\.uk/products/([^/?#]+)/([^/?#]+)/k/\d+$")
PAGE_SIZE = 30  # Aldi shows 30 products a page: ?page=2 and so on

# Aldi aisle -> our aisle, with shelves that belong elsewhere. Aisles not listed (Specialbuys,
# seasonal ranges, Price Drops...) aren't crawled; neither are shelves mapped to None.
ALDI_AISLES = {
    "fresh-food": "Fruit & veg", "food-cupboard": "Food cupboard", "chilled-food": "Dairy, eggs & chilled",
    "alcohol": "Beer, wine & spirits", "drinks": "Drinks", "frozen-food": "Frozen", "bakery": "Bakery",
    "home-essentials": "Household", "health-beauty": "Health & beauty", "baby-toddler": "Baby",
    "pet-care": "Pets",
}
ALDI_SHELVES = {
    **{s: "Meat & fish" for s in ("poultry", "beef", "pork-gammon", "bacon-sausages", "lamb", "game-venison",
                                   "black-pudding-haggis-stuffing", "fish", "seafood-prawns", "breaded-fish-fishcakes")},
    **{s: "Snacks & sweets" for s in ("biscuits-crackers", "chocolate-sweets", "crisps-snacks")},
    **{s: None for s in ("flowers", "candles-reed-diffusers", "batteries", "carrier-bags", "dog-accessories")},
}


@dataclass(frozen=True)
class BankProduct:
    chain: str
    id: str
    name: str
    price: float
    aisle: str  # ours: "Food cupboard"
    shelf: str  # the shop's: "Jams, Spreads & Syrups"
    url: str = ""
    loyalty_price: float | None = None
    multibuy_qty: int | None = None
    multibuy_price: float | None = None


def aldi_shelves(fetcher) -> list[tuple[str, str, str]]:
    """(url, our aisle, Aldi's shelf name) for every grocery shelf in Aldi's menu."""
    fetcher.open(ALDI_HOME)
    links = fetcher.page.evaluate(
        "() => [...document.querySelectorAll('a[href]')].map(a => [a.href.split('?')[0], (a.innerText || '').trim()])")
    shelves, seen = [], set()
    for url, text in links:
        m = ALDI_SHELF.search(url)
        if not m or url in seen or m[1] not in ALDI_AISLES:
            continue
        aisle = ALDI_SHELVES.get(m[2], ALDI_AISLES[m[1]])
        if aisle is None:
            continue
        seen.add(url)
        shelves.append((url, aisle, text or m[2].replace("-", " ").title()))
    return shelves


def crawl_aldi(fetcher, on_progress: Callable[[str], None] = print, max_pages: int = 20) -> list[BankProduct]:
    from .prices.browser import tiles_to_listings
    from .prices.sites import SITES

    site = SITES["aldi"]
    shelves = aldi_shelves(fetcher)
    found: dict[str, BankProduct] = {}
    for n, (url, aisle, shelf) in enumerate(shelves, 1):
        count = 0
        for page in range(1, max_pages + 1):
            fetcher.open(url if page == 1 else f"{url}?page={page}")
            tiles = fetcher.tiles(site)
            for l in tiles_to_listings(site, tiles):
                if l.id not in found:  # a product on two shelves keeps the first
                    found[l.id] = BankProduct("aldi", l.id, l.name, l.price, aisle, shelf, l.url, l.loyalty_price,
                                              l.multibuy_qty, l.multibuy_price)
                    count += 1
            if len(tiles) < PAGE_SIZE:
                break
        on_progress(f"  {n:3}/{len(shelves)}  {aisle} › {shelf}: {count} products")
    return list(found.values())


CRAWLERS = {"aldi": crawl_aldi}


# --- telling what kind of thing a product is -----------------------------------

def _features(name: str) -> set[str]:
    """Words (singular) and neighbouring pairs ("peanut butter"), without filler."""
    from .matching import FILLER

    words = [w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
             for w in normalise(name).split() if w not in FILLER and not w.isdigit() and len(w) > 1]
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:])}


class ShelfGuesser:
    """Guesses a product's shelf from its name, using the names of products already on shelves.

    Each word, and each pair of neighbouring words, votes for the shelves it appears on;
    rare ones loudest ("croissants" says Breakfast Pastries far more clearly than "butter"
    says anything, and "peanut butter" says Spreads more clearly than either word alone).
    """

    def __init__(self, products: Iterable[BankProduct]):
        self.shelves: dict[str, Counter] = defaultdict(Counter)  # feature -> shelf counts
        self.aisle_of: dict[str, str] = {}
        documents = 0
        for p in products:
            documents += 1
            self.aisle_of[p.shelf] = p.aisle
            for feature in _features(p.name):
                self.shelves[feature][p.shelf] += 1
        self.idf = {f: math.log((1 + documents) / (1 + sum(c.values()))) + 1 for f, c in self.shelves.items()}

    def guess(self, name: str) -> tuple[str, str] | None:
        """(our aisle, shop's shelf) for a product name, or None if its words are unknown."""
        scores: Counter = Counter()
        for feature in _features(name):
            counts = self.shelves.get(feature)
            if not counts:
                continue
            total = sum(counts.values())
            weight = self.idf[feature] * (2 if " " in feature else 1)
            for shelf, n in counts.items():
                scores[shelf] += weight * n / total
        if not scores:
            return None
        shelf = scores.most_common(1)[0][0]
        return self.aisle_of[shelf], shelf
