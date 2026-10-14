"""Show shortlisted plans side by side, as plain text or a standalone HTML page."""

from __future__ import annotations

import html
from string import ascii_uppercase

from .matching import telling_words
from .optimizer import Plan


def _cell(plan: Plan, item_id: str) -> str:
    line = plan.line_for(item_id)
    if line is None:
        return "—"
    c = line.choice
    packs = f"{c.packs}× " if c.packs > 1 else ""
    return f"{line.channel.store.name}: {packs}{c.product.name} £{c.cost:.2f}"


def _summary(plan: Plan) -> str:
    parts = [f"goods £{plan.goods:.2f}"]
    for channel in plan.channels:
        if channel.label in plan.fees:
            parts.append(f"{channel.label} £{plan.fees[channel.label]:.2f}")
    if plan.mileage:
        parts.append(f"fuel ~£{plan.mileage:.2f}")
    if plan.route:
        parts[-1] += f" ({plan.miles:g} mi: home → {' → '.join(s.name for s in plan.route)} → home)"
    trips = []
    if plan.stops:
        trips.append(f"{plan.stops} stop{'s' * (plan.stops > 1)}")
    if plan.deliveries:
        trips.append(f"{plan.deliveries} deliver{'ies' if plan.deliveries > 1 else 'y'}")
    return " + ".join(parts) + " · " + ", ".join(trips)


def _differing_rows(plans: list[Plan], show_all: bool) -> list[tuple[str, str]]:
    first = plans[0]
    rows = []
    for line in first.lines:
        item_id = line.entry.item_id
        keys = {(p.line_for(item_id).choice.product.key if p.line_for(item_id) else None) for p in plans}
        if show_all or len(keys) > 1:
            rows.append((item_id, line.item.name))
    return rows


def render_text(shortlisted: list[tuple[str, Plan]], show_all: bool = False) -> str:
    if not shortlisted:
        return "No way to buy this list with the shops and prices we know about."
    plans = [p for _, p in shortlisted]
    out = []
    for letter, (title, plan) in zip(ascii_uppercase, shortlisted):
        out.append(f"{letter}  £{plan.cash:7.2f}  {plan.label}")
        out.append(f"   {title}: {_summary(plan)}")
    unavailable = plans[0].unavailable
    if unavailable:
        out.append(f"\nNot found anywhere (buy separately): {', '.join(unavailable)}")

    rows = _differing_rows(plans, show_all)
    out.append("")
    if not rows:
        out.append("Every option buys the same products.")
        return "\n".join(out)
    out.append("Where the options differ:" if not show_all else "Item by item:")
    name_width = max(len(name) for _, name in rows)
    for item_id, name in rows:
        groups: dict[str, list[str]] = {}
        for letter, plan in zip(ascii_uppercase, plans):
            groups.setdefault(_cell(plan, item_id), []).append(letter)
        letters_width = max(len(",".join(g)) for g in groups.values())
        for n, (cell, letters) in enumerate(groups.items()):
            label = name if n == 0 else ""
            out.append(f"  {label:<{name_width}}  {','.join(letters):<{letters_width}}  {cell}")
    return "\n".join(out)


def _html_cell(plan: Plan, item_id: str, changed: bool) -> str:
    line = plan.line_for(item_id)
    if line is None:
        return "<td>—</td>"
    c = line.choice
    packs = f"{c.packs}× " if c.packs > 1 else ""
    return (f"<td class='{'changed' if changed else ''}'><div class='store'>{html.escape(line.channel.store.name)}</div>"
            f"{packs}{html.escape(c.product.name)} <span class='cost'>£{c.cost:.2f}</span></td>")


