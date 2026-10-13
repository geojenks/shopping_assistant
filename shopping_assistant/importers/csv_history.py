"""Generic CSV purchase history: date,store,product,qty,price.

Use it for anything without a dedicated importer: Sainsbury's/Asda order
emails, Aldi or Lidl receipts typed or scanned in, a spreadsheet you already keep.
`price` is the total paid for the line; `qty` defaults to 1.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import Purchase


def read_csv_history(path: str | Path) -> list[Purchase]:
    purchases = []
    line_numbers: dict[str, int] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            date = row["date"][:10]
            chain = row.get("store", row.get("chain", "")).lower()
            txn = f"{date}:{chain}"
            line_numbers[txn] = line_numbers.get(txn, 0) + 1
            purchases.append(Purchase(
                date=date,
                chain=chain,
                product_name=row["product"],
                qty=float(row.get("qty") or 1),
                price=float(row["price"].lstrip("£")),
                source=f"csv:{Path(path).name}",
                txn_id=txn,
                line_no=line_numbers[txn],
            ))
    return purchases
