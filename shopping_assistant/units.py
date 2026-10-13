"""Pack sizes: read them from product names and compare them across units."""

from __future__ import annotations

import re

# Everything is compared in grams, millilitres or a count.
TO_BASE = {
    "g": (1, "g"), "kg": (1000, "g"),
    "ml": (1, "ml"), "cl": (10, "ml"), "l": (1000, "ml"), "ltr": (1000, "ml"), "litre": (1000, "ml"),
    "litres": (1000, "ml"), "pint": (568, "ml"), "pints": (568, "ml"), "pt": (568, "ml"),
    "each": (1, "each"),
}

_NUM = r"(\d+(?:\.\d+)?)"
_MULTI = re.compile(rf"\b(\d+)\s*x\s*{_NUM}\s*(kg|g|ml|cl|l)\b", re.I)
_MEASURE = re.compile(rf"(?<![\d.]){_NUM}\s*(kg|g|ml|cl|ltr|litres|litre|l|pints|pint|pt)\b", re.I)
_COUNT = re.compile(r"\b(\d+)\s*(?:x\s*)?(?:pack|pk|eggs|tea ?bags|bags|rolls|pods|capsules|sachets|slices|"
                    r"tablets|pieces|count|each)\b", re.I)


def to_base(qty: float, unit: str) -> tuple[float, str]:
    factor, base = TO_BASE.get(unit.lower(), (1, unit.lower()))
    return qty * factor, base


def parse_sizes(name: str) -> list[tuple[float, str]]:
    """Every size mentioned in a product name, in base units.

    "Yorkshire Tea 80 Tea Bags 250g" -> [(80, "each"), (250, "g")]
    "Walkers Crisps 6 x 25g"         -> [(150, "g"), (6, "each")]
    """
    sizes: list[tuple[float, str]] = []
    rest = name
    for m in _MULTI.finditer(name):
        sizes.append(to_base(int(m[1]) * float(m[2]), m[3]))
        sizes.append((float(m[1]), "each"))
        rest = rest.replace(m[0], " ")
    for m in _COUNT.finditer(rest):
        sizes.append((float(m[1]), "each"))
    for m in _MEASURE.finditer(rest):
        sizes.append(to_base(float(m[1]), m[2]))
    seen: set[tuple[float, str]] = set()
    return [s for s in sizes if not (s in seen or seen.add(s))]


def size_in(name: str, unit: str) -> float | None:
    """The size from `name` in the same base unit as `unit`, if the name gives one."""
    base = to_base(1, unit)[1]
    return next((qty for qty, u in parse_sizes(name) if u == base), None)
