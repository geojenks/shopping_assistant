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
- **Getting it.** Each store can be a pickup (fuel at `per_mile` for the round
  trip, plus `per_stop` for your time) and/or a delivery (fee, minimum basket,
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

## Where the data comes from, honestly

| Need | Source | Status |
|---|---|---|
| Your history | Tesco: *My account → request your data* on tesco.com gives every Clubcard transaction with items. Sainsbury's (Nectar), Asda, Morrisons: subject access request, or online order history. | Tesco importer written against the export's described shape, not a real file yet: tweak once we see one. |
| Shopping list | Built in (`shop list`). Bring! has only an unofficial API (the `bring-api` Python package Home Assistant uses), which can break without notice. | Own list done. Bring! import is possible later. |
| Prices | No supermarket offers a public price API. The options are scraping each site's product search, a third-party feed, or entering prices by hand. | Not done: **the next big piece of work.** |
| Delivery | Tesco, Sainsbury's, Asda, Morrisons, Ocado, Waitrose deliver. Aldi and Lidl don't run their own delivery (Aldi is trialling Deliveroo "Shop & Deliver" at a few stores from October 2026). | Terms entered per store by hand; slot availability isn't checked. |

## Roadmap

1. **Real prices.** Scraper/collector per chain feeding `prices.csv`, matching
   products to items by name (and barcode where available). Run it on a schedule
   from home rather than a datacentre IP.
2. **Shared list on phones.** A small web app (PWA) over the same database so
   both of you can add "running low" / "run out" from the kitchen.
3. **Better suggestions.** Rate products after trying them; feed ratings into
   preferences automatically ("you rated Aldi kale 1/5: block it?").
4. **Checkout handoff.** Export each shop's part of the plan as a list, or fill
   the online basket for the delivery part.
