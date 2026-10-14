"""`shop serve`: the shared shopping list as a web app for your phones.

Runs on the computer that fetches prices (supermarkets only serve prices to a
home connection), using the same database as the command line. Phones open it
in the browser and "Add to Home Screen" to use it like an app.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import traceback
from datetime import date
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import history, report
from ..db import Database
from ..optimizer import plan_options, shortlist

STATIC = Path(__file__).parent / "static"
COOKIE = "shop_key"


class PlanJob:
    """One "find the best shop" run at a time: optionally refresh prices, then plan."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.state = {"state": "idle"}

    def start(self, refresh: bool, fetcher_factory) -> bool:
        with self.lock:
            if self.state.get("state") == "running":
                return False
            self.state = {"state": "running", "progress": "Starting…"}
        threading.Thread(target=self._run, args=(refresh, fetcher_factory), daemon=True).start()
        return True

    def _progress(self, text: str) -> None:
        with self.lock:
            self.state = {**self.state, "progress": text}

    def _run(self, refresh: bool, fetcher_factory) -> None:
        db = Database(self.db_path)
        try:
            entries = db.shopping_list()
            if not entries:
                raise ValueError("The list is empty.")
            items = db.items()
            failed: set[str] = set()
            if refresh:
                failed = self._refresh_prices(db, [items[e.item_id] for e in entries if e.item_id in items],
                                              fetcher_factory)
            self._progress("Comparing shops…")
            settings = db.settings()
            plans = plan_options(entries, db.items(), db.products(), db.stores(), db.preferences(settings), settings)
            result = report.comparison(shortlist(plans, limit=5))
            result["checked"] = db.prices_updated()
            result["failed"] = sorted(failed)
            with self.lock:
                self.state = {"state": "done", "result": result, "finished": date.today().isoformat()}
        except Exception as e:
            traceback.print_exc()
            with self.lock:
                self.state = {"state": "error", "error": str(e).splitlines()[0][:300]}
        finally:
            db.close()

    def _refresh_prices(self, db: Database, items, fetcher_factory) -> set[str]:
        """Fetch today's prices for `items`; returns the chains that couldn't be checked."""
        from ..prices.collect import collect
        from ..prices.sites import SITES

        sites = [SITES[c] for c in sorted({s.chain for s in db.stores()}) if c in SITES]
        total = len(items) * len(sites)
        done = 0
        failed: set[str] = set()

        def on_result(outcome) -> None:
            nonlocal done
            done += 1
            if outcome.found:
                db.replace_products(outcome.chain, outcome.item_id, outcome.products)
            elif outcome.error:
                failed.add(outcome.chain)
            self._progress(f"Checking prices: {done} of {total} searches")

        with fetcher_factory(db) as fetcher:
            collect(items, sites, fetcher, on_result=on_result)
        return failed


def make_handler(db_path: str, key: str, job: PlanJob, fetcher_factory):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ShoppingAssistant/0.1"

        def log_message(self, fmt, *args):  # quieter: one line per API call
            if "/api/state" not in (args[0] if args else ""):
                super().log_message(fmt, *args)

        # --- plumbing -----------------------------------------------------

        def _authorised(self) -> bool:
            if not key:
                return True
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return COOKIE in jar and secrets.compare_digest(jar[COOKIE].value, key)

        def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        # --- routes -------------------------------------------------------

        def do_GET(self):
            url = urlparse(self.path)
            given = parse_qs(url.query).get("key", [""])[0]
            if key and given and secrets.compare_digest(given, key):
                # The link you were sent: remember the key for a year, then drop it from the address bar.
                self._send(303, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={key}; Max-Age=31536000; Path=/; SameSite=Lax"})
                return
            if url.path in ("/manifest.webmanifest", "/sw.js") or url.path.startswith("/icon"):
                return self._static(url.path.lstrip("/"))  # needed before login for "Add to Home Screen"
            if not self._authorised():
                return self._send(401, (STATIC / "locked.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/":
                return self._static("index.html")
            if url.path == "/api/state":
                return self._json(self._state())
            if url.path == "/api/plan":
                with job.lock:
                    return self._json(job.state)
            return self._static(url.path.lstrip("/"))

        def do_POST(self):
            if not self._authorised():
                return self._json({"error": "not signed in"}, 401)
            path = urlparse(self.path).path
            body = self._body()
            if path == "/api/plan":
                started = job.start(bool(body.get("refresh", True)), fetcher_factory)
                return self._json({"started": started})
            db = Database(db_path)
            try:
                if path == "/api/add":
                    item = db.item_from_text(str(body["text"]))
                    db.add_to_list(item.id, float(body.get("qty") or 1), body.get("status") or "low",
                                   added_by=str(body.get("who") or ""))
                elif path == "/api/update":
                    db.update_list_entry(body["item_id"], body.get("qty"), body.get("status"))
                elif path == "/api/bought":
                    db.mark_bought(body["item_id"], str(body.get("who") or ""))
                elif path == "/api/remove":
                    db.remove_from_list(body["item_id"])
                elif path == "/api/undo":
                    db.undo_bought(int(body["purchase_id"]))
                elif path == "/api/pref":
                    action, item_id = body["action"], body["item_id"]
                    if action == "never-product":
                        db.blacklist(body["key"])
                    elif action == "not-from-chain":
                        db.block(item_id, body["chain"])
                    elif action == "no-own-label":
                        db.set_own_label(item_id, body["chain"], False)
                    elif action == "reset":
                        db.clear_preferences(item_id)
                    else:
                        raise ValueError(action)
                else:
                    return self._json({"error": "unknown"}, 404)
                return self._json(self._state(db))
            except (KeyError, ValueError) as e:
                return self._json({"error": f"bad request: {e}"}, 400)
            finally:
                db.close()

        def _static(self, name: str) -> None:
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".webmanifest":
                kind = "application/manifest+json"
            if kind.startswith("text/") or kind.endswith(("javascript", "json")):
                kind += "; charset=utf-8"
            self._send(200, path.read_bytes(), kind)

        def _state(self, db: Database | None = None) -> dict:
            own = db is None
            db = db or Database(db_path)
            try:
                items = db.items()
                entries = db.shopping_list()
                listed = {e.item_id for e in entries}
                due = [h for h in history.due(db.purchases(), date.today()) if h.item_id not in listed][:8]
                return {
                    "list": [{"item_id": e.item_id, "name": items[e.item_id].name if e.item_id in items else e.item_id,
                              "category": items[e.item_id].category if e.item_id in items else "",
                              "qty": e.qty, "status": e.status, "who": e.added_by} for e in entries],
                    "due": [{"item_id": h.item_id, "name": items[h.item_id].name, "qty": h.usual_qty}
                            for h in due if h.item_id in items],
                    "recent": [{"purchase_id": pid, "item_id": iid, "qty": qty,
                                "name": items[iid].name if iid in items else iid}
                               for pid, iid, qty in db.recently_bought() if iid not in listed],
                    "catalogue": sorted(i.name for i in items.values()),
                }
            finally:
                if own:
                    db.close()

    return Handler


def serve(db_path: str, host: str, port: int, key: str, fetcher_factory) -> None:
    Database(db_path).close()  # create/migrate before threads start
    job = PlanJob(db_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path, key, job, fetcher_factory))
    httpd.serve_forever()
