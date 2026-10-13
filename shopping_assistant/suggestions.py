"""Ideas for things to try alongside what's on the list ("other crisp flavours")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Item, Product, chain_name
from .preferences import Preferences


@dataclass(frozen=True)
class Suggestion:
    item: Item
    reason: str
    product: Product | None = None


def related(
    item: Item,
    items: Iterable[Item],
    products: Iterable[Product],
    prefs: Preferences,
    bought: set[str],
    limit: int = 5,
) -> list[Suggestion]:
    """Items in the same category or sharing a theme tag, deals and new things first."""
    themes = set(item.tags)
    products = [p for p in products if prefs.allows(p)]
    candidates = []
    for other in items:
        if other.id == item.id:
            continue
        shared = themes & set(other.tags)
        if not shared and not (item.category and other.category == item.category):
            continue
        offers = [p for p in products if p.item_id == other.id and p.on_offer]
        deal = min(offers, key=lambda p: p.single_price(True), default=None)
        if deal is not None:
            reason = f"on offer at {chain_name(deal.chain)}: {deal.name} £{deal.single_price(True):.2f}"
        elif other.id not in bought:
            reason = "you haven't tried this yet"
        else:
            reason = "you've bought this before"
        rank = (deal is None, other.id in bought, -len(shared), other.name)
        candidates.append((rank, Suggestion(other, reason, deal)))
    candidates.sort(key=lambda c: c[0])
    return [s for _, s in candidates[:limit]]
