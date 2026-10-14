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
