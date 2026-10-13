"""Tesco "request your data" export (tesco.com > My account > request your data).

The export lists each Clubcard transaction (in store and online) with the
items bought. We haven't pinned down the exact file layout yet, so this reader
walks the JSON looking for anything shaped like a transaction: an object with a
date and a list of product objects that each have a name and a price. Field
names are matched loosely; adjust the KEY lists below once a real export is
to hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..models import Purchase

DATE_KEYS = ("timeStamp", "timestamp", "transactionDate", "date", "dateTime", "purchaseDate")
TXN_KEYS = ("transactionID", "transactionId", "id", "receiptId")
STORE_KEYS = ("storeName", "store", "storeId", "location")
NAME_KEYS = ("name", "productName", "description", "itemDescription", "title")
QTY_KEYS = ("quantity", "qty", "count")
PRICE_KEYS = ("price", "value", "amount", "itemPrice", "lineValue")


def _first(obj: dict, keys: tuple[str, ...]) -> Any:
    lowered = {k.lower(): v for k, v in obj.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def _money(value: Any) -> float | None:
    if isinstance(value, dict):
        value = _first(value, ("amount", "value", "price"))
    try:
        return float(str(value).replace("£", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _product_list(obj: dict) -> list[dict] | None:
    for value in obj.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            if all(_first(v, NAME_KEYS) is not None and _first(v, PRICE_KEYS) is not None for v in value):
                return value
    return None


def _transactions(node: Any) -> Iterator[tuple[dict, list[dict]]]:
    if isinstance(node, dict):
        products = _product_list(node)
        if products is not None and _first(node, DATE_KEYS) is not None:
            yield node, products
            return
        for value in node.values():
            yield from _transactions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _transactions(value)


def read_tesco_export(path: str | Path) -> list[Purchase]:
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    purchases = []
    for n, (txn, products) in enumerate(_transactions(data)):
        date = str(_first(txn, DATE_KEYS))[:10]
        txn_id = str(_first(txn, TXN_KEYS) or f"{date}#{n}")
        for line_no, product in enumerate(products, start=1):
            qty = _money(_first(product, QTY_KEYS)) or 1.0
            price = _money(_first(product, PRICE_KEYS))
            if price is None:
                continue
            purchases.append(Purchase(
                date=date,
                chain="tesco",
                product_name=str(_first(product, NAME_KEYS)),
                qty=qty,
                price=price,
                source="tesco-export",
                txn_id=txn_id,
                line_no=line_no,
            ))
    return purchases
