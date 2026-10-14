"""Search each chain for each item, keep the results that really are that item, store them as products."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol

from ..matching import keyword_matches, normalise
from ..models import Item, Product, Tier
from ..units import size_in, to_base
from .sites import Site
from .tiles import Listing

# Pack sizes further than this from the item's usual size are probably a different product
# (a 4-pint milk is not a 1-pint one), though the planner would still cost them correctly.
SIZE_RANGE = (0.4, 2.5)

NATIONAL_BRANDS = (
    "heinz", "yorkshire tea", "pg tips", "twinings", "tetley", "lavazza", "kenco", "nescafe", "nescafé",
    "lurpak", "anchor", "country life", "cathedral city", "philadelphia", "president", "arla", "cravendale",
    "yeo valley", "fage", "muller", "müller", "activia", "alpro", "oatly", "warburtons", "hovis", "kingsmill",
    "walkers", "kettle", "pringles", "doritos", "mccoy's", "sensations", "tyrrells", "kellogg's", "weetabix",
    "quaker", "nestle", "nestlé", "cadbury", "mcvitie's", "napolina", "dolmio", "loyd grossman", "uncle ben's",
    "ben's original", "tilda", "barilla", "de cecco", "branston", "hellmann's", "colman's", "bisto", "oxo",
    "fairy", "ecover", "method", "persil", "fairy non bio", "comfort", "lenor", "andrex", "cushelle",
    "velvet", "kleenex", "plenty", "bounty", "coca-cola", "pepsi", "robinsons", "innocent", "tropicana",
    "birds eye", "young's", "mccain", "richmond", "wall's", "bernard matthews", "the laughing cow",
    "dairylea", "babybel", "flora", "bertolli", "clover", "utterly butterly", "elmlea", "princes", "john west",
)


def tier_and_brand(site: Site, name: str) -> tuple[Tier, str]:
    tier = site.own_label(name)
    if tier is not None:
        return tier, ""
    n = name.casefold()
    brand = next((b for b in sorted(NATIONAL_BRANDS, key=len, reverse=True) if n.startswith(b)), None)
    if brand:
        return Tier.BRANDED, brand
    if site.all_own_label:
        return Tier.OWN, ""
    return Tier.BRANDED, n.split()[0] if n else ""


def is_item(item: Item, name: str) -> bool:
    phrases = item.keywords or (item.name,)
    if not any(keyword_matches(p, name) for p in phrases):
        return False
    if any(keyword_matches(x, name) for x in item.exclude):
        return False
    if item.size:
        have = size_in(name, item.unit)
        if have is None:
            # Without a pack size we can't compare it fairly ("Loose Bananas 18p" is one banana).
            return False
        ratio = have / to_base(item.size, item.unit)[0]
        if not SIZE_RANGE[0] <= ratio <= SIZE_RANGE[1]:
            return False
    return True


def to_product(site: Site, item: Item, listing: Listing) -> Product:
    tier, brand = tier_and_brand(site, listing.name)
    return Product(
        id=listing.id, chain=site.chain, item_id=item.id, name=listing.name, price=listing.price,
        brand=brand, tier=tier, size=size_in(listing.name, item.unit), unit=to_base(1, item.unit)[1],
        loyalty_price=listing.loyalty_price, multibuy_qty=listing.multibuy_qty,
        multibuy_price=listing.multibuy_price, url=listing.url,
    )


class Searcher(Protocol):
    def search(self, site: Site, query: str) -> list[Listing]: ...


@dataclass
class Outcome:
    chain: str
    item_id: str
    found: int = 0  # listings on the results page
    products: list[Product] = field(default_factory=list)  # those that are this item
    error: str = ""


def collect(items: Iterable[Item], sites: Iterable[Site], searcher: Searcher,
            on_result: Callable[[Outcome], None] | None = None, max_per_item: int = 10) -> list[Outcome]:
    outcomes = []
    sites = list(sites)
    for item in items:
        query = item.search or item.name
        for site in sites:
            outcome = Outcome(site.chain, item.id)
            try:
                listings = searcher.search(site, query)
            except Exception as e:  # one bad page shouldn't stop the run
                outcome.error = f"{type(e).__name__}: {e}".splitlines()[0][:200]
            else:
                outcome.found = len(listings)
                matches = [to_product(site, item, l) for l in listings if l.available and is_item(item, l.name)]
                # Keep the best value per kg / litre / item, not just the first on the page.
                outcome.products = sorted(matches, key=lambda p: p.price / p.size if p.size else p.price)[:max_per_item]
            outcomes.append(outcome)
            if on_result:
                on_result(outcome)
    return outcomes
