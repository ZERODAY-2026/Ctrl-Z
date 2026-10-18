"""Visualization studio: user-selectable charts built only from the rows a query returned.

The studio never aggregates. Snowflake already computed every value, so a chart either plots the
returned rows as they are or explains why the chosen form does not fit them (and suggests one that does).
`StudioChart.plotted` records exactly what was drawn so tests and the UI can check it against the table.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

import plotly.graph_objects as go

from src.services.grounding_service import fmt_number, is_identifier_column
from src.services.visualization_service import (DATE_TYPES, NUMERIC_TYPES, _looks_like_year, _ticks, _to_float,
                                                fmt_compact, humanize)

KINDS = ("bar", "line", "donut", "area", "scatter")
KIND_LABELS = {"bar": "Bar chart", "line": "Line chart", "donut": "Pie chart", "area": "Area chart",
               "scatter": "Scatter plot"}
KIND_HINTS = {"bar": "Rankings and category comparisons", "line": "Trends over time or ordered sequences",
              "donut": "Part-to-whole proportions", "area": "Cumulative or time-based patterns",
              "scatter": "Relationships between two numbers"}

# Categorical order validated with the dataviz palette checker against the #111821 chart surface
# (CVD ΔE 7.9 on one adjacent pair: legal only with secondary encoding, so legends and 2px gaps are always on).
PALETTE = ["#4778FF", "#B8800F", "#0FA89A", "#E8556B", "#9168F0", "#1FA36E"]
# Single-series bars: each bar is named on its axis, so colour carries no identity there. A decorative
# teal -> blue -> sky -> sand -> orange sweep (the DataMind look) is interpolated across the bars in order.
RAMP = ["#22C7B0", "#1497C9", "#5CC4EE", "#E3CC8C", "#EF9A4A"]
SURFACE = "#111821"
INK_MUTED = "#98A6B8"
GRID = "rgba(152,166,184,0.14)"

MAX_BAR_ROWS = 40
MAX_DONUT_SLICES = 8
MAX_SERIES = len(PALETTE)
LABEL_CHARS = 28
# Measures whose values do not add up to a meaningful whole (a donut of averages is meaningless).
_NON_ADDITIVE = re.compile(r"(^|_)(avg|average|mean|median|min|max|minimum|maximum|rate|ratio|per_capita|"
                           r"per|stddev|pct_change|growth|balance|price|discount|tax)(_|$)", re.I)
_SHARE = re.compile(r"(share|pct|percent|percentage)", re.I)


@dataclass
class ResultProfile:
    columns: list[str]
    measures: list[str]  # numeric, not identifiers or years
    categories: list[str]  # text / boolean labels
    times: list[str]  # dates, timestamps and year-like integers
    ids: list[str]  # numeric key columns
    n_rows: int


@dataclass
class ChartSpec:
    kind: str
    x: str | None = None
    y: str | None = None
    series: str | None = None
    sort: str = "query"  # "query" (Snowflake's order), "desc", "asc"
    top_n: int | None = None
    sort_by: str | None = None  # column to sort by when sort != "query" (default: the Y measure)
    labels: bool = True  # value labels on bars / points

    def as_dict(self) -> dict[str, Any]:
        return dict(kind=self.kind, x=self.x, y=self.y, series=self.series, sort=self.sort, top_n=self.top_n,
                    sort_by=self.sort_by, labels=self.labels)


@dataclass
class StudioChart:
    spec: ChartSpec
    figure: go.Figure | None
    caption: str
    notes: list[str] = field(default_factory=list)
    problem: str | None = None  # why the selected chart does not fit this result
    suggestion: str | None = None  # a kind that does fit
    plotted: list[tuple[str, object]] = field(default_factory=list)


def profile(columns: list[str], types: list[str], rows: list[tuple]) -> ResultProfile:
    raw = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
    numeric = [c for c, t in zip(columns, types) if t in NUMERIC_TYPES
               and all(_to_float(v) is not None or v is None for v in raw[c])]
    ids = [c for c in numeric if is_identifier_column(c)]
    years = [c for c in numeric if _looks_like_year(raw[c], c)]
    dates = [c for c, t in zip(columns, types) if t in DATE_TYPES]
    measures = [c for c in numeric if c not in ids and c not in years]
    categories = [c for c in columns if c not in numeric and c not in dates]
    return ResultProfile(list(columns), measures, categories, dates + years, ids, len(rows))


# ---------------------------------------------------------------------------- option lists per chart kind
def x_options(kind: str, p: ResultProfile) -> list[str]:
    if kind == "scatter":
        return list(p.measures)
    if kind in ("line", "area"):
        return list(p.times)
    if kind == "donut":
        return list(p.categories)
    return p.categories + p.times + p.ids


def y_options(kind: str, p: ResultProfile) -> list[str]:
    return list(p.measures)


def series_options(kind: str, p: ResultProfile, x: str | None) -> list[str]:
    if kind == "donut":
        return []
    return [c for c in p.categories if c != x]


def recommend(p: ResultProfile) -> str | None:
    """Default chart kind for a result shape, or None when a table (or a single number) reads better."""
    if p.n_rows < 2 or not p.measures:
        return None
    if p.times:
        return "line"
    if p.categories or p.ids:
        return "bar"
    if len(p.measures) >= 2 and p.n_rows >= 3:
        return "scatter"
    return None


def default_spec(p: ResultProfile, kind: str | None = None) -> ChartSpec | None:
    kind = kind or recommend(p)
    if kind is None:
        return None
    xs, ys = x_options(kind, p), y_options(kind, p)
    if kind == "scatter":
        return ChartSpec(kind, xs[0] if xs else None, ys[1] if len(ys) > 1 else None)
    # Charting the last measure keeps the old auto-chart's choice (e.g. revenue over an order count).
    return ChartSpec(kind, xs[0] if xs else None, ys[-1] if ys else None)


def coerce(spec: ChartSpec, p: ResultProfile) -> ChartSpec:
    """Keep a spec's choices where they are still valid for its kind; fill the rest with defaults."""
    base = default_spec(p, spec.kind) or ChartSpec(spec.kind)
    x = spec.x if spec.x in x_options(spec.kind, p) else base.x
    y = spec.y if spec.y in y_options(spec.kind, p) else base.y
    if spec.kind == "scatter" and y == x:
        y = next((c for c in y_options("scatter", p) if c != x), None)
    series = spec.series if spec.series in series_options(spec.kind, p, x) else None
    sort_by = spec.sort_by if spec.sort_by in p.columns else None
    return replace(spec, x=x, y=y, series=series, sort_by=sort_by)


