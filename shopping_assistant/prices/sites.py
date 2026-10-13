"""Where each chain's product search lives, and how to tell its own label from brands.

These are the bits most likely to need updating when a supermarket changes its
website. `shop prices probe CHAIN QUERY` shows what we get back from a site, so
it's quick to see what needs changing here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from ..models import Tier


@dataclass(frozen=True)
class Site:
    chain: str
    search_url: str  # "{query}" is replaced with the url-encoded search
    product_link: str  # regex matching links to a product page; group 1 is the product id if present
    premium: tuple[str, ...] = ()  # name prefixes for premium own label
    value: tuple[str, ...] = ()  # value own label
    own: tuple[str, ...] = ()  # standard own label and the chain's exclusive brands
    all_own_label: bool = False  # unless it's a known national brand (M&S, Aldi)
    only_prefix: tuple[str, ...] = ()  # keep only products whose name starts with one of these
    loyalty_words: tuple[str, ...] = ()
    note: str = ""
    extra_wait_ms: int = 0
    postcode_input: str = ""  # selector for a "your postcode" box shown before searching is allowed

    def url_for(self, query: str) -> str:
        return self.search_url.format(query=quote_plus(query))

    def product_id(self, url: str) -> str:
        m = re.search(self.product_link, url)
        if m and m.groups() and m[1]:
            return m[1]
        return url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]

    def own_label(self, name: str) -> Tier | None:
        """The own-label tier if the name starts with one of this chain's own labels."""
        n = name.casefold()
        for tier, prefixes in ((Tier.PREMIUM_OWN, self.premium), (Tier.VALUE, self.value), (Tier.OWN, self.own)):
            if any(n.startswith(p) for p in prefixes):
                return tier
        return None


SITES: dict[str, Site] = {s.chain: s for s in [
    Site(
        chain="tesco",
        # Tesco moved from /groceries/en-GB/ to /shop/en-GB/ in 2026; accept both.
        search_url="https://www.tesco.com/shop/en-GB/search?query={query}",
        product_link=r"/(?:shop|groceries)/en-GB/products/(\d+)",
        premium=("tesco finest", "finest"),
        value=("stockwell", "tesco stockwell", "hearty food co", "ms molly's", "grower's harvest", "eastman's",
               "creamfields", "h.w. nevill's", "h. w. nevill's"),
        own=("tesco", "woodside farms", "boswell farms", "nightingale farms", "rosedene farms", "willow farms",
             "suntrail farms", "redmere farms", "fresh ideas", "romano"),
        loyalty_words=("clubcard price", "clubcard"),
        note="Blocks servers (Akamai): works from a home connection. Tile layout not yet checked.",
    ),
    Site(
        chain="sainsburys",
        search_url="https://www.sainsburys.co.uk/groceries/search?searchTerm={query}",
        product_link=r"/(?:groceries|gol-ui)/product/([^/?#]+)",
        premium=("sainsbury's taste the difference", "taste the difference"),
        value=("stamford street", "sainsbury's stamford street", "hubbard's"),
        own=("sainsbury's", "by sainsbury's", "so organic", "js "),
        loyalty_words=("nectar price", "with nectar", "nectar"),
    ),
    Site(
        chain="aldi",
        search_url="https://www.aldi.co.uk/results?q={query}",
        product_link=r"/product/[^/?#]*?-(\d{6,})",
        premium=("specially selected",),
        value=("everyday essentials", "aldi everyday essentials"),
        own=("aldi", "cowbelle", "village bakery", "nature's pick", "ashfield farm", "corale", "cucina", "alcafe",
             "saxon", "magnum", "snackrite", "brooklea", "emporium", "merevale", "harvest morn", "the deli",
             "the fishmonger", "the pantry", "four seasons", "bramwells", "dairyfine", "gianni's", "mamia",
             "lacura", "oaklands", "belmont", "baker street"),
        all_own_label=True,  # tiles often show the product without its Aldi brand name
        note="No delivery: prices are for the shops (click & collect where offered).",
    ),
    Site(
        chain="waitrose",
        search_url="https://www.waitrose.com/ecom/shop/search?&searchTerm={query}",
        product_link=r"/ecom/products/[^/]+/([\w-]+)",
        premium=("waitrose no.1", "no.1"),
        value=("essential waitrose", "waitrose essential", "essential"),
        own=("waitrose", "duchy organic", "waitrose duchy", "waitrose & partners"),
        loyalty_words=("mywaitrose", "my waitrose"),
    ),
    Site(
        chain="ms",
        # M&S food is sold online through Ocado. Shop prices are usually the same but can differ.
        search_url="https://www.ocado.com/search?q=M%26S+{query}",
        product_link=r"/products/[^?#]*?[/-](\d{4,})",
        premium=("m&s collection", "m&s our best ever", "m&s plant kitchen"),
        own=("m&s", "marks & spencer"),
        all_own_label=True,
        only_prefix=("m&s",),
        loyalty_words=("sparks",),
        note="Prices from M&S's range on Ocado; in-store prices can differ slightly.",
    ),
    Site(
        chain="ocado",
        search_url="https://www.ocado.com/search?q={query}",
        product_link=r"/products/[^?#]*?[/-](\d{4,})",
        own=("ocado", "m&s"),
        premium=("m&s collection",),
    ),
    Site(
        chain="coop",
        search_url="https://shop.coop.co.uk/search?term={query}",
        product_link=r"/product/([^/?#]+)",
        premium=("co-op irresistible", "irresistible"),
        value=("co-op honest value", "honest value"),
        own=("co-op", "coop", "the co-operative"),
        loyalty_words=("member price", "members price", "member"),
        postcode_input="input#postcode",  # it sends you to /welcome to pick your local store first
        note="Needs your postcode (shop set postcode ...). Blocks servers: run from home.",
    ),
]}
