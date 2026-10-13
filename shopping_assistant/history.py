"""Learn buying habits from purchase history: what's probably due again."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from .models import Purchase


@dataclass(frozen=True)
class Habit:
    item_id: str
    times_bought: int
    every_days: float  # median gap between purchases
    last_bought: date
    usual_qty: float

    def days_since(self, today: date) -> int:
        return (today - self.last_bought).days

    def due_in(self, today: date) -> float:
        return self.every_days - self.days_since(today)


def habits(purchases: Iterable[Purchase], min_times: int = 3) -> dict[str, Habit]:
    days: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for p in purchases:
        if p.item_id:
            days[p.item_id][date.fromisoformat(p.date)] += p.qty
    result = {}
    for item_id, by_day in days.items():
        dates = sorted(by_day)
        if len(dates) < min_times:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        result[item_id] = Habit(
            item_id=item_id,
            times_bought=len(dates),
            every_days=statistics.median(gaps),
            last_bought=dates[-1],
            usual_qty=statistics.median(by_day.values()),
        )
    return result


def due(purchases: Iterable[Purchase], today: date, horizon_days: float = 3) -> list[Habit]:
    """Items you usually have bought again by `horizon_days` from today, most overdue first."""
    found = [h for h in habits(purchases).values() if h.due_in(today) <= horizon_days]
    return sorted(found, key=lambda h: h.due_in(today))
