# Shopping assistant

Keeps the household shopping list, learns what you usually buy, swaps in cheaper
equivalents you've said you're happy with, and compares ways of getting it all:

```
A  £  39.68  Tesco Extra (pickup)
   Best overall: goods £38.18 + fuel ~£1.50 · 1 stop
B  £  36.37  Aldi (pickup) + Tesco Extra (pickup)
   Lowest spend: goods £33.79 + fuel ~£2.58 · 2 stops
C  £  45.24  Sainsbury's (delivery)
   Delivery: goods £40.24 + Sainsbury's delivery £5.00 · 1 delivery

Where the options differ:
  Mature cheddar     A,E  Tesco Extra: Tesco Mature Cheddar 400g £3.39
                     B    Aldi: Emporium Mature Cheddar 400g £2.89
                     C,D  Sainsbury's: Sainsbury's Mature Cheddar 400g £3.40
  ...
```

`shop plan --html plan.html` writes the same comparison as a page, with the cells
that differ from option A highlighted.

## Quick start

```sh
pip install -e '.[dev]'
shop init --sample             # sample catalogue, illustrative prices, 3 months of fake history
shop list due --add            # put what you usually buy by now on the list
shop list add kale bananas --out
shop pref brand "baked beans" heinz
shop pref sub-ok "toilet roll" aldi
shop pref block kale aldi
shop plan --html plan.html
```

Without installing: `python -m shopping_assistant ...`. The database is
`shopping.db` in the current folder, or wherever `$SHOP_DB` / `--db` points.

**The sample prices are made up** to exercise the planner. They are roughly the
right shape but not real.

## Starting from blank

`shop init` creates the database with the starter catalogue: about 200 everyday
items in aisles (Fruit & veg, Dairy, Bakery, Food cupboard, Frozen, Snacks,
Drinks, Beer, wine & spirits, Household, Health & beauty), each with a usual
pack size and words that rule out look-alikes. No made-up history or prices.
If you tried the demo, `shop init --fresh` clears it and keeps your shops and
settings. Anything you add that isn't in the catalogue becomes a new item.
The catalogue is `shopping_assistant/catalogue/items.json`.

## On your phones

```sh
shop serve
```

It prints a link like `http://192.168.1.20:8765/?key=…`. Open it once on each
phone (it then remembers you), then **Share → Add to Home Screen** (iPhone) or
**⋮ → Add to Home screen** (Android) and it opens like an app. Both of you can:

- browse the **Shop** tab by aisle (or search it) and add things to the basket,
  each showing the cheapest price found so far
- add things as you run low or run out, with suggestions from the catalogue
  (anything new becomes a new item)
- change quantities, tick things off as bought (with undo), and see who added what
- see what you usually buy around now
- press **Find the best shop**: the computer checks today's prices for what's on
  the list (about 10 seconds per search) and shows the options side by side
- tap any product in the comparison to say "never this one", "not from this
  shop", or "this shop's own brand isn't good enough"; it re-compares at once

The computer running `shop serve` does the price checks (supermarkets only answer
a home connection), so it needs to be on and awake while you use the list.

**Away from home Wi-Fi:** install [Tailscale](https://tailscale.com) (free for
personal use) on the computer and both phones, sign in with the same account, then
on the computer run `tailscale serve --bg 8765`. It gives a private
`https://….ts.net` address that only your devices can open, from anywhere. Use
that address (plus `/?key=…`) on the phones instead.

## Your shops

```sh
shop stores find --postcode "BS5 6JS"     # nearest 3 branches of each chain within 3 miles' drive
shop stores                               # what it found
shop stores delivery tesco 3.50 50 4      # your real delivery terms: fee, minimum, charge if under
shop stores remove osm-w20442350          # a branch you never use
```

Shops come from OpenStreetMap, home from postcodes.io (both free). Tesco Express,
Sainsbury's Local and Little Waitrose are left out: smaller range, higher prices
than online. Lidl is left out because its website has no full price list.
Delivery terms start as estimates, so correct them with `shop stores delivery`.

A plan never uses two branches of one chain, and pickups are driven as one
round trip in the shortest order (straight-line distance × 1.3 for roads), so
Aldi and Lidl next door to each other cost a few hundred metres, not two trips.
Each plan's cost of driving is fuel (`per_mile`, cash) plus what your time is
worth (`per_mile_time`, `per_stop`), which only affects the ranking.

## How it decides

- **Items vs products.** An *item* is the generic thing ("salted butter, 250g").
  A *product* is what a chain sells ("Lurpak Slightly Salted 250g" at Tesco).
  The list is made of items; the planner picks products.
- **Preferences** decide which products are acceptable for an item:
  - `pref brand ITEM BRAND`: you want that brand. Own label is then excluded…
  - `pref sub-ok ITEM CHAIN`: …except where you say a chain's own label is fine
    (`*` for every chain).
  - `pref no-sub ITEM [CHAIN]`: own label isn't good enough (there, or anywhere).
  - `pref block ITEM CHAIN`: never buy this item at that chain.
  - `pref blacklist ITEM CHAIN`: never buy the product currently picked there.
  - Without rules, the cheapest product of any brand wins.
- **Pack sizes.** Quantities are in "usual packs" of the item; a 300g pack for a
  650g item means two packs. Coming up to 10% short is accepted.
- **Prices** use the lowest of shelf, promo, and loyalty price (if you have the
  card, set per store), and handle "4 for £2" multibuys.
- **Getting it.** Each store can be a pickup (driven as one shortest route,
  see above) and/or a delivery (fee, minimum basket,
  under-minimum charge or hard minimum, free-over threshold). Every combination
  of up to `max_channels` is costed. If a delivery basket is under its minimum,
  the planner compares paying the charge with moving items into that basket.
