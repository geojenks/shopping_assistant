import json
from datetime import date

import pytest

from shopping_assistant.history import due, habits
from shopping_assistant.importers import read_tesco_export
from shopping_assistant.matching import match_item
from shopping_assistant.models import (DeliveryTerms, Item, ListEntry, Product, Purchase, Settings, Store,
                                       Tier, TravelCosts)
from shopping_assistant.optimizer import plan_options, shortlist
from shopping_assistant.preferences import Preferences


def product(chain, item_id, price, tier=Tier.OWN, brand="", **kw):
    return Product(id=f"{item_id}-{brand or tier.value}", chain=chain, item_id=item_id,
                   name=f"{chain} {brand} {item_id}", price=price, brand=brand, tier=tier, **kw)


def settings(per_stop=0.0, per_mile=0.0, max_channels=3):
    return Settings(travel=TravelCosts(per_mile=per_mile, per_stop=per_stop, max_distance_miles=None),
                    max_channels=max_channels)


ITEMS = {i: Item(i, i) for i in ("milk", "beans", "kale", "tea")}


def entries(*ids):
    return [ListEntry(i) for i in ids]


# --- products -----------------------------------------------------------

def test_cost_uses_cheapest_of_promo_and_loyalty_price():
    p = product("tesco", "tea", 3.25, promo_price=3.00, loyalty_price=2.75)
    assert p.cost(1, loyalty_member=True) == 2.75
    assert p.cost(1, loyalty_member=False) == 3.00


def test_multibuy_applies_to_full_groups_only():
    p = product("tesco", "beans", 0.55, multibuy_qty=4, multibuy_price=2.00)
    assert p.cost(4, False) == 2.00
    assert p.cost(5, False) == 2.55
    assert p.cost(2, False) == 1.10


def test_packs_cover_the_wanted_amount_with_some_slack():
    item = Item("chicken", "Chicken", size=650, unit="g")
    assert product("x", "chicken", 1, size=640, unit="g").packs_for(item, 1) == 1
    assert product("x", "chicken", 1, size=300, unit="g").packs_for(item, 1) == 2
    assert product("x", "chicken", 1, size=650, unit="g").packs_for(item, 2) == 2
    assert product("x", "chicken", 1).packs_for(item, 3) == 3  # unknown size: one pack per unit


def test_delivery_charges():
    terms = DeliveryTerms(fee=4.5, min_basket=50, under_min_charge=4, free_over=100)
    assert terms.charges(60) == 4.5
    assert terms.charges(30) == 8.5
    assert terms.charges(120) == 0
    assert DeliveryTerms(fee=3, min_basket=40).charges(30) is None


# --- preferences ----------------------------------------------------------

def test_brand_preference_excludes_own_label_unless_allowed_at_that_chain():
    prefs = Preferences(brands={"beans": {"heinz"}})
    heinz = product("tesco", "beans", 1.4, Tier.BRANDED, "Heinz")
    branston = product("tesco", "beans", 1.0, Tier.BRANDED, "Branston")
    tesco_own = product("tesco", "beans", 0.45)
    aldi_own = product("aldi", "beans", 0.39)
    assert prefs.allows(heinz) and not prefs.allows(branston) and not prefs.allows(tesco_own)
    prefs.own_label_ok[("beans", "aldi")] = True
    assert prefs.allows(aldi_own) and not prefs.allows(tesco_own)


def test_own_label_can_be_ruled_out_for_one_chain_or_all():
    prefs = Preferences(own_label_ok={("tea", "aldi"): False})
    assert not prefs.allows(product("aldi", "tea", 1.09))
    assert prefs.allows(product("tesco", "tea", 1.35))
    prefs.own_label_ok[("tea", "*")] = False
    assert not prefs.allows(product("tesco", "tea", 1.35))
    assert prefs.allows(product("tesco", "tea", 2.75, Tier.BRANDED, "Yorkshire Tea"))


def test_blocked_and_blacklisted():
    kale = product("aldi", "kale", 0.79)
    assert not Preferences(blocked={("kale", "aldi")}).allows(kale)
    assert not Preferences(blacklisted_products={kale.key}).allows(kale)


# --- optimiser ------------------------------------------------------------

TESCO = Store("tesco", "tesco", "Tesco", 1.0, delivery=DeliveryTerms(fee=4.5, min_basket=10, under_min_charge=4))
ALDI = Store("aldi", "aldi", "Aldi", 1.0)
PRODUCTS = [
    product("tesco", "milk", 1.65), product("aldi", "milk", 1.45),
    product("tesco", "beans", 1.40, Tier.BRANDED, "Heinz"), product("aldi", "beans", 0.39),
    product("tesco", "kale", 1.00), product("aldi", "kale", 0.79),
    product("tesco", "tea", 2.75, Tier.BRANDED, "Yorkshire Tea"),
]


