"""Contextual follow-up suggestions derived from a finished answer's question and result shape.

They are only suggestions: clicking one sends a real question through the orchestrator with the
conversation history, exactly like a typed follow-up. Nothing here precomputes an answer.
"""

from __future__ import annotations

import re

from src.services.chart_studio import _NON_ADDITIVE, _SHARE, profile
from src.services.grounding_service import Evidence
from src.services.visualization_service import humanize

MAX_SUGGESTIONS = 4
_TOP_N = re.compile(r"\b(top|bottom|first|last)\s+(\d+)\b", re.I)


def suggest(question: str, ev: Evidence, categorical_columns: list[str]) -> list[str]:
    """Up to four follow-ups for an answered result. `categorical_columns` are 'TABLE.COLUMN' from config."""
    if not ev.rows:
        return []
    p = profile(ev.result_columns, ev.result_types, ev.rows)
    q = question.lower()
    out: list[str] = []
    measure = p.measures[-1] if p.measures else None
    m = humanize(measure).lower() if measure else ""
    label = (p.categories or [None])[0]

    if p.n_rows == 1 and measure:
        # A single number: offer breakdowns by low-cardinality columns of the tables this query actually used.
        used = {t.upper() for t in ev.tables}
        dims = [c.split(".", 1)[1] for c in categorical_columns if c.split(".", 1)[0] in used]
        out += [f"Break this down by {humanize(d).lower()}" for d in dims[:2]]
        if p.times or any(c.upper().endswith("DATE") for c in ev.columns_used):
            out.append("How has this changed by year?")
    if p.times and measure:
        t = humanize(p.times[0]).lower()
        out.append(f"Which {t} had the highest {m}?")
        out.append(f"Which {t} had the lowest {m}?")
    if label and measure and p.n_rows >= 3:
        lab = humanize(label).lower()
        hit = _TOP_N.search(q)
        if not hit or int(hit.group(2)) > 3:
            out.append(f"Show only the top 3 by {m}")
        out.append(f"Sort these from lowest to highest {m}")
        if not _NON_ADDITIVE.search(measure) and not _SHARE.search(measure) and "share" not in q and "%" not in q:
            whole = m if m.startswith("total ") else f"the total {m}"
            out.append(f"What share of {whole} does each {lab} account for?")
    if label and not measure and p.n_rows >= 2:
        out.append(f"How many rows are there for each {humanize(label).lower()}?")
    seen, unique = set(), []
    for s in out:
        if s.lower() not in seen and s.lower() != q.strip(" ?"):
            seen.add(s.lower())
            unique.append(s)
    return unique[:MAX_SUGGESTIONS]
