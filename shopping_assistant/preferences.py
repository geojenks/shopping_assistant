"""Household rules for which products are acceptable for each item."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Product

ANY_CHAIN = "*"


@dataclass
class Preferences:
    # Specific products never to buy, by Product.key ("aldi:12345").
    blacklisted_products: set[str] = field(default_factory=set)
    # (item_id, chain): don't buy this item at that chain at all ("Aldi kale is awful").
    blocked: set[tuple[str, str]] = field(default_factory=set)
    # (item_id, chain or "*") -> whether that chain's own label is an acceptable swap.
    own_label_ok: dict[tuple[str, str], bool] = field(default_factory=dict)
    # item_id -> brands you want ("heinz"); own label only allowed where own_label_ok says so.
    brands: dict[str, set[str]] = field(default_factory=dict)
    default_own_label_ok: bool = True

    def own_label_allowed(self, item_id: str, chain: str) -> bool | None:
        """The explicit rule for this item at this chain, if any."""
        for key in ((item_id, chain), (item_id, ANY_CHAIN)):
            if key in self.own_label_ok:
                return self.own_label_ok[key]
        return None

    def allows(self, product: Product) -> bool:
        if product.key in self.blacklisted_products:
            return False
        if (product.item_id, product.chain) in self.blocked:
            return False

        wanted_brands = self.brands.get(product.item_id)
        rule = self.own_label_allowed(product.item_id, product.chain)
        if product.tier.is_own_label:
            if rule is not None:
                return rule
            # Asking for a brand implies own label isn't fine unless you say so.
            return not wanted_brands and self.default_own_label_ok
        if wanted_brands:
            return product.brand.casefold() in wanted_brands
        return True
