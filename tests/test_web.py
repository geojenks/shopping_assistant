import json
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer

import pytest

from shopping_assistant.cli import main
from shopping_assistant.web.server import PlanJob, make_handler


@pytest.fixture()
def app(tmp_path):
    db_path = str(tmp_path / "shop.db")
    main(["--db", db_path, "init", "--sample"])
    job = PlanJob(db_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path, "secret", job, None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with opener.open(request) as response:
            return json.load(response) if "json" in response.headers["Content-Type"] else response.read()

    call.base, call.opener = base, opener
    yield call
    httpd.shutdown()


def test_needs_the_key_once(app):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(app.base + "/api/state")
    assert e.value.code == 401
    assert b"Add something" in app("/?key=secret")  # redirected home with a cookie
    assert app("/api/state")["list"] == []
    assert app("/manifest.webmanifest")  # available before signing in, for Add to Home Screen


def test_list_round_trip(app):
    app("/?key=secret")
    state = app("/api/add", {"text": "kale", "who": "Sam"})
    assert [(e["item_id"], e["who"], e["status"]) for e in state["list"]] == [("kale", "Sam", "low")]
    state = app("/api/add", {"text": "oat milk", "status": "out"})  # not in the catalogue yet
    assert {"Oat milk": "out"}.items() <= {e["name"]: e["status"] for e in state["list"]}.items()
    state = app("/api/update", {"item_id": "kale", "qty": 3})
    assert next(e for e in state["list"] if e["item_id"] == "kale")["qty"] == 3
    state = app("/api/bought", {"item_id": "kale"})
    assert "kale" not in {e["item_id"] for e in state["list"]}
    [recent] = state["recent"]
    state = app("/api/undo", {"purchase_id": recent["purchase_id"]})
    assert next(e for e in state["list"] if e["item_id"] == "kale")["qty"] == 3
    assert state["recent"] == []


def test_compare_with_saved_prices(app):
    app("/?key=secret")
    app("/api/add", {"text": "kale"})
    app("/api/add", {"text": "milk"})
    assert app("/api/plan", {"refresh": False})["started"]
    for _ in range(50):
        job = app("/api/plan")
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done", job
    result = job["result"]
    assert result["options"][0]["title"] == "Best overall"
    assert {r["item_id"] for r in result["rows"]} == {"kale", "milk-semi"}


def test_preferences_from_the_phone(app, tmp_path):
    from shopping_assistant.db import Database
    app("/?key=secret")
    app("/api/add", {"text": "kale"})
    app("/api/pref", {"action": "not-from-chain", "item_id": "kale", "chain": "aldi"})
    app("/api/pref", {"action": "no-own-label", "item_id": "kale", "chain": "tesco"})
    app("/api/pref", {"action": "never-product", "item_id": "kale", "key": "lidl:li011"})
    db = Database(tmp_path / "shop.db")
    prefs = db.preferences()
    assert ("kale", "aldi") in prefs.blocked
    assert prefs.own_label_ok[("kale", "tesco")] is False
    assert "lidl:li011" in prefs.blacklisted_products
    app("/api/pref", {"action": "reset", "item_id": "kale"})
    prefs = db.preferences()
    assert not prefs.blocked and not prefs.own_label_ok


def test_browse_the_shop_and_add_from_a_shelf(tmp_path):
    from shopping_assistant.db import Database
    db_path = str(tmp_path / "fresh.db")
    main(["--db", db_path, "init"])  # the starter catalogue, nothing else
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path, "", PlanJob(db_path), None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/catalogue") as r:
            aisles = json.load(r)["aisles"]
        names = [a["name"] for a in aisles]
        assert names[0] == "Fruit & veg" and "Beer, wine & spirits" in names
        snacks = next(a for a in aisles if a["name"] == "Snacks & sweets")
        crisps = next(s for s in snacks["shelves"] if s["name"] == "Crisps")
        assert any(i["id"] == "crisps-prawn-cocktail" for i in crisps["items"])
        request = urllib.request.Request(base + "/api/add", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"item_id": "crisps-prawn-cocktail", "status": "want"}).encode())
        with urllib.request.urlopen(request) as r:
            [entry] = json.load(r)["list"]
        assert (entry["item_id"], entry["status"]) == ("crisps-prawn-cocktail", "want")
    finally:
        httpd.shutdown()
    assert Database(db_path).purchases() == []  # no made-up history


def test_find_something_not_in_the_catalogue(tmp_path):
    from contextlib import contextmanager
    from shopping_assistant.db import Database
    from shopping_assistant.prices.tiles import Listing
    from shopping_assistant.web.server import FindJob

    db_path = str(tmp_path / "find.db")
    main(["--db", db_path, "init"])

    class Shops:
        def search(self, site, query):
            if site.chain == "aldi":
                raise TimeoutError("slow")
            return [Listing(site.chain, "1", f"{site.chain.title()} Tahini 300g", price=3.0),
                    Listing(site.chain, "2", "Tahini & Lime Dressing 250ml", price=4.0)]

    @contextmanager
    def fetcher(db):
        yield Shops()

    finder = FindJob(db_path)
    assert finder.start("tahini", fetcher)
    for _ in range(100):
        if finder.state["state"] != "running":
            break
        time.sleep(0.05)
    results = {r["chain"]: r for r in finder.state["results"]}
    assert results["aldi"]["error"] and results["tesco"]["products"][0]["name"] == "Tesco Tahini 300g"

    db = Database(db_path)
    item = finder.add(db, "generic", "Food cupboard", who="Sam")
    assert (item.name, item.category, item.group) == ("Tahini", "Food cupboard", "Added by you")
    assert {p.chain for p in db.products(item.id)} >= {"tesco", "sainsburys"}
    one = finder.add(db, "product", "Food cupboard", "tesco", "1")
    assert (one.name, one.size, one.unit) == ("Tesco Tahini 300g", 300, "g")
    assert [p.name for p in db.products(one.id) if p.chain == "tesco"] == ["Tesco Tahini 300g"]
    assert {e.item_id for e in db.shopping_list()} == {item.id, one.id}
