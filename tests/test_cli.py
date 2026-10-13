from shopping_assistant.cli import main
from shopping_assistant.db import Database


def run(db_path, *args):
    main(["--db", str(db_path), *args])


def test_sample_walkthrough(tmp_path, capsys):
    db_path = tmp_path / "shop.db"
    run(db_path, "init", "--sample")
    run(db_path, "list", "add", "kale", "coffee", "--out")
    run(db_path, "list", "add", "baked beans", "--qty", "3")
    run(db_path, "pref", "brand", "baked beans", "heinz")
    run(db_path, "pref", "block", "kale", "aldi")
    html = tmp_path / "plan.html"
    run(db_path, "plan", "--html", str(html))
    out = capsys.readouterr().out
    assert "Best overall" in out
    assert "Aldi: Nature's Pick Kale" not in out  # blocked
    assert "Corale" not in out  # Aldi own-label beans: they want Heinz
    assert "<table>" in html.read_text()


def test_reimporting_history_adds_nothing(tmp_path):
    db_path = tmp_path / "shop.db"
    run(db_path, "init", "--sample")
    db = Database(db_path)
    before = len(db.purchases())
    from shopping_assistant.cli import SAMPLE_DIR
    from shopping_assistant.importers import read_csv_history
    assert db.add_purchases(read_csv_history(SAMPLE_DIR / "history.csv")) == (0, 0)
    assert len(db.purchases()) == before


def test_mapping_a_receipt_name(tmp_path):
    db_path = tmp_path / "shop.db"
    run(db_path, "init", "--sample")
    db = Database(db_path)
    assert ("Tesco Houmous 200g", 10) in db.unmatched_names()
    assert db.map_name("Tesco Houmous 200g", "kale") == 10
    assert all(name != "Tesco Houmous 200g" for name, _ in db.unmatched_names())


def test_list_merges_repeat_adds(tmp_path):
    db = Database(tmp_path / "shop.db")
    db.add_to_list("milk", 1, "low")
    db.add_to_list("milk", 2, "out")
    db.add_to_list("milk", 1, "low")
    [entry] = db.shopping_list()
    assert (entry.qty, entry.status) == (2, "out")
