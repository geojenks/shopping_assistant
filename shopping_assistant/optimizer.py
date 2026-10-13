"""Work out the cheapest ways to buy a shopping list across supermarkets.

Every combination of up to `max_channels` "channels" (a pickup at a store, or a
delivery from a store) is costed. Within a combination each item goes to the
cheapest acceptable product; delivery minimum baskets are then met either by
paying the under-minimum charge or by moving items into that basket, whichever
is cheaper. Plans are ranked by cash spent plus the value you put on each stop.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Literal

from .models import Item, ListEntry, Product, Settings, Store
from .preferences import Preferences

Mode = Literal["pickup", "delivery"]


@dataclass(frozen=True)
class Channel:
    store: Store
    mode: Mode

    @property
    def label(self) -> str:
        return f"{self.store.name} ({self.mode})"


@dataclass(frozen=True)
class Choice:
    product: Product
    packs: int
    cost: float


@dataclass
class Line:
    entry: ListEntry
    item: Item
    channel: Channel
    choice: Choice


@dataclass
class Plan:
    channels: tuple[Channel, ...]
    lines: list[Line]
    fees: dict[str, float] = field(default_factory=dict)  # channel label -> delivery charges
    mileage: float = 0.0
    hassle: float = 0.0
    unavailable: list[str] = field(default_factory=list)

    @property
    def goods(self) -> float:
        return round(sum(line.choice.cost for line in self.lines), 2)

    @property
    def cash(self) -> float:
        """Money you actually spend: shopping, delivery charges and fuel."""
        return round(self.goods + sum(self.fees.values()) + self.mileage, 2)

    @property
    def score(self) -> float:
        return round(self.cash + self.hassle, 2)

    @property
    def label(self) -> str:
        return " + ".join(c.label for c in self.channels)

    @property
    def stops(self) -> int:
        return sum(1 for c in self.channels if c.mode == "pickup")

    @property
    def deliveries(self) -> int:
        return sum(1 for c in self.channels if c.mode == "delivery")

    def goods_for(self, channel: Channel) -> float:
        return round(sum(l.choice.cost for l in self.lines if l.channel == channel), 2)

    def line_for(self, item_id: str) -> Line | None:
        return next((l for l in self.lines if l.entry.item_id == item_id), None)


def available_channels(stores: Iterable[Store], settings: Settings) -> list[Channel]:
    limit = settings.travel.max_distance_miles
    channels = []
    for store in stores:
        if store.pickup and (limit is None or store.distance_miles is None or store.distance_miles <= limit):
            channels.append(Channel(store, "pickup"))
        if store.delivery is not None:
            channels.append(Channel(store, "delivery"))
    return channels


def best_choices(
    entries: list[ListEntry],
    items: dict[str, Item],
    products: Iterable[Product],
    stores: Iterable[Store],
    prefs: Preferences,
) -> dict[str, dict[str, Choice]]:
    """For each list entry and store, the cheapest acceptable product."""
    by_item: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if prefs.allows(p):
            by_item[p.item_id].append(p)

    result: dict[str, dict[str, Choice]] = {}
    for entry in entries:
        item = items[entry.item_id]
        per_store: dict[str, Choice] = {}
        for store in stores:
            for p in by_item.get(entry.item_id, ()):
                if p.chain != store.chain:
                    continue
                packs = p.packs_for(item, entry.qty)
                choice = Choice(p, packs, p.cost(packs, store.loyalty_member))
                current = per_store.get(store.id)
                if current is None or choice.cost < current.cost:
                    per_store[store.id] = choice
        result[entry.item_id] = per_store
    return result


def plan_options(
    entries: list[ListEntry],
    items: dict[str, Item],
    products: Iterable[Product],
    stores: list[Store],
    prefs: Preferences,
    settings: Settings,
) -> list[Plan]:
    """Every sensible plan for the list, cheapest (by score) first."""
    choices = best_choices(entries, items, products, stores, prefs)
    unavailable = [e.item_id for e in entries if not choices[e.item_id]]
    buyable = [e for e in entries if choices[e.item_id]]
    if not buyable:
        return []

    channels = available_channels(stores, settings)
    plans = []
    for k in range(1, settings.max_channels + 1):
        for combo in combinations(channels, k):
            if len({c.store.id for c in combo}) < k:
                continue  # pickup and delivery from the same shop is never worth it
            plan = _evaluate(combo, buyable, items, choices, settings)
            if plan is not None:
                plan.unavailable = unavailable
                plans.append(plan)
    plans.sort(key=lambda p: (len(p.unavailable), p.score, len(p.channels)))
    return plans


def _evaluate(
    combo: tuple[Channel, ...],
    entries: list[ListEntry],
    items: dict[str, Item],
    choices: dict[str, dict[str, Choice]],
    settings: Settings,
) -> Plan | None:
    assignment: dict[str, Channel] = {}
    for entry in entries:
        options = [(choices[entry.item_id][c.store.id].cost, i, c)
                   for i, c in enumerate(combo) if c.store.id in choices[entry.item_id]]
        if not options:
            return None  # this combination can't cover the list
        assignment[entry.item_id] = min(options, key=lambda o: (o[0], o[1]))[2]

    def cost_of(entry: ListEntry, channel: Channel) -> float:
        return choices[entry.item_id][channel.store.id].cost

    def goods(channel: Channel) -> float:
        return sum(cost_of(e, channel) for e in entries if assignment[e.item_id] == channel)

    fees: dict[str, float] = {}
    for channel in combo:
        if channel.mode != "delivery":
            continue
        terms = channel.store.delivery
        charge_as_is = terms.charges(goods(channel))

        # Alternative: move the items that cost least extra into this basket until it hits the minimum.
        moved: list[str] = []
        extra = 0.0
        if goods(channel) < terms.min_basket:
            movable = sorted(
                (cost_of(e, channel) - cost_of(e, assignment[e.item_id]), e.item_id, e)
                for e in entries
                if assignment[e.item_id] != channel and channel.store.id in choices[e.item_id]
            )
            total = goods(channel)
            for delta, key, entry in movable:
                if total >= terms.min_basket:
                    break
                moved.append(key)
                extra += delta
                total += cost_of(entry, channel)
            charge_topped_up = terms.charges(total)
        else:
            charge_topped_up = None

        if charge_topped_up is not None and (charge_as_is is None or extra + charge_topped_up < charge_as_is):
            for key in moved:
                assignment[key] = channel
            fees[channel.label] = charge_topped_up
        elif charge_as_is is not None:
            fees[channel.label] = charge_as_is
        else:
            return None  # hard minimum we can't reach

    used = set(assignment.values())
    if any(c not in used for c in combo):
        return None  # a smaller combination does the same job without the extra stop
    # Topping up one basket can push another delivery under its minimum; re-check.
    for channel in combo:
        if channel.mode == "delivery":
            charge = channel.store.delivery.charges(goods(channel))
            if charge is None:
                return None
            fees[channel.label] = charge

    lines = [Line(e, items[e.item_id], assignment[e.item_id],
                  choices[e.item_id][assignment[e.item_id].store.id]) for e in entries]
    pickups = [c for c in combo if c.mode == "pickup"]
    return Plan(
        channels=combo,
        lines=lines,
        fees=fees,
        mileage=round(sum(settings.travel.mileage(c.store) for c in pickups), 2),
        hassle=round(len(pickups) * settings.travel.per_stop, 2),
    )


def _dominates(a: Plan, b: Plan) -> bool:
    """`a` costs no more than `b` and needs no more stops or deliveries."""
    return a.cash <= b.cash and a.stops <= b.stops and a.deliveries <= b.deliveries


def describe(plan: Plan) -> str:
    parts = []
    if plan.deliveries:
        parts.append("Delivery" if plan.deliveries == 1 else f"{plan.deliveries} deliveries")
    if plan.stops:
        parts.append(f"{plan.stops} stop{'s' * (plan.stops > 1)}")
    return " + ".join(parts)


def shortlist(plans: list[Plan], limit: int = 5) -> list[tuple[str, Plan]]:
    """A handful of labelled plans worth comparing side by side.

    First every plan that isn't beaten on all of price, stops and deliveries by
    another (best score first), then single-shop plans for reference.
    """
    if not plans:
        return []
    worth_it = [p for i, p in enumerate(plans)
                if not any(_dominates(q, p) and (q.cash < p.cash or j < i) for j, q in enumerate(plans) if q is not p)]
    cheapest = min(plans, key=lambda p: (p.cash, p.score))
    picked: list[tuple[str, Plan]] = []
    for plan in worth_it:
        if plan is plans[0]:
            title = "Best overall"
        elif plan is cheapest:
            title = "Lowest spend"
        else:
            title = describe(plan)
        picked.append((title, plan))
    for plan in plans:
        if len(plan.channels) == 1 and all(p is not plan for _, p in picked):
            picked.append((f"Everything from {plan.channels[0].label}", plan))
    return picked[:limit]