# ---------------------------------------------------------------------------- compatibility
def check(spec: ChartSpec, p: ResultProfile, columns: list[str], rows: list[tuple]) -> tuple[str | None, str | None]:
    """Return (problem, suggested kind). problem is None when the chart honestly fits the rows."""
    alt = recommend(p)
    alt = alt if alt != spec.kind else None
    if p.n_rows == 0:
        return "No rows were returned, so there is nothing to chart.", None
    if not p.measures:
        return "This result has no numeric measure to plot; read it in the Results table.", None
    k = spec.kind
    if k == "scatter":
        if len(p.measures) < 2:
            return ("A scatter plot needs two numeric measures; this result has one "
                    f"({humanize(p.measures[0])})."), alt
        if p.n_rows < 3:
            return "A scatter plot needs at least three rows to show a relationship.", alt
        return None, None
    if spec.x is None:
        need = {"line": "a date, timestamp or year column", "area": "a date, timestamp or year column",
                "donut": "a text category column", "bar": "a category column"}[k]
        return f"A {KIND_LABELS[k].lower()} needs {need}; this result has none.", alt
    if p.n_rows < 2:
        return "Only one row was returned; the value is shown in the answer card.", None

    idx = {c: i for i, c in enumerate(columns)}
    keys = [(r[idx[spec.x]], r[idx[spec.series]] if spec.series else None) for r in rows]
    if len(set(keys)) < len(keys):
        dup = next(str(k[0]) for k in keys if keys.count(k) > 1)
        what = f"{humanize(spec.x)} and {humanize(spec.series)}" if spec.series else humanize(spec.x)
        return (f"Several rows share the same {what} (for example '{dup}'). Pick a series column to separate them; "
                "the studio never adds rows together, because that could change the meaning of the answer."), alt
    if spec.series:
        n = len({r[idx[spec.series]] for r in rows})
        if n > MAX_SERIES:
            return f"{humanize(spec.series)} has {n} values; at most {MAX_SERIES} series stay readable.", None

    if k == "bar" and p.n_rows > MAX_BAR_ROWS and not spec.top_n:
        return (f"{p.n_rows} bars would not be readable. Set Top N (up to {MAX_BAR_ROWS}) to chart the leading "
                "rows."), None
    if k == "donut":
        y = spec.y or ""
        vals = [_to_float(r[idx[y]]) for r in rows] if y else []
        if _NON_ADDITIVE.search(y) and not _SHARE.search(y):
            return (f"{humanize(y)} is not a quantity that adds up to a whole (an average, rate or balance), so "
                    "a part-to-whole chart would mislead. A bar chart compares these values honestly."), "bar"
        if any(v is not None and v < 0 for v in vals):
            return "A donut cannot show negative values; use a bar chart.", "bar"
        shown = min(p.n_rows, spec.top_n or p.n_rows)
        if shown > MAX_DONUT_SLICES:
            return (f"{shown} slices are too many to compare by angle. Set Top N to {MAX_DONUT_SLICES} or fewer, "
                    "or use a bar chart."), "bar"
        if sum(v for v in vals if v is not None) <= 0:
            return "The values sum to zero, so there is no whole to divide.", "bar"
    return None, None


