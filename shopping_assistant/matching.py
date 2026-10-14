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


def has_phrase(name: str, phrase: str) -> bool:
    """`phrase`'s words appear together and in order in `name` ("almond butter", not "butter almond")."""
    words, want = normalise(name).split(), normalise(phrase).split()
    return bool(want) and any(words[i:i + len(want)] == want for i in range(len(words) - len(want) + 1))


def keyword_matches(keyword: str, name: str) -> bool:
    """A keyword matches if all its words are in the name; in "quotes", only as that exact phrase."""
    if len(keyword) > 2 and keyword[0] == keyword[-1] == '"':
        return has_phrase(name, keyword[1:-1])
    want = set(normalise(keyword).split())
    return bool(want) and want <= set(normalise(name).split())


FILLER = {"with", "of", "in", "for", "from", "all", "x", "a", "pack", "each", "new", "our", "classic"}


def telling_words(name: str, query: str) -> list[str]:
    """Words that set a product apart from what was asked for: "croissants" in
    "All Butter Almond Croissants" when you wanted "almond butter"."""
    asked = set(normalise(query).split())
    seen: list[str] = []
    for word in normalise(name).split():
        if word not in asked and word not in FILLER and len(word) > 2 and not word.isdigit() and word not in seen:
            seen.append(word)
    return seen[::-1]  # the product type is usually last ("... croissants"), so offer it first
