"""Fetch search results with a real browser (Playwright), meant to run on your own computer.

Supermarket sites block scripted requests from servers, so we drive Chromium
with a persistent profile: cookies (and any "are you human" check you pass by
hand with --show-browser) carry over between runs.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

from .sites import Site
from .tiles import Listing, parse_tile

# For each link to a product page, climb to the smallest element that holds the
# whole tile: it contains a price but no link to a different product.
EXTRACT_TILES = r"""
(pattern) => {
  const re = new RegExp(pattern);
  const money = /£\s?\d|\d{1,3}p\b/;
  const key = a => a.href.split('#')[0].split('?')[0];
  const links = [...document.querySelectorAll('a[href]')].filter(a => re.test(a.href));
  const seen = new Map();
  for (const a of links) {
    const href = key(a);
    let node = a, tile = null;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      const others = [...node.querySelectorAll('a[href]')].some(b => re.test(b.href) && key(b) !== href);
      if (others) break;
      if (money.test(node.innerText || '')) tile = node;
    }
    if (!tile) continue;
    const name = (a.getAttribute('aria-label') || a.getAttribute('title') || a.innerText || '').trim()
      || (tile.querySelector('h2, h3, [data-testid*="title"]')?.innerText || '').trim();
    const prev = seen.get(href);
    if (!prev || (!prev.name && name) || prev.text.length < tile.innerText.length)
      seen.set(href, {href, name: name || prev?.name || '', text: tile.innerText});
  }
  return [...seen.values()];
}
"""

COOKIE_BUTTONS = (
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept all cookies')",
    "button:has-text('Accept all')",
    "button:has-text('Accept cookies')",
    "button:has-text('Allow all')",
    "button:has-text('I accept')",
)


def tiles_to_listings(site: Site, tiles: list[dict]) -> list[Listing]:
    listings = []
    for tile in tiles:
        name = (tile.get("name") or "").strip()
        if not name or "\n" in name:
            # No accessible name, or the link wraps the whole tile: the longest line
            # without a price is nearly always the product name.
            lines = [l.strip() for l in (name or tile["text"]).splitlines() if l.strip() and "£" not in l]
            name = max(lines, key=len, default="")
        if not name:
            continue
        listing = parse_tile(site.chain, site.product_id(tile["href"]), name, tile["text"], tile["href"],
                             site.loyalty_words)
        if site.only_prefix and not listing.name.casefold().startswith(site.only_prefix):
            continue
        if listing.price is not None:
            listings.append(listing)
    return listings


class BrowserFetcher:
    def __init__(self, profile_dir: str | Path = "data/private/browser-profile", headless: bool = True,
                 delay: tuple[float, float] = (2.0, 5.0), save_dir: str | Path | None = None):
        from playwright.sync_api import sync_playwright  # optional dependency

        self._pw = sync_playwright().start()
        launch: dict = {"headless": headless, "locale": "en-GB", "viewport": {"width": 1366, "height": 900}}
        if os.environ.get("SHOP_CHROMIUM"):
            launch["executable_path"] = os.environ["SHOP_CHROMIUM"]
        self.context = self._pw.chromium.launch_persistent_context(str(profile_dir), **launch)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.delay = delay
        self.save_dir = Path(save_dir) if save_dir else None
        self._last = 0.0

    def close(self) -> None:
        self.context.close()
        self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _pause(self) -> None:
        wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _accept_cookies(self) -> None:
        for selector in COOKIE_BUTTONS:
            button = self.page.locator(selector).first
            try:
                if button.is_visible(timeout=500):
                    button.click(timeout=2000)
                    return
            except Exception:
                continue

    def open(self, url: str, wait_ms: int = 0) -> None:
        self._pause()
        self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        self._accept_cookies()
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass  # some sites never go idle; the tiles are usually there anyway
        # Lazy-loaded results appear as you scroll.
        for _ in range(4):
            self.page.mouse.wheel(0, 2500)
            self.page.wait_for_timeout(400)
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

    def tiles(self, site: Site) -> list[dict]:
        return self.page.evaluate(EXTRACT_TILES, site.product_link)

    def search(self, site: Site, query: str) -> list[Listing]:
        self.open(site.url_for(query), site.extra_wait_ms)
        tiles = self.tiles(site)
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{site.chain}-{query}".replace(" ", "_").replace("/", "_")
            (self.save_dir / f"{stem}.html").write_text(self.page.content(), encoding="utf-8")
            self.page.screenshot(path=str(self.save_dir / f"{stem}.png"), full_page=False)
        return tiles_to_listings(site, tiles)