def render_html(shortlisted: list[tuple[str, Plan]]) -> str:
    plans = [p for _, p in shortlisted]
    e = html.escape
    head = "".join(
        f"<th><div class='opt'>{letter}</div><div class='price'>£{plan.cash:.2f}</div>"
        f"<div class='title'>{e(title)}</div><div class='where'>{e(plan.label)}</div>"
        f"<div class='sum'>{e(_summary(plan))}</div></th>"
        for letter, (title, plan) in zip(ascii_uppercase, shortlisted)
    )
    body = []
    differing = {item_id for item_id, _ in _differing_rows(plans, show_all=False)} if plans else set()
    for item_id, name in (_differing_rows(plans, show_all=True) if plans else []):
        cls = "diff" if item_id in differing else "same"
        first = _cell(plans[0], item_id)
        cells = "".join(_html_cell(p, item_id, changed=_cell(p, item_id) != first) for p in plans)
        body.append(f"<tr class='{cls}'><th scope='row'>{e(name)}</th>{cells}</tr>")
    unavailable = plans[0].unavailable if plans else []
    missing = (f"<p class='missing'>Not found anywhere: {e(', '.join(unavailable))}</p>" if unavailable else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shopping Options</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1f2328; --muted:#646b73; --line:#e3e0d8; --diff:#fff4d6; --accent:#1a7f5a; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{ --bg:#16181b; --fg:#e8e6e1; --muted:#9aa0a6; --line:#2c3035; --diff:#3a3220; --accent:#4cc38a; }}
}}
:root[data-theme="dark"] {{ --bg:#16181b; --fg:#e8e6e1; --muted:#9aa0a6; --line:#2c3035; --diff:#3a3220; --accent:#4cc38a; }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
  font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
p {{ color:var(--muted); margin:0 0 16px; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; min-width:100%; }}
th, td {{ text-align:left; vertical-align:top; padding:8px 10px; border-bottom:1px solid var(--line); }}
thead th {{ min-width:180px; }}
.opt {{ font-size:12px; color:var(--muted); }}
.price {{ font-size:22px; font-weight:650; color:var(--accent); font-variant-numeric:tabular-nums; }}
.title {{ font-weight:600; }}
.where, .sum {{ font-weight:400; font-size:13px; color:var(--muted); }}
td.changed {{ background:var(--diff); }}
.store {{ font-size:12px; color:var(--muted); }}
.cost {{ font-variant-numeric:tabular-nums; font-weight:600; white-space:nowrap; }}
tr.same td {{ color:var(--muted); }}
label {{ display:inline-flex; gap:6px; align-items:center; margin-bottom:12px; color:var(--muted); }}
body.only-diff tr.same {{ display:none; }}
.missing {{ color:#b42318; }}
</style></head>
<body class="only-diff">
<h1>Shopping options</h1>
<p>Highlighted cells are where an option buys something different from option A.</p>
<label><input type="checkbox" id="all"> Show every item</label>
{missing}
<div class="wrap"><table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>
<script>
document.getElementById('all').addEventListener('change', e =>
  document.body.classList.toggle('only-diff', !e.target.checked));
</script>
</body></html>
"""


def comparison(shortlisted: list[tuple[str, Plan]]) -> dict:
    """The shortlist as plain data, for the web app."""
    plans = [p for _, p in shortlisted]
    options = [{
        "letter": letter, "title": title, "cash": plan.cash, "label": plan.label, "summary": _summary(plan),
        "miles": plan.miles, "route": [s.name for s in plan.route],
        "deliveries": [c.label for c in plan.channels if c.mode == "delivery"],
    } for letter, (title, plan) in zip(ascii_uppercase, shortlisted)]
    differing = {item_id for item_id, _ in _differing_rows(plans, show_all=False)} if plans else set()
    rows = []
    for item_id, name in (_differing_rows(plans, show_all=True) if plans else []):
        cells = []
        for plan in plans:
            line = plan.line_for(item_id)
            cells.append(None if line is None else {
                "store": line.channel.store.name, "product": line.choice.product.name, "packs": line.choice.packs,
                "cost": line.choice.cost, "url": line.choice.product.url, "key": line.choice.product.key,
                "chain": line.choice.product.chain, "own_label": line.choice.product.tier.is_own_label,
                "telling": telling_words(line.choice.product.name, " ".join((line.item.name, *line.item.keywords)))[:4],
            })
        rows.append({"item_id": item_id, "name": name, "differs": item_id in differing, "cells": cells})
    return {"options": options, "rows": rows, "unavailable": plans[0].unavailable if plans else []}
