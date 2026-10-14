"""Turn the text of a product tile on a search results page into prices and offers.

Search pages differ in layout, but a product tile's text reads much the same
everywhere: a name, a price, a unit price ("73p/litre"), and offer lines such as
"Clubcard Price £1.50", "Any 3 for £5" or "Was £2.00 Now £1.50".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MONEY = re.compile(r"£\s?(\d+(?:\.\d{1,2})?)|(?<![\d.£])(\d{1,3})p\b", re.I)
UNIT_PRICE = re.compile(r"(/|\bper\b|\beach\b(?!\s*for))", re.I)
MULTIBUY = re.compile(r"\b(?:any\s+)?(\d+)\s+for\s+(?:£\s?(\d+(?:\.\d{1,2})?)|(\d{1,3})p\b)", re.I)
X_FOR_Y = re.compile(r"\b(?:buy\s+)?(\d+)\s+for\s+(?:the\s+price\s+of\s+)?(\d+)\b(?!\s*(?:p\b|\.))", re.I)
NOW = re.compile(r"\bnow\b", re.I)
WAS = re.compile(r"\b(was|ordinarily)\b", re.I)  # Ocado says "Ordinarily £2.65"
# Prices shown side by side run together in page text: "£1.6573p / ltr" is £1.65 and 73p/ltr.
GLUED = re.compile(r"(£\s?\d+\.\d{2}|(?<![\d.£])\d{1,3}p)(?=£|\d)")
# Iceland splits offers over lines: "2 / for / £3.00" and "Mix & Match from / £1.50 / each".
SPLIT_MULTIBUY = re.compile(r"\b(\d+)\s*\n\s*for\s*\n\s*(£\s?\d+(?:\.\d{1,2})?|\d{1,3}p)\b", re.I)
SPLIT_FROM = re.compile(r"\bfrom\s*\n\s*(£\s?\d+(?:\.\d{1,2})?|\d{1,3}p)\s*\n\s*each\b", re.I)
NOISE = re.compile(r"^(add|write a review|rest of shelf|quantity|sponsored|new|offer|view|save|"
                   r"\d+(\.\d)? out of 5|\(\d+\)|reviews?|\d+ reviews?)$", re.I)


@dataclass
class Listing:
    chain: str
    id: str
    name: str
    url: str = ""
    price: float | None = None
    loyalty_price: float | None = None
    multibuy_qty: int | None = None
    multibuy_price: float | None = None
    unit_price: str = ""
    offers: list[str] = field(default_factory=list)
    available: bool = True


def _money(text: str) -> float | None:
    m = MONEY.search(text)
    if not m:
        return None
    return float(m[1]) if m[1] else int(m[2]) / 100


def parse_tile(chain: str, product_id: str, name: str, text: str, url: str = "",
               loyalty_words: tuple[str, ...] = ()) -> Listing:
    listing = Listing(chain=chain, id=product_id, name=" ".join(name.split()), url=url)
    text = SPLIT_FROM.sub(r"from \1 each", SPLIT_MULTIBUY.sub(r"\1 for \2", text))
    lines = [" ".join(l.split()) for l in GLUED.sub(r"\1\n", text).splitlines()]
    lines = [l for l in lines if l and l.casefold() != listing.name.casefold() and not NOISE.match(l)]
    loyalty = tuple(w.casefold() for w in loyalty_words)
    shelf: list[float] = []  # standalone price lines
    loyalty_label = False  # a "Nectar price" badge with the price on a line of its own

    for line in lines:
        low = line.casefold()
        if re.search(r"(currently )?(out of stock|unavailable|not available)", low):
            listing.available = False
            continue
        if not MONEY.search(line) and not X_FOR_Y.search(line):
            loyalty_label = loyalty_label or any(w in low for w in loyalty)
            continue
        if loyalty and any(w in low for w in loyalty):
            listing.offers.append(line)
            mb = MULTIBUY.search(line)
            if mb:  # "Any 2 for £3 Clubcard Price": a members-only multibuy
                listing.multibuy_qty = int(mb[1])
                listing.multibuy_price = float(mb[2]) if mb[2] else int(mb[3]) / 100
            else:
                listing.loyalty_price = _money(line)
            continue
        mb = MULTIBUY.search(line)
        if mb:
            listing.offers.append(line)
            listing.multibuy_qty = int(mb[1])
            listing.multibuy_price = float(mb[2]) if mb[2] else int(mb[3]) / 100
            continue
        if WAS.search(line) and NOW.search(line):
            listing.offers.append(line)
            amounts = [_money(part) for part in NOW.split(line, maxsplit=1)]
            if amounts[1] is not None:
                listing.price = amounts[1]
            continue
        if WAS.search(line):  # "Was £2.00": the shelf price is shown elsewhere
            listing.offers.append(line)
            continue
        if UNIT_PRICE.search(line) or "(" in line:
            if not listing.unit_price:
                listing.unit_price = line.strip("()")
            continue
        xy = X_FOR_Y.search(line)
        if xy and not MONEY.search(line):
            listing.offers.append(line)
            continue
        amount = _money(line)
        # A price line is short: "£1.65", "Now £1.50", "£1.65 each".
        if amount is not None and len(MONEY.sub("", line).strip(" -:")) <= 12:
            shelf.append(amount)

    if listing.price is None and shelf:
        if loyalty_label and listing.loyalty_price is None and len(shelf) >= 2:
            # Two prices beside a loyalty badge: the card price and the usual price.
            listing.loyalty_price, listing.price = min(shelf[:2]), max(shelf[:2])
        else:
            listing.price = shelf[0]

    # "3 for 2"-style offers need the shelf price.
    if listing.price is not None and listing.multibuy_qty is None:
        for line in listing.offers:
            xy = X_FOR_Y.search(line)
            if xy and not MONEY.search(line) and int(xy[1]) > int(xy[2]):
                listing.multibuy_qty = int(xy[1])
                listing.multibuy_price = round(int(xy[2]) * listing.price, 2)
    if listing.loyalty_price is not None and listing.price is not None and listing.loyalty_price >= listing.price:
        listing.loyalty_price = None
    return listing