def test_cheapest_split_when_stops_are_free():
    plans = plan_options(entries("milk", "beans", "kale", "tea"), ITEMS, PRODUCTS, [TESCO, ALDI],
                         Preferences(), settings(per_stop=0))
    best = plans[0]
    assert {c.label for c in best.channels} == {"Tesco (pickup)", "Aldi (pickup)"}
    assert best.goods == round(1.45 + 0.39 + 0.79 + 2.75, 2)


def test_extra_stop_not_worth_a_small_saving():
    plans = plan_options(entries("milk", "kale", "tea"), ITEMS, PRODUCTS, [TESCO, ALDI],
                         Preferences(), settings(per_stop=2.0))
    assert [c.label for c in plans[0].channels] == ["Tesco (pickup)"]


def test_items_nobody_sells_are_reported_not_fatal():
    items = {**ITEMS, "caviar": Item("caviar", "caviar")}
    plans = plan_options(entries("milk", "caviar"), items, PRODUCTS, [TESCO, ALDI], Preferences(), settings())
    assert plans and plans[0].unavailable == ["caviar"]


def test_under_minimum_delivery_tops_up_when_cheaper_than_the_charge():
    # Heinz beans and tea come from Tesco (£4.15), under its £5 minimum. Moving the
    # milk over from Aldi costs 20p, far less than the £4 under-minimum charge.
    stores = [Store("tesco", "tesco", "Tesco", None, pickup=False,
                    delivery=DeliveryTerms(fee=4.5, min_basket=5, under_min_charge=4)), ALDI]
    plans = plan_options(entries("milk", "beans", "kale", "tea"), ITEMS, PRODUCTS, stores,
                         Preferences(brands={"beans": {"heinz"}}), settings())
    split = next(p for p in plans if len(p.channels) == 2)
    tesco = next(c for c in split.channels if c.store.id == "tesco")
    assert split.line_for("milk").channel == tesco
    assert split.line_for("kale").channel.store.id == "aldi"
    assert split.fees == {tesco.label: 4.5}


def test_hard_minimum_rules_out_a_small_delivery():
    stores = [Store("tesco", "tesco", "Tesco", None, pickup=False, delivery=DeliveryTerms(fee=4.5, min_basket=40))]
    assert plan_options(entries("milk"), ITEMS, PRODUCTS, stores, Preferences(), settings()) == []


def test_shortlist_drops_plans_that_cost_more_for_more_hassle():
    plans = plan_options(entries("milk", "beans", "kale", "tea"), ITEMS, PRODUCTS, [TESCO, ALDI],
                         Preferences(), settings(per_stop=1.0))
    picked = shortlist(plans, limit=10)
    titles = [t for t, _ in picked]
    assert titles[0] == "Best overall"
    for title, plan in picked:
        if not title.startswith("Everything from") and plan is not picked[0][1]:
            assert not any(q.cash < plan.cash and q.stops <= plan.stops and q.deliveries <= plan.deliveries
                           for q in plans)


# --- history, matching, import ---------------------------------------------

def test_due_items_from_regular_purchases():
    purchases = [Purchase(f"2026-09-{d:02d}", "tesco", "milk", 1, 1.65, item_id="milk") for d in (1, 5, 9, 13)]
    purchases += [Purchase(f"2026-09-{d:02d}", "tesco", "tea", 1, 2.75, item_id="tea") for d in (1, 15)]
    assert habits(purchases)["milk"].every_days == 4
    assert "tea" not in habits(purchases)  # not enough history yet
    assert [h.item_id for h in due(purchases, date(2026, 9, 16))] == ["milk"]
    assert due(purchases, date(2026, 9, 13), horizon_days=0) == []


@pytest.mark.parametrize("name, expected", [
    ("Tesco Semi Skimmed Milk 4 Pints", "milk-semi"),
    ("Walkers Salt & Vinegar 6x25g", "crisps-sv"),
    ("Tesco Houmous 200g", None),
])
def test_match_item(name, expected):
    items = [Item("milk-semi", "Semi-skimmed milk", keywords=("semi skimmed milk",)),
             Item("crisps-sv", "Crisps: salt & vinegar", keywords=("salt & vinegar",))]
    assert match_item(name, items) == expected


def test_tesco_export_reader_finds_nested_transactions(tmp_path):
    export = {"Purchase": [[
        {"timeStamp": "2026-09-01T10:00:00", "transactionID": "T1", "basketValueGross": 3.4,
         "product": [{"name": "Tesco Semi Skimmed Milk 4 Pints", "quantity": 2, "price": 3.30},
                     {"name": "Tesco Kale 200g", "quantity": 1, "price": 1.00}]},
    ]]}
    path = tmp_path / "tesco.json"
    path.write_text(json.dumps(export))
    purchases = read_tesco_export(path)
    assert [(p.product_name, p.qty, p.price, p.date, p.txn_id) for p in purchases] == [
        ("Tesco Semi Skimmed Milk 4 Pints", 2, 3.30, "2026-09-01", "T1"),
        ("Tesco Kale 200g", 1, 1.00, "2026-09-01", "T1"),
    ]
