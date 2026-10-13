"""Match supermarket product names (from receipts or price feeds) to generic items."""

from __future__ import annotations

import difflib
import re
from typing import Iterable

from .models import Item

# Words that say who made it or how it's marketed, not what it is.
NOISE = {
    "tesco", "finest", "stockwell", "ms", "molly", "woodsides", "sainsburys", "sainsbury's",
    "taste", "difference", "stamford", "street", "aldi", "specially", "selected", "everyday",
    "essentials", "lidl", "deluxe", "asda", "extra", "special", "just", "essential", "morrisons",
    "savers", "the", "best", "waitrose", "ocado", "co", "op", "irresistible", "by", "and", "&",
}
SIZE = re.compile(r"\b\d+(\.\d+)?\s*(x\s*\d+\s*)?(g|kg|ml|l|ltr|litre|pint|pints|pk|pack|cl|s)?\b")


def normalise(name: str) -> str:
    name = name.casefold().replace("'", "")
    name = SIZE.sub(" ", name)
    words = [w for w in re.split(r"[^a-z0-9]+", name) if w and w not in NOISE]
    return " ".join(words)


def match_item(name: str, items: Iterable[Item], cutoff: float = 0.8) -> str | None:
    """The id of the item `name` most likely is, or None if nothing fits well."""
    text = normalise(name)
    words = set(text.split())
    best: tuple[int, str] | None = None
    names: dict[str, str] = {}
    for item in items:
        names[normalise(item.name)] = item.id
        for phrase in (item.keywords or (item.name,)):
            phrase = normalise(phrase)
            if phrase and set(phrase.split()) <= words:
                score = len(phrase)
                if best is None or score > best[0]:
                    best = (score, item.id)
    if best:
        return best[1]
    close = difflib.get_close_matches(text, list(names), n=1, cutoff=cutoff)
    return names[close[0]] if close else None