# ---------------------------------------------------------------------------- figure building
def build(spec: ChartSpec, columns: list[str], types: list[str], rows: list[tuple], *,
          partial: bool = False, height: int = 380) -> StudioChart:
    """Plot `rows` exactly as returned. `partial` = the rows are a top-N cut or truncated, not a full set."""
    p = profile(columns, types, rows)
    spec = coerce(spec, p)
    problem, alt = check(spec, p, columns, rows)
    if problem:
        return StudioChart(spec, None, "", problem=problem, suggestion=alt)

    idx = {c: i for i, c in enumerate(columns)}
    data = list(rows)
    notes: list[str] = []
    if spec.sort in ("asc", "desc") and spec.kind not in ("line", "area"):
        key = idx[spec.sort_by or (spec.y if spec.kind != "scatter" else spec.x)]
        num = all(_to_float(r[key]) is not None or r[key] is None for r in data)
        val = (lambda r: _to_float(r[key])) if num else (lambda r: str(r[key]))
        known = sorted((r for r in data if r[key] is not None), key=val, reverse=spec.sort == "desc")
        data = known + [r for r in data if r[key] is None]  # NULLs last either way
    if spec.top_n and spec.top_n < len(data):
        data = data[:spec.top_n]
        notes.append(f"Showing the first {spec.top_n} of {len(rows)} returned rows"
                     + (" after sorting." if spec.sort != "query" else " in the query's order."))
    if spec.kind in ("line", "area"):
        data.sort(key=lambda r: (r[idx[spec.x]] is None, r[idx[spec.x]]))

    builder = {"bar": _bar, "line": _line, "area": _line, "donut": _donut, "scatter": _scatter}[spec.kind]
    fig, plotted, dropped = builder(spec, idx, data)
    if dropped:
        notes.append(f"{dropped} row(s) with a NULL value are not plotted (they are in the Results table).")
    if spec.kind == "donut" and (partial or spec.top_n):
        notes.append("Shares are of the rows shown, not of the whole dataset (the result is a top-N cut or "
                     "was truncated).")
    if spec.kind == "bar":
        ys = [v for _, v in plotted if v is not None]
        fl = [float(v) for v in ys]
        if fl and min(fl) > 0 and (max(fl) - min(fl)) / max(fl) < 0.10:
            notes.append("These values are within 10% of each other, so the zero-based bars look similar. "
                         "Hover for exact values.")
    _style(fig, spec, height)
    caption = f"Visualization of {len(plotted)} returned row{'s' if len(plotted) != 1 else ''}"
    return StudioChart(spec, fig, caption, notes, plotted=plotted)


