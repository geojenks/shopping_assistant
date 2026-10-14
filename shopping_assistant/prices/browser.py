"""Fetch search results with a real browser (Playwright), meant to run on your own computer.

Supermarket sites block scripted requests from servers, so we drive Chromium
with a persistent profile: cookies (and any "are you human" check you pass by
hand with --show-browser) carry over between runs.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from string import capwords

from ..units import parse_sizes
from .sites import Site
from .tiles import MONEY, Listing, parse_tile

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
    // The link's own title or visible text names the product; aria-labels often add
    // extras ("Featured, ..."), so they and image alt text are only fallbacks.
    const strong = (a.getAttribute('title') || a.innerText || '').trim();
    const weak = (a.querySelector('img[alt]')?.getAttribute('alt') || a.getAttribute('aria-label') || '').trim();
    const prev = seen.get(href) || {href, name: '', weak: '', text: ''};
    seen.set(href, {
      href,
      name: prev.name || strong,
      weak: prev.weak || weak,
      text: prev.text.length >= tile.innerText.length ? prev.text : tile.innerText,
    });
  }
  return [...seen.values()].map(t => ({href: t.href, name: t.name || t.weak, text: t.text}));
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


SIZE_ONLY = re.compile(r"^\d+(\.\d+)?\s*(x\s*\d+(\.\d+)?\s*)?(kg|g|ml|cl|l|ltr|litres?|pints?|pk|pack|each|s)$", re.I)


# Tile text that is never the product name.
BOILERPLATE = re.compile(r"\bin (trolley|basket)\b|out of 5|^\(\d+\)$|^(view|add|remove|more|see)\b|"
                         r"\bfor$|^(item price|price per unit|new lower price|featured|sponsored|offer)$", re.I)


def name_from_lines(text: str) -> str:
    """A product name from a tile whose link wraps several lines.

    Aldi's read "COWBELLE / Semi Skimmed Milk 1.7% Fat / 2.272 l / (£0.73/1 L) / £1.65",
    which becomes "Cowbelle Semi Skimmed Milk 1.7% Fat 2.272 l".
    """
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not MONEY.search(l) and not BOILERPLATE.search(l.strip())]
    sizes = [l for l in lines if SIZE_ONLY.match(l)]
    rest = [l for l in lines if l not in sizes]
    brand = rest[0] if len(rest) > 1 and rest[0].isupper() else ""
    main = max((l for l in rest if l != brand), key=len, default="")
    size = sizes[0] if sizes and not parse_sizes(main) else ""
    return " ".join(filter(None, [capwords(brand), main, size]))


def tiles_to_listings(site: Site, tiles: list[dict]) -> list[Listing]:
    listings = []
    for tile in tiles:
        name = (tile.get("name") or "").strip()
        if not name or "\n" in name or BOILERPLATE.search(name):
            name = name_from_lines(name if "\n" in name else tile["text"])
        name = name.replace("\u2019", "'")
        if not name:
            continue
        if not parse_sizes(name):
            # Waitrose and Ocado put the pack size on its own line ("6s", "125g").
            size = next((l.strip() for l in tile["text"].splitlines() if SIZE_ONLY.match(l.strip())), "")
            name = f"{name} {size}".strip()
        listing = parse_tile(site.chain, site.product_id(tile["href"]), name, tile["text"], tile["href"],
                             site.loyalty_words)
        if site.only_prefix and not listing.name.casefold().startswith(site.only_prefix):
            continue
        if listing.price is not None:
            listings.append(listing)
    return listings


@dataclass(frozen=True)
class BrowserSetup:
    """Which browser to drive and how. Sites differ in what their bot checks accept."""

    channel: str = ""  # "" = Playwright's own Chromium; "chrome" or "msedge" = the installed browser
    headless: bool = True
    stealth: bool = True  # hide the automation flags and the "HeadlessChrome" user agent

    @property
    def label(self) -> str:
        name = {"": "built-in Chromium", "chrome": "Google Chrome", "msedge": "Microsoft Edge"}[self.channel]
        return f"{name}, {'hidden' if self.headless else 'window'}{'' if self.stealth else ', plain'}"


# What `shop prices doctor` tries, in order.
SETUPS = (
    BrowserSetup(),
    BrowserSetup(stealth=False),
    BrowserSetup(channel="chrome"),
    BrowserSetup(channel="chrome", headless=False),
    BrowserSetup(channel="msedge", headless=False),
)


class SiteNeedsSetup(Exception):
    pass


class BrowserFetcher:
    def __init__(self, profile_dir: str | Path = "data/private/browser-profile", setup: BrowserSetup = BrowserSetup(),
                 delay: tuple[float, float] = (2.0, 5.0), save_dir: str | Path | None = None, postcode: str = ""):
        from playwright.sync_api import sync_playwright  # optional dependency

        self._pw = sync_playwright().start()
        try:
            self._launch(profile_dir, setup)
        except Exception:
            self._pw.stop()  # or the next BrowserFetcher can't start Playwright
            raise
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.delay = delay
        self.save_dir = Path(save_dir) if save_dir else None
        self.postcode = postcode
        self._last = 0.0

    def _launch(self, profile_dir: str | Path, setup: BrowserSetup) -> None:
        launch: dict = {"headless": setup.headless, "locale": "en-GB", "timezone_id": "Europe/London",
                        "viewport": {"width": 1366, "height": 900}}
        if setup.channel:
            launch["channel"] = setup.channel
        elif os.environ.get("SHOP_CHROMIUM"):
            launch["executable_path"] = os.environ["SHOP_CHROMIUM"]
        if setup.stealth:
            # Bot checks (Akamai at Sainsbury's and Waitrose) turn away browsers that say they're automated.
            launch["args"] = ["--disable-blink-features=AutomationControlled"]
            if setup.headless:
                launch["user_agent"] = self._plain_user_agent(launch)
        self.context = self._pw.chromium.launch_persistent_context(str(profile_dir), **launch)
        if setup.stealth:
            self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def _plain_user_agent(self, launch: dict) -> str:
        """This browser's user agent without the "HeadlessChrome" giveaway."""
        browser = self._pw.chromium.launch(
            headless=True, **{k: launch[k] for k in ("channel", "executable_path") if k in launch})
        try:
            agent = browser.new_page().evaluate("navigator.userAgent")
        finally:
            browser.close()
        return agent.replace("HeadlessChrome", "Chrome")

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

    def _settle(self) -> None:
        self._accept_cookies()
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass  # some sites never go idle; the tiles are usually there anyway

    def _enter_postcode(self, site: Site) -> bool:
        """Get past a "which store are you near?" page (Co-op). True if there was one."""
        box = self.page.locator(site.postcode_input).first
        self._accept_cookies()
        try:
            # The box is drawn by the page's scripts after it loads, so give it a moment.
            box.wait_for(state="visible", timeout=8000)
        except Exception:
            return False
        if not self.postcode:
            raise SiteNeedsSetup(f"{site.chain} asks for a postcode first: shop set postcode \"AB1 2CD\"")
        box.fill(self.postcode)
        box.press("Enter")
        self._settle()
        self.snapshot(f"{site.chain}-after-postcode")
        return True

    def snapshot(self, stem: str) -> None:
        if self.save_dir:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            (self.save_dir / f"{stem}.html").write_text(self.page.content(), encoding="utf-8")
            self.page.screenshot(path=str(self.save_dir / f"{stem}.png"))

    def open(self, url: str, wait_ms: int = 0, site: Site | None = None) -> None:
        self._pause()
        self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if site and site.postcode_input and self._enter_postcode(site):
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

    def product_like_links(self, limit: int = 15) -> list[str]:
        """Links that look like product pages, to fix a site's `product_link` when it finds nothing."""
        return self.page.evaluate("""(limit) => [...new Set([...document.querySelectorAll('a[href]')]
            .map(a => a.href.split('?')[0]).filter(h => /product/i.test(h)))].slice(0, limit)""", limit)

    def tiles(self, site: Site) -> list[dict]:
        return self.page.evaluate(EXTRACT_TILES, site.product_link)

    def search(self, site: Site, query: str) -> list[Listing]:
        self.open(site.url_for(query), site.extra_wait_ms, site)
        tiles = self.tiles(site)
        self.snapshot(f"{site.chain}-{query}".replace(" ", "_").replace("/", "_"))
        return tiles_to_listings(site, tiles)
