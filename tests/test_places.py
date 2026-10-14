import pytest

from shopping_assistant.models import Item, ListEntry, Product, Settings, Store, TravelCosts
from shopping_assistant.optimizer import plan_options
from shopping_assistant.places import best_route, chain_of, drive_miles, nearby_stores
from shopping_assistant.preferences import Preferences

HOME = (51.4686, -2.5602)  # BS5 6JS


@pytest.mark.parametrize("tags, chain", [
    ({"brand": "Tesco Extra", "name": "Tesco Extra"}, "tesco"),
    ({"brand": "Tesco Express", "name": "Tesco Express"}, None),  # small range, higher prices
    ({"name": "Little Waitrose & Partners"}, None),
    ({"brand": "M&S Foodhall"}, "ms"),
    ({"name": "The Co-operative Food"}, "coop"),
    ({"name": "Scoopaway"}, None),  # contains "coop" but isn't one
    ({"brand": "Lidl"}, None),  # no price source yet
])
def test_chain_of(tags, chain):
    assert chain_of(tags) == chain


def element(n, lat, lon, **tags):
    return {"type": "node", "id": n, "lat": lat, "lon": lon, "tags": tags}


def test_nearby_stores_keeps_nearest_branches_and_adds_delivery():
    elements = [
        element(1, 51.4720, -2.5630, brand="Tesco Extra", name="Tesco Extra", **{"addr:street": "Eastgate Road"}),
        element(2, 51.4400, -2.6000, brand="Tesco", name="Tesco", **{"addr:street": "Far Road"}),
        element(3, 51.4610, -2.5550, brand="Aldi", name="Aldi", **{"addr:street": "Church Road"}),
        element(4, 51.6000, -2.9000, brand="Aldi", name="Aldi"),  # too far
    ]
    stores = nearby_stores(HOME, elements, per_chain=1, max_miles=3, loyalty=["tesco"])
    pickups = [s for s in stores if s.pickup]
    assert [s.name for s in pickups] == ["Aldi Church Road", "Tesco Extra Eastgate Road"]
    tesco = next(s for s in pickups if s.chain == "tesco")
    assert tesco.loyalty_member and tesco.lat == 51.4720 and tesco.distance_miles < 0.5
    deliveries = {s.chain for s in stores if s.delivery}
    assert {"tesco", "sainsburys", "waitrose", "ms", "coop"} <= deliveries
    assert "aldi" not in deliveries


def store(id, chain, lat, lon):
    return Store(id, chain, id, round(drive_miles(HOME, (lat, lon)), 1), lat=lat, lon=lon)


# Two shops next to each other, one the other way.
ALDI = store("Aldi", "aldi", 51.4610, -2.5550)
LIDL = store("Lidl", "lidl", 51.4615, -2.5545)
TESCO = store("Tesco", "tesco", 51.4760, -2.5660)


def test_shops_next_to_each_other_cost_little_extra_driving():
    _, both = best_route(HOME, [ALDI, LIDL])
    _, aldi = best_route(HOME, [ALDI])
    assert both - aldi < 0.1
    _, apart = best_route(HOME, [ALDI, TESCO])
    assert apart > aldi + 0.5


def test_route_order_is_shortest():
    route, _ = best_route(HOME, [TESCO, LIDL, ALDI])
    assert abs(route.index(ALDI) - route.index(LIDL)) == 1  # never Aldi, Tesco, Lidl


def test_without_coordinates_falls_back_to_round_trips():
    plain = Store("x", "aldi", "x", 1.5)
    assert best_route(HOME, [plain]) == ([plain], 3.0)


def test_planner_uses_one_branch_per_chain_and_prefers_the_nearer():
    near = store("Aldi near", "aldi", 51.4610, -2.5550)
    far = store("Aldi far", "aldi", 51.4400, -2.6000)
    items = {"milk": Item("milk", "milk")}
    products = [Product("1", "aldi", "milk", "Milk", 1.0)]
    settings = Settings(travel=TravelCosts(per_mile=0.3, per_stop=0, max_distance_miles=None), home=HOME)
    plans = plan_options([ListEntry("milk")], items, products, [near, far], Preferences(), settings)
    assert [p.route for p in plans] == [[near], [far]]  # never both
    assert plans[0].miles < plans[1].miles


def test_a_shop_mapped_twice_is_one_branch():
    elements = [element(1, 51.4720, -2.5630, brand="Tesco Extra"), element(2, 51.4722, -2.5628, brand="Tesco Extra")]
    assert len([s for s in nearby_stores(HOME, elements) if s.pickup]) == 1