def _short(label: str) -> str:
    return label if len(label) <= LABEL_CHARS else label[:LABEL_CHARS - 1] + "…"


def _label(v: Any) -> str:
    return "(NULL)" if v is None else str(v)


def _groups(spec: ChartSpec, idx: dict[str, int], data: list[tuple]) -> list[tuple[str | None, list[tuple]]]:
    if not spec.series:
        return [(None, data)]
    order: dict[Any, list[tuple]] = {}
    for r in data:
        order.setdefault(r[idx[spec.series]], []).append(r)
    return [(_label(k), v) for k, v in order.items()]


def _ramp(n: int) -> list[str]:
    """n colours sampled evenly along RAMP."""
    if n <= 1:
        return RAMP[:1]
    rgb = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in RAMP]
    out = []
    for k in range(n):
        t = k / (n - 1) * (len(rgb) - 1)
        a, f = min(int(t), len(rgb) - 2), t - min(int(t), len(rgb) - 2)
        out.append("#" + "".join(f"{round(rgb[a][c] + (rgb[a + 1][c] - rgb[a][c]) * f):02X}" for c in range(3)))
    return out


def _bar(spec, idx, data):
    # Few, short categories stand upright (the DataMind look); many or long labels lie horizontal so they stay
    # readable. Dates and years always run left to right.
    xs = [r[idx[spec.x]] for r in data]
    texty = all(isinstance(v, str) or v is None for v in xs)
    horizontal = texty and (len(set(xs)) > 12 or max(len(_label(v)) for v in xs) > 18)
    groups = _groups(spec, idx, data)
    plotted, dropped, fig = [], 0, go.Figure()
    for n, (name, rows) in enumerate(groups):
        kept = [r for r in rows if _to_float(r[idx[spec.y]]) is not None]
        dropped += len(rows) - len(kept)
        cats = [_label(r[idx[spec.x]]) for r in kept]
        vals = [_to_float(r[idx[spec.y]]) for r in kept]
        exact = [fmt_number(r[idx[spec.y]]) for r in kept]
        plotted += [(c, r[idx[spec.y]]) for c, r in zip(cats, kept)]
        color = PALETTE[n % len(PALETTE)] if len(groups) > 1 else _ramp(len(kept))
        trace = dict(name=name or humanize(spec.y), marker=dict(color=color, line=dict(color=SURFACE, width=2)),
                     customdata=list(zip(cats, exact)),
                     text=[_compact(v) for v in vals] if spec.labels else None, textposition="outside",
                     textfont=dict(color="#DCE3EC", size=12), cliponaxis=False,
                     hovertemplate="%{customdata[0]}<br>" + humanize(spec.y) + ": %{customdata[1]}<extra>"
                     + (name or "") + "</extra>")
        if horizontal:
            fig.add_bar(x=vals, y=[_short(c) for c in cats], orientation="h", **trace)
        else:
            fig.add_bar(x=cats, y=vals, **trace)
    ys = [float(v) for _, v in plotted if v is not None]
    lo, hi = min([0.0, *ys]), max([0.0, *ys])
    tv, tt = _ticks(lo, hi, integer=all(isinstance(v, int) for _, v in plotted))
    pad = 1.18 if spec.labels else 1.08
    value_axis = dict(tickvals=tv, ticktext=tt, range=[lo * pad, hi * pad], zeroline=True,
                      zerolinecolor="rgba(152,166,184,0.45)", title=humanize(spec.y))
    order = list(dict.fromkeys(_short(c) if horizontal else c for c, _ in plotted))
    cat_axis = dict(title=humanize(spec.x), type="category", categoryorder="array",
                    categoryarray=order[::-1] if horizontal else order)  # first row at the top / left
    fig.update_layout(barmode="group", barcornerradius=4, bargap=0.22 if len(groups) == 1 else 0.28)
    (fig.update_xaxes if horizontal else fig.update_yaxes)(**value_axis)
    (fig.update_yaxes if horizontal else fig.update_xaxes)(**cat_axis)
    return fig, plotted, dropped


