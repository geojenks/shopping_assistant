"""Where you live, where the shops are, and how far a round of stops is.

Postcodes are looked up with postcodes.io and shops come from OpenStreetMap
(via the Overpass API); both are free and need no key.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from itertools import permutations
from typing import Iterable, Sequence

from .models import DeliveryTerms, Store, chain_name

ROAD_FACTOR = 1.3  # roads wind: driving is roughly 1.3x the straight-line distance in a city
KM_PER_MILE = 1.609
OVERPASS = (  # the main server, then public mirrors
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
USER_AGENT = "shopping-assistant/0.1 (household price comparison)"

# How OpenStreetMap names each chain we have prices for, and formats to leave out:
# convenience shops carry a fraction of the range at higher prices than online.
CHAINS = {
    "tesco": (("tesco",), ("express",)),
    "sainsburys": (("sainsbury",), ("local",)),
    "aldi": (("aldi",), ()),
    "waitrose": (("waitrose",), ("little waitrose",)),
    "ms": (("m&s", "marks & spencer", "marks and spencer"), ("m&s food to go",)),
    "coop": (("co-op", "co-operative"), ()),
}

# Rough delivery terms (October 2026) so delivery can be compared at all. Fees vary by slot:
# edit them with your real ones in stores.json or `shop stores delivery`.
DELIVERY = {
    "tesco": DeliveryTerms(fee=4.50, min_basket=50, under_min_charge=4.00, note="estimate: fee varies by slot"),
    "sainsburys": DeliveryTerms(fee=5.00, min_basket=40, under_min_charge=7.00, note="estimate: fee varies by slot"),
    "waitrose": DeliveryTerms(fee=5.00, min_basket=60, note="estimate: fee varies by slot"),
    "ms": DeliveryTerms(fee=4.00, min_basket=40, note="via Ocado; estimate: fee varies by slot"),
    "coop": DeliveryTerms(fee=1.99, min_basket=15, note="'delivery from £1.99, £15 minimum' on shop.coop.co.uk"),
}


def _get_json(url: str, data: bytes | None = None, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def postcode_location(postcode: str) -> tuple[float, float]:
    result = _get_json("https://api.postcodes.io/postcodes/" + urllib.parse.quote(postcode))["result"]
    return result["latitude"], result["longitude"]


def km_between(a: Sequence[float], b: Sequence[float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def drive_miles(a: Sequence[float], b: Sequence[float]) -> float:
    return km_between(a, b) * ROAD_FACTOR / KM_PER_MILE


def chain_of(tags: dict) -> str | None:
    text = " ".join(tags.get(k, "") for k in ("brand", "name", "operator")).casefold()
    for chain, (names, skip) in CHAINS.items():
        if any(n in text for n in names) and not any(s in text for s in skip):
            return chain
    return None


def osm_supermarkets(lat: float, lon: float, radius_km: float) -> list[dict]:
    around = f"(around:{int(radius_km * 1000)},{lat},{lon})"
    # Co-ops are often mapped as convenience shops; only branded ones matter here.
    query = (f'[out:json][timeout:50];(nwr["shop"="supermarket"]{around};'
             f'nwr["shop"="convenience"]["brand"]{around};);out center tags;')
    data = urllib.parse.urlencode({"data": query}).encode()
    last_error: Exception | None = None
    for url in OVERPASS:
        try:
            return _get_json(url, data, timeout=60)["elements"]
        except Exception as e:  # mirrors come and go; try the next
            last_error = e
    raise LookupError(f"the OpenStreetMap servers didn't answer ({last_error}); try again in a few minutes")


def nearby_stores(home: tuple[float, float], elements: Iterable[dict], per_chain: int = 3,
                  max_miles: float = 4.0, loyalty: Iterable[str] = ()) -> list[Store]:
    """The nearest few branches of each chain, plus one delivery option per chain."""
    loyalty = set(loyalty)
    found: dict[str, list[tuple[float, Store]]] = {}
    for e in elements:
        tags = e.get("tags", {})
        chain = chain_of(tags)
        where = e.get("center") or e
        if chain is None or "lat" not in where:
            continue
        miles = drive_miles(home, (where["lat"], where["lon"]))
        if miles > max_miles:
            continue
        place = tags.get("addr:street") or tags.get("addr:suburb") or tags.get("addr:postcode") or ""
        label = tags.get("name") or tags.get("brand") or chain
        store = Store(id=f"osm-{e['type'][0]}{e['id']}", chain=chain, name=f"{label} {place}".strip(),
                      distance_miles=round(miles, 1), loyalty_member=chain in loyalty,
                      lat=where["lat"], lon=where["lon"])
        found.setdefault(chain, []).append((miles, store))
    stores = []
    for chain, branches in sorted(found.items()):
        kept: list[Store] = []
        for _, branch in sorted(branches, key=lambda b: b[0]):
            # OpenStreetMap often maps a shop twice (building and site): treat close ones as one.
            if all(km_between((branch.lat, branch.lon), (k.lat, k.lon)) > 0.15 for k in kept):
                kept.append(branch)
        stores += kept[:per_chain]
    for chain, terms in DELIVERY.items():
        stores.append(Store(id=f"delivery-{chain}", chain=chain, name=f"{chain_name(chain)} delivery",
                            pickup=False, delivery=terms, loyalty_member=chain in loyalty))
    return stores


def best_route(home: Sequence[float] | None, stops: Sequence[Store]) -> tuple[list[Store], float]:
    """The order to visit `stops` from home and back that drives least, and its miles.

    Without coordinates, falls back to separate round trips by each store's distance.
    """
    if not stops:
        return [], 0.0
    if home is None or any(s.lat is None for s in stops):
        return list(stops), sum(2 * (s.distance_miles or 0) for s in stops)
    best: tuple[float, tuple[Store, ...]] | None = None
    for order in permutations(stops):
        points = [home, *((s.lat, s.lon) for s in order), home]
        miles = sum(drive_miles(a, b) for a, b in zip(points, points[1:]))
        if best is None or miles < best[0]:
            best = (miles, order)
    return list(best[1]), best[0]