- **Ranking.** "Best overall" is lowest cash + `per_stop` × stops. The shortlist
  then shows every option that isn't beaten on price, stops *and* deliveries by
  another, plus single-shop options for reference.

Tune it with `shop set per_stop 5`, `shop set max_distance_miles 4`,
`shop set max_channels 2`, `shop set default_own_label_ok false`.

## Your data

- `shop load DIR` loads `items.json`, `stores.json` and `prices.csv` (see
  `data/sample/` for the formats). Put your real versions in `data/private/`,
  which git ignores.
- `shop import tesco FILE.json` reads Tesco's data export (below).
  `shop import csv FILE` reads `date,store,product,qty,price` for anything else.
- `shop unmatched` lists receipt names that couldn't be matched to an item;
  `shop map "Tesco Houmous 200g" houmous` fixes one for good.
- `shop list due` uses the history to say what you're probably running low on.
- `shop suggest crisps` suggests related items (same category or theme tag),
  deals first, then things you haven't tried.

## Prices from the supermarkets

`shop prices update` searches each supermarket's website for every item in
your catalogue, using a real Chromium browser. It reads the results (shelf
price, Clubcard/Nectar/member price, "Any 3 for £5", "3 for 2", "Was/Now",
unavailable), keeps the products that really are that item, and stores them with
a price history.

| Chain | Where prices come from | Checked against the live site |
|---|---|---|
| Sainsbury's | sainsburys.co.uk grocery search | Yes, October 2026 |
| Aldi | aldi.co.uk product search (shop prices; Aldi doesn't deliver) | Yes, October 2026 |
| Waitrose | waitrose.com search | Yes, October 2026 |
| M&S | M&S's range on Ocado (M&S sells food online through Ocado; shop prices can differ slightly) | Yes, October 2026 |
| Tesco | tesco.com grocery search | Yes, from a home connection with Chrome (October 2026) |
| Morrisons | groceries.morrisons.com search | Yes, October 2026 |
| Iceland | iceland.co.uk search | Yes, October 2026 |
| Co-op | shop.coop.co.uk search, after entering your postcode | Off by default: its security service turns away automated visits even from home |
| Asda | not set up | Cloudflare's bot check stops it from a server; untried from home |

Sainsbury's, Aldi, Waitrose, Ocado, Morrisons and Iceland work from a server; Tesco
only from a home connection, so running it on your own computer covers them all. Setup:

```sh
pip install -e '.[web]'
playwright install chromium
shop set postcode "BS5 6JS"                               # Co-op prices by local store
shop prices doctor                                        # find a browser setup every shop accepts
shop prices probe tesco semi skimmed milk --show-browser   # look at one search in detail
shop prices update                                        # everything: ~10s per search
shop prices update --list                                 # just what's on the list, before a shop
shop prices show butter                                   # what it found, with product keys
shop pref blacklist tesco:254656543                       # never pick that one again
```

`doctor` tries each way of driving a browser (the built-in Chromium with and
without hiding that it's automated, your installed Chrome, Edge, hidden or in a
window), each with a fresh profile, searches every shop with it, and remembers the
setup that gets through to the most shops. Run it again if a shop starts blocking.

`probe` prints what it read from one search and saves the page and a screenshot
next to the database (`probe/`), so if a supermarket changes its website or shows
a "verify you're human" page, you can see what happened. `--show-browser` lets
you watch or pass a check by hand; the browser profile is kept,
so you usually only do that once.

Each item can have a `search` (what to type into the search box) and `exclude`
words ("unsalted", "spreadable") in `items.json`. Results are kept if they
contain the item's keywords, none of the excluded words, and a pack size within
0.4–2.5× the item's usual size.

To refresh prices weekly, schedule it, e.g. with cron (Sunday 7am):

```
0 7 * * 0  cd ~/shopping_assistant && SHOP_DB=data/private/shopping.db shop prices update >> data/private/prices.log 2>&1
```

Be considerate: it waits 2–5 seconds between pages (`--delay`), and it's for
your household's own shopping. Check each supermarket's terms if you want to do
more than that.

## Where the data comes from, honestly

| Need | Source | Status |
|---|---|---|
| Your history | Tesco: *My account → request your data* on tesco.com gives every Clubcard transaction with items. Sainsbury's (Nectar), Asda, Morrisons: subject access request, or online order history. | Tesco importer written against the export's described shape, not a real file yet: tweak once we see one. |
| Shopping list | Built in (`shop list`). Bring! has only an unofficial API (the `bring-api` Python package Home Assistant uses), which can break without notice. | Own list done. Bring! import is possible later. |
| Prices | No supermarket offers a public price API, so `shop prices` reads their search pages in a browser. | Working for Sainsbury's, Aldi, Waitrose and M&S; Tesco and Co-op need a run from home. |
| Delivery | Tesco, Sainsbury's, Asda, Morrisons, Ocado, Waitrose deliver. Aldi and Lidl don't run their own delivery (Aldi is trialling Deliveroo "Shop & Deliver" at a few stores from October 2026). | Terms entered per store by hand; slot availability isn't checked. |

## Roadmap

1. **Co-op prices.** The postcode step is in place; its results page still needs
   checking from a home connection.
2. **Better suggestions.** Rate products after trying them; feed ratings into
   preferences automatically ("you rated Aldi kale 1/5: block it?").
3. **Checkout handoff.** Export each shop's part of the plan as a list, or fill
   the online basket for the delivery part.