def _line(spec, idx, data):
    area = spec.kind == "area"
    plotted, dropped, fig = [], 0, go.Figure()
    groups = _groups(spec, idx, data)
    for n, (name, rows) in enumerate(groups):
        kept = [r for r in rows if _to_float(r[idx[spec.y]]) is not None and r[idx[spec.x]] is not None]
        dropped += len(rows) - len(kept)
        xs = [r[idx[spec.x]] for r in kept]
        color = PALETTE[n % len(PALETTE)]
        plotted += [(str(x), r[idx[spec.y]]) for x, r in zip(xs, kept)]
        fig.add_scatter(
            x=xs, y=[_to_float(r[idx[spec.y]]) for r in kept], name=name or humanize(spec.y),
            mode="lines+markers+text" if spec.labels and len(kept) <= 12 else "lines+markers",
            text=[_compact(_to_float(r[idx[spec.y]])) for r in kept], textposition="top center",
            textfont=dict(color="#DCE3EC", size=11), line=dict(color=color, width=2), marker=dict(size=8, color=color,
            line=dict(color=SURFACE, width=2)), fill="tozeroy" if area else None,
            fillcolor=_alpha(color, 0.18 if len(groups) == 1 else 0.10) if area else None,
            customdata=[fmt_number(r[idx[spec.y]]) for r in kept],
            hovertemplate="%{x}<br>" + humanize(spec.y) + ": %{customdata}<extra>" + (name or "") + "</extra>")
    ys = [float(v) for _, v in plotted]
    lo, hi = min([0.0, *ys]), max([0.0, *ys])
    tv, tt = _ticks(lo, hi, integer=all(isinstance(v, int) for _, v in plotted))
    fig.update_yaxes(tickvals=tv, ticktext=tt, rangemode="tozero", title=humanize(spec.y))
    fig.update_xaxes(title=humanize(spec.x), tickformat="d" if spec.x and "YEAR" in spec.x.upper() else None)
    fig.update_layout(hovermode="x unified" if len(groups) > 1 else "closest")
    return fig, plotted, dropped


def _donut(spec, idx, data):
    kept = [r for r in data if _to_float(r[idx[spec.y]]) is not None]
    labels = [_label(r[idx[spec.x]]) for r in kept]
    exact = [fmt_number(r[idx[spec.y]]) for r in kept]
    fig = go.Figure(go.Pie(
        labels=labels, values=[_to_float(r[idx[spec.y]]) for r in kept], hole=0.58, sort=False,
        direction="clockwise", marker=dict(colors=PALETTE[:len(kept)] + PALETTE[:max(0, len(kept) - len(PALETTE))],
                                            line=dict(color=SURFACE, width=2)),
        customdata=exact, textinfo="percent" if spec.labels else "none", textfont=dict(color="#F4F7FB"),
        hovertemplate="%{label}<br>" + humanize(spec.y) + ": %{customdata}<br>%{percent} of shown<extra></extra>"))
    return fig, [(lab, r[idx[spec.y]]) for lab, r in zip(labels, kept)], len(data) - len(kept)


def _scatter(spec, idx, data):
    plotted, dropped, fig = [], 0, go.Figure()
    # A text column (e.g. a nation name) identifies each point in the tooltip.
    label_col = next((c for c in idx if c not in (spec.x, spec.y, spec.series)
                      and all(isinstance(r[idx[c]], str) or r[idx[c]] is None for r in data)), None)
    for n, (name, rows) in enumerate(_groups(spec, idx, data)):
        kept = [r for r in rows if _to_float(r[idx[spec.x]]) is not None and _to_float(r[idx[spec.y]]) is not None]
        dropped += len(rows) - len(kept)
        plotted += [(fmt_number(r[idx[spec.x]]), r[idx[spec.y]]) for r in kept]
        color = PALETTE[n % len(PALETTE)]
        fig.add_scatter(
            x=[_to_float(r[idx[spec.x]]) for r in kept], y=[_to_float(r[idx[spec.y]]) for r in kept],
            mode="markers", name=name or "rows", marker=dict(size=10, color=color, opacity=0.85,
                                                              line=dict(color=SURFACE, width=2)),
            customdata=[(_label(r[idx[label_col]]) if label_col else "", fmt_number(r[idx[spec.x]]),
                         fmt_number(r[idx[spec.y]])) for r in kept],
            hovertemplate="%{customdata[0]}<br>" + humanize(spec.x) + ": %{customdata[1]}<br>" + humanize(spec.y)
            + ": %{customdata[2]}<extra></extra>")
    for axis, col in (("x", spec.x), ("y", spec.y)):
        vals = [float(_to_float(r[idx[col]])) for r in data if _to_float(r[idx[col]]) is not None]
        tv, tt = _ticks(min(vals), max(vals)) if vals else ([], [])
        (fig.update_xaxes if axis == "x" else fig.update_yaxes)(tickvals=tv, ticktext=tt, title=humanize(col))
    return fig, plotted, dropped


