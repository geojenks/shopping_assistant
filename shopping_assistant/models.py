"""Core data types shared by the planner, the database and the importers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .units import to_base

PACK_TOLERANCE = 0.1
CHAIN_NAMES = {"sainsburys": "Sainsbury's", "ms": "M&S", "coop": "Co-op"}


def chain_name(chain: str) -> str:
    return CHAIN_NAMES.get(chain, chain.title())


class Tier(str, Enum):
    """How a product sits in a supermarket's range."""

    VALUE = "value"  # Tesco Stockwell, Sainsbury's Stamford Street, Aldi Everyday Essentials
    OWN = "own"  # standard own label
    PREMIUM_OWN = "premium_own"  # Tesco Finest, Taste the Difference, Aldi Specially Selected
    BRANDED = "branded"

    @property
    def is_own_label(self) -> bool:
        return self is not Tier.BRANDED


@dataclass(frozen=True)
class Item:
    """A generic thing you buy, independent of supermarket ("kale", "salted butter")."""

    id: str
    name: str
    category: str = ""
    tags: tuple[str, ...] = ()
    size: float | None = None  # the pack size one "unit" on the list means, in `unit`
    unit: str = "each"
    keywords: tuple[str, ...] = ()  # phrases used to match till-receipt product names
    search: str = ""  # what to type into supermarket search boxes (default: the name)
    exclude: tuple[str, ...] = ()  # words that mean a product isn't this ("unsalted", "spreadable")


@dataclass(frozen=True)
class Product:
    """A specific product sold by a supermarket chain, mapped to a generic item."""

    id: str
    chain: str
    item_id: str
    name: str
    price: float
    brand: str = ""
    tier: Tier = Tier.OWN
    size: float | None = None
    unit: str = "each"
    promo_price: float | None = None
    loyalty_price: float | None = None  # Clubcard / Nectar price
    multibuy_qty: int | None = None  # "3 for £5" -> multibuy_qty=3, multibuy_price=5.0
    multibuy_price: float | None = None
    url: str = ""

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.id}"

    @property
    def on_offer(self) -> bool:
        return any(p is not None for p in (self.promo_price, self.loyalty_price, self.multibuy_price))

    def single_price(self, loyalty_member: bool) -> float:
        candidates = [self.price]
        if self.promo_price is not None:
            candidates.append(self.promo_price)
        if loyalty_member and self.loyalty_price is not None:
            candidates.append(self.loyalty_price)
        return min(candidates)

    def cost(self, packs: int, loyalty_member: bool) -> float:
        each = self.single_price(loyalty_member)
        straight = packs * each
        if self.multibuy_qty and self.multibuy_price is not None:
            groups, rest = divmod(packs, self.multibuy_qty)
            return round(min(straight, groups * self.multibuy_price + rest * each), 2)
        return round(straight, 2)

    def packs_for(self, item: Item, qty: float) -> int:
        """How many of this product cover `qty` of the generic item."""
        want, want_unit = to_base(item.size or 0, item.unit)
        have, have_unit = to_base(self.size or 0, self.unit)
        if want and have and want_unit == have_unit:
            # Coming up to 10% short is fine (a 640g pack for a 650g item, 2 x 300g for 650g).
            return max(1, math.ceil(qty * want / have * (1 - PACK_TOLERANCE)))
        return max(1, math.ceil(qty - 1e-9))


@dataclass(frozen=True)
class DeliveryTerms:
    fee: float
    min_basket: float = 0.0
    # Charge added when the basket is under `min_basket`. None means the minimum
    # is hard: the shop won't deliver below it at all.
    under_min_charge: float | None = None
    free_over: float | None = None
    note: str = ""

    def charges(self, goods: float) -> float | None:
        """Delivery charges for a basket of `goods`, or None if it can't be delivered."""
        fee = 0.0 if self.free_over is not None and goods >= self.free_over else self.fee
        if goods < self.min_basket:
            if self.under_min_charge is None:
                return None
            fee += self.under_min_charge
        return round(fee, 2)


@dataclass(frozen=True)
class Store:
    id: str
    chain: str
    name: str
    distance_miles: float | None = None
    pickup: bool = True
    delivery: DeliveryTerms | None = None
    loyalty_member: bool = False
    lat: float | None = None
    lon: float | None = None


@dataclass
class ListEntry:
    item_id: str
    qty: float = 1.0
    status: str = "low"  # "low" (running low) or "out" (run out)
    note: str = ""
    added_by: str = ""
    id: int | None = None
    text: str = ""


@dataclass(frozen=True)
class Purchase:
    date: str  # ISO date
    chain: str
    product_name: str
    qty: float
    price: float  # total paid for the line
    item_id: str | None = None
    source: str = ""
    txn_id: str = ""
    line_no: int = 0


@dataclass
class TravelCosts:
    """What a pickup stop costs you, beyond the shopping itself."""

    per_mile: float = 0.30  # fuel; round trip miles are counted
    per_stop: float = 4.00  # what an extra stop is worth to you in time and hassle
    per_mile_time: float = 0.50  # what a mile of driving is worth to you, on top of fuel
    max_distance_miles: float | None = 6.0


@dataclass
class Settings:
    travel: TravelCosts = field(default_factory=TravelCosts)
    max_channels: int = 3  # most shops/deliveries combined in one plan
    default_own_label_ok: bool = True
    postcode: str = ""  # some shops (Co-op) price by local store
    browser: dict = field(default_factory=dict)  # BrowserSetup fields chosen by `shop prices doctor`
    home: tuple[float, float] | None = None  # latitude, longitude, from `shop stores find`
