import json
import urllib.request
import threading
from http.server import ThreadingHTTPServer

from shopping_assistant.bank import BankProduct, ShelfGuesser, crawl_aldi
from shopping_assistant.cli import main
from shopping_assistant.db import Database
from shopping_assistant.web.server import PlanJob, make_handler

BASE = "https://www.aldi.co.uk/products"


def tile(name, price, pid):
    return {"href": f"https://www.aldi.co.uk/product/{name.lower().replace(' ', '-')}-{pid}",
            "name": f"{name}\n\n£{price:.2f}", "text": f"{name}\n\n£{price:.2f}"}


class FakeAldi:
    """Aldi's menu and two shelves, the second spread over two pages."""

    def __init__(self):
        self.url = ""
        self.page = self
        jams = [tile(f"Grandessa Jam {i} 340 g", 1.0 + i / 100, 100000 + i) for i in range(30)]
        self.pages = {
            f"{BASE}/food-cupboard/jams-spreads-syrups/k/1": jams,
            f"{BASE}/food-cupboard/jams-spreads-syrups/k/1?page=2": [tile("Grandessa Almond Butter 200 g", 2.49, 200001)],
            f"{BASE}/bakery/breakfast-pastries/k/2": [tile("Village Bakery All Butter Almond Croissants 4 Each", 1.59, 300001)],
            f"{BASE}/home-essentials/flowers/k/3": [tile("Tulips", 3.0, 400001)],
        }

    def open(self, url, *a):
        self.url = url

    def evaluate(self, script, *a):  # the menu's links
        return [[f"{BASE}/food-cupboard/jams-spreads-syrups/k/1", "Jams, Spreads & Syrups"],
                [f"{BASE}/bakery/breakfast-pastries/k/2", "Breakfast Pastries"],
                [f"{BASE}/home-essentials/flowers/k/3", "Flowers"],  # not groceries
                [f"{BASE}/specialbuys/diy/k/4", "DIY"]]  # not crawled

    def tiles(self, site):
        return self.pages.get(self.url, [])


def test_crawl_reads_every_grocery_shelf_and_page():
    products = crawl_aldi(FakeAldi(), on_progress=lambda s: None)
    by_name = {p.name: p for p in products}
    assert len(products) == 32
    almond = by_name["Grandessa Almond Butter 200 g"]
    assert (almond.aisle, almond.shelf, almond.price) == ("Food cupboard", "Jams, Spreads & Syrups", 2.49)
    assert by_name["Village Bakery All Butter Almond Croissants 4 Each"].aisle == "Bakery"
    assert "Tulips" not in by_name


BANK = [
    BankProduct("aldi", "1", "Grandessa Smooth Peanut Butter 340 g", 0.99, "Food cupboard", "Jams, Spreads & Syrups"),
    BankProduct("aldi", "2", "Grandessa Almond Butter 200 g", 2.49, "Food cupboard", "Jams, Spreads & Syrups"),
    BankProduct("aldi", "3", "Grandessa Strawberry Jam 454 g", 0.89, "Food cupboard", "Jams, Spreads & Syrups"),
    BankProduct("aldi", "4", "Village Bakery All Butter Croissants 4 Each", 1.29, "Bakery", "Breakfast Pastries"),
    BankProduct("aldi", "5", "Village Bakery Pain Au Chocolat 4 Each", 1.29, "Bakery", "Breakfast Pastries"),
    BankProduct("aldi", "7", "Village Bakery Almond Croissants 2 Each", 1.49, "Bakery", "Breakfast Pastries"),
    BankProduct("aldi", "6", "Cowbelle Salted Butter 250 g", 1.85, "Dairy, eggs & chilled", "Dairy"),
]


def test_shelf_guesser_tells_the_spread_from_the_croissant():
    g = ShelfGuesser(BANK)
    assert g.guess("Pip & Nut Smooth Almond Butter 225g") == ("Food cupboard", "Jams, Spreads & Syrups")
    assert g.guess("Sainsbury's All Butter Almond Croissants x4") == ("Bakery", "Breakfast Pastries")
    assert g.guess("Quantum Flux Capacitor") is None


def test_bank_in_the_app(tmp_path):
    db_path = str(tmp_path / "bank.db")
    main(["--db", db_path, "init"])
    db = Database(db_path)
    db.replace_bank("aldi", BANK)
    assert db.bank_shelves()[0] == ("Food cupboard", "aldi", "Jams, Spreads & Syrups", 3)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path, "", PlanJob(db_path), None))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    try:
        found = call("/api/bank/search?q=almond%20butter")["products"]
        assert [p["id"] for p in found] == ["2"]
        cupboard = next(a for a in call("/api/catalogue")["aisles"] if a["name"] == "Food cupboard")
        assert {"chain": "aldi", "shelf": "Jams, Spreads & Syrups", "count": 3} in cupboard["bank"]
        shelf = call("/api/bank/shelf?chain=aldi&shelf=Breakfast%20Pastries")["products"]
        assert [p["id"] for p in shelf] == ["4", "5", "7"]
        state = call("/api/bank/add", {"chain": "aldi", "id": "2"})
        [entry] = state["list"]
        item = db.items()[entry["item_id"]]
        assert (item.name, item.category, item.group, item.size, item.unit) == (
            "Grandessa Almond Butter 200 g", "Food cupboard", "Jams, Spreads & Syrups", 200, "g")
        assert [(p.chain, p.price) for p in db.products(item.id)] == [("aldi", 2.49)]
    finally:
        httpd.shutdown()