def _compact(v: float | None) -> str:
    if v is None:
        return ""
    return fmt_compact(v, 2) if abs(v) >= 1000 else (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}")


def _alpha(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def _style(fig: go.Figure, spec: ChartSpec, height: int) -> None:
    multi = len(fig.data) > 1 or spec.kind == "donut"
    fig.update_layout(
        height=height, margin=dict(l=8, r=16, t=16, b=8), paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, system-ui, sans-serif", size=12, color=INK_MUTED),
        showlegend=multi, legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#161F2B", bordercolor="#283442", font=dict(color="#F4F7FB",
                        family="Inter, system-ui, sans-serif")),
        separators=".,")
    fig.update_xaxes(gridcolor=GRID, linecolor="#283442", automargin=True, title_font=dict(size=12))
    fig.update_yaxes(gridcolor=GRID, linecolor="#283442", automargin=True, title_font=dict(size=12))


# ---------------------------------------------------------------------------- grounded insights
def insights(spec: ChartSpec, columns: list[str], rows: list[tuple], *, partial: bool) -> list[str]:
    """Plain statements computed directly from the returned rows (no model involved)."""
    if not rows or not spec.y or spec.kind == "scatter" or spec.x is None:
        return []
    idx = {c: i for i, c in enumerate(columns)}
    if spec.x not in idx or spec.y not in idx:
        return []
    pts = [(r[idx[spec.x]], r[idx[spec.y]]) for r in rows if _to_float(r[idx[spec.y]]) is not None]
    if len(pts) < 2:
        return []
    m, d = humanize(spec.y), humanize(spec.x)
    hi = max(pts, key=lambda t: _to_float(t[1]))
    lo = min(pts, key=lambda t: _to_float(t[1]))
    out = [f"Highest {m}: {_label(hi[0])} ({fmt_number(hi[1])}).", f"Lowest {m}: {_label(lo[0])} ({fmt_number(lo[1])})."]
    if spec.kind in ("line", "area"):
        ordered = sorted(pts, key=lambda t: t[0])
        first, last = _to_float(ordered[0][1]), _to_float(ordered[-1][1])
        change = f"{(last - first) / abs(first) * 100:+.1f}%" if first else "n/a (starts at zero)"
        out.append(f"From {_label(ordered[0][0])} to {_label(ordered[-1][0])}, {m} went from "
                   f"{fmt_number(ordered[0][1])} to {fmt_number(ordered[-1][1])} ({change}).")
    elif not _NON_ADDITIVE.search(spec.y) and not _SHARE.search(spec.y) and not spec.series:
        total = sum(_to_float(v) for _, v in pts)
        if total > 0 and all(_to_float(v) >= 0 for _, v in pts):
            share = _to_float(hi[1]) / total * 100
            scope = "the rows returned" if partial else f"all {len(pts)} {d.lower()} values returned"
            out.append(f"{_label(hi[0])} accounts for {share:.1f}% of the {m.lower()} across {scope}.")
    if hi[1] != lo[1] and _to_float(lo[1]) > 0:
        ratio = _to_float(hi[1]) / _to_float(lo[1])
        if math.isfinite(ratio):
            out.append(f"The highest is {ratio:,.2f}× the lowest.")
    return out
