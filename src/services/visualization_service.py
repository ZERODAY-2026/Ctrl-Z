"""Choose a chart from the actual result shape. Returns kind "table"/"none" when a chart would not be honest.

Every plotted point comes from the returned rows, in the query's order; `ChartChoice.plotted` records
exactly what was drawn so tests (and the UI) can check it against the result table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.services.grounding_service import fmt_number, is_identifier_column

MAX_BAR_CATEGORIES = 30
# If every value lies within this fraction of the largest, zero-based bars look identical:
# use a dot plot on a zoomed axis instead, and say so.
NARROW_SPREAD = 0.10
NUMERIC_TYPES = {"FIXED", "REAL", "DECFLOAT"}
DATE_TYPES = {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP"}
ACCENT = "#2563eb"


@dataclass
class ChartChoice:
    kind: str  # "metric", "bar", "dot", "line", "scatter", "table", "none"
    reason: str
    figure: go.Figure | None = None
    plotted: list[tuple[str, object]] = field(default_factory=list)  # (label, exact value) per drawn mark


def to_dataframe(columns: list[str], rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


COLUMN_LABELS: dict[str, str] = {}  # optional display names from the dataset config (set by the app)
_AMBIGUOUS = {"name", "key", "date", "status", "comment", "type", "size", "id"}


def humanize(col: str) -> str:
    """Column name -> axis title: TOTAL_SALES -> 'Total sales', O_ORDERPRIORITY -> 'Order priority'."""
    if col.upper() in COLUMN_LABELS:
        return COLUMN_LABELS[col.upper()]
    name = col.lower()
    if len(name) > 2 and name[1] == "_" and name[0].isalpha():  # TPC-H style prefixes: o_, c_, l_ ...
        name = name[2:]
    name = name.replace("_", " ").replace("orderpriority", "order priority").replace("mktsegment", "market segment")
    if name in _AMBIGUOUS:  # "name" alone says nothing; keep the real column name instead
        return col.upper()
    return name[:1].upper() + name[1:]


def fmt_compact(v: float, decimals: int = 2) -> str:
    """45501158695.79 -> '45.50B'. Display only; never used for stored or exported values."""
    a = abs(v)
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= size:
            return f"{v / size:,.{decimals}f}{suffix}"
    return f"{v:,.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{v:,.0f}"


def _to_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return None if math.isnan(f) else f
    return None


def _looks_like_year(values: list, name: str) -> bool:
    vals = [v for v in values if v is not None]
    if not vals or not all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return False
    return "YEAR" in name.upper() and all(1800 <= v <= 2200 for v in vals)


def _ticks(lo: float, hi: float, n: int = 5, integer: bool = False) -> tuple[list[float], list[str]]:
    """Evenly spaced ticks with compact labels precise enough to stay distinct (whole numbers for counts)."""
    if hi <= lo:
        return [lo], [fmt_compact(lo)]
    if integer and hi < 1000:
        step = max(1, math.ceil((hi - lo) / (n - 1)))
        vals = list(range(math.ceil(lo), math.floor(hi) + 1, step))
        return vals, [f"{v:,}" for v in vals]
    step = (hi - lo) / (n - 1)
    vals = [lo + i * step for i in range(n)]
    scale = next((s for s in (1e12, 1e9, 1e6, 1e3) if max(abs(lo), abs(hi)) >= s), 1)
    decimals = max(0, min(4, math.ceil(-math.log10(step / scale)) if step < scale else 0))
    return vals, [fmt_compact(v, decimals) for v in vals]


def choose_chart(columns: list[str], types: list[str], rows: list[tuple]) -> ChartChoice:
    if not rows:
        return ChartChoice("none", "No rows were returned, so there is nothing to chart.")
    df = to_dataframe(columns, rows)
    raw = {c: [r[i] for r in rows] for i, c in enumerate(columns)}  # exact values; pandas would turn None into NaN
    numeric = [c for c, t in zip(columns, types) if t in NUMERIC_TYPES
               and all(_to_float(v) is not None or v is None for v in raw[c])]
    ids = [c for c in numeric if is_identifier_column(c)]
    dates = [c for c, t in zip(columns, types) if t in DATE_TYPES]
    years = [c for c in numeric if _looks_like_year(raw[c], c)]
    measures = [c for c in numeric if c not in years and c not in ids]
    labels = [c for c in columns if c not in numeric and c not in dates]

    if len(df) == 1 and measures and not labels and not dates:
        return ChartChoice("metric", "A single row of numbers is shown as key metrics.")

    time_col = (dates or years or [None])[0]
    if time_col and measures and len(df) >= 2:
        m = measures[0]
        plot = df.assign(**{m: [_to_float(v) for v in raw[m]]}).sort_values(time_col)
        color = labels[0] if labels and plot[labels[0]].nunique() <= 10 else None
        fig = px.line(plot, x=time_col, y=m, color=color, markers=True)
        ys = [v for v in plot[m] if v is not None]
        if ys:
            tv, tt = _ticks(0 if min(ys) >= 0 else min(ys), max(ys))
            fig.update_yaxes(tickvals=tv, ticktext=tt, rangemode="tozero")
        _style(fig, f"{humanize(m)} over {humanize(time_col).lower()}", humanize(time_col), humanize(m), height=360)
        return ChartChoice("line", f"{humanize(m)} over {humanize(time_col).lower()} (time series).", fig,
                           [(str(t), v) for t, v in zip(raw[time_col], raw[m])])

    if labels and measures and len(df) >= 2:
        return _category_chart(raw, labels[0], measures[-1], ids[0] if ids else None)

    if len(measures) >= 2 and len(df) >= 3 and not labels:
        plot = df.assign(**{c: [_to_float(v) for v in raw[c]] for c in measures[:2]})
        fig = px.scatter(plot, x=measures[0], y=measures[1])
        _style(fig, f"{humanize(measures[1])} vs {humanize(measures[0]).lower()}", humanize(measures[0]),
               humanize(measures[1]), height=380)
        return ChartChoice("scatter", f"Relationship between {measures[0]} and {measures[1]}.", fig,
                           [(str(a), b) for a, b in zip(raw[measures[0]], raw[measures[1]])])

    return ChartChoice("table", "This result is best read as a table.")


def _category_chart(raw: dict[str, list], label: str, measure: str, id_col: str | None) -> ChartChoice:
    n_cat = len(set(raw[label]))
    if n_cat > MAX_BAR_CATEGORIES:
        return ChartChoice("table", f"{n_cat} categories is too many for a readable chart.")
    labels = ["(NULL)" if v is None else str(v) for v in raw[label]]
    # Duplicate display labels would be merged into one bar by Plotly; disambiguate from the row itself.
    if len(set(labels)) < len(labels):
        if id_col is not None:
            labels = [f"{lab} ({k})" for lab, k in zip(labels, raw[id_col])]
        if len(set(labels)) < len(labels):
            labels = [f"{lab} (row {i + 1})" for i, lab in enumerate(labels)]
    exact = list(raw[measure])
    kept = [(lab, v) for lab, v in zip(labels, exact) if _to_float(v) is not None]
    dropped = len(labels) - len(kept)
    if len(kept) < 2:
        return ChartChoice("table", "Fewer than two non-NULL values; a chart would not add anything.")
    ys = [_to_float(v) for _, v in kept]
    names = [lab for lab, _ in kept]
    hover = [fmt_number(v) for _, v in kept]  # exact values, full precision
    lo, hi = min(ys), max(ys)
    integer = all(isinstance(v, int) and not isinstance(v, bool) for _, v in kept)
    narrow = lo > 0 and (hi - lo) / hi < NARROW_SPREAD
    mlabel, llabel = humanize(measure), humanize(label)
    title = f"{mlabel} by {llabel.lower()}"
    height = max(220, min(560, 70 + 42 * len(kept)))

    if narrow:
        pad = max((hi - lo) * 0.25 or hi * 0.01, 0.5 if integer else 0)
        x0, x1 = lo - pad, hi + pad
        decimals = 2 if (hi - lo) / hi < 0.02 else 1
        fig = go.Figure(go.Scatter(
            x=ys, y=names, mode="markers+text", marker=dict(size=13, color=ACCENT),
            text=[f"{int(v):,}" if integer else fmt_compact(v, decimals) for v in ys], textposition="middle right",
            customdata=hover, hovertemplate="%{y}<br>" + mlabel + ": %{customdata}<extra></extra>"))
        for y, name in zip(ys, names):  # thin guide from the axis start so position reads as length
            fig.add_shape(type="line", x0=x0, x1=y, y0=name, y1=name, line=dict(color="rgba(37,99,235,0.25)", width=2))
        tv, tt = _ticks(x0, x1, integer=integer)
        fig.update_xaxes(range=[x0, x1 + (x1 - x0) * 0.12], tickvals=tv, ticktext=tt)
        spread = (hi - lo) / hi * 100
        start = f"{x0:,.1f}" if integer else fmt_compact(x0, decimals)
        reason = (f"Dot plot: the axis starts at {start}, not zero, because all values lie within "
                  f"{spread:.2f}% of each other; zero-based bars would look identical. Hover for exact values.")
        kind = "dot"
    else:
        fig = go.Figure(go.Bar(
            x=ys, y=names, orientation="h", marker_color=ACCENT,
            text=[fmt_compact(v, 2) if abs(v) >= 1000 else fmt_number(v) for v in ys], textposition="outside",
            customdata=hover, hovertemplate="%{y}<br>" + mlabel + ": %{customdata}<extra></extra>", cliponaxis=False))
        tv, tt = _ticks(min(0.0, lo), max(0.0, hi), integer=integer)
        fig.update_xaxes(range=[min(0.0, lo) * 1.15, max(0.0, hi) * 1.15], tickvals=tv, ticktext=tt)
        reason = f"{mlabel} by {llabel.lower()}; bars start at zero."
        kind = "bar"
    fig.update_yaxes(categoryorder="array", categoryarray=names[::-1])  # first query row at the top
    _style(fig, title, mlabel, llabel, height=height)
    if dropped:
        reason += f" {dropped} row(s) with a NULL {measure} are not plotted (see the table)."
    return ChartChoice(kind, reason, fig, kept)


def _style(fig: go.Figure, title: str, x: str, y: str, height: int) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, xanchor="left", font=dict(size=15)),
        margin=dict(l=10, r=30, t=44, b=10), height=height, xaxis_title=x, yaxis_title=y,
        font=dict(family="Inter, system-ui, sans-serif", size=13, color="#64748b"), hovermode="closest",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=fig.layout.showlegend is not False
        and len(fig.data) > 1,
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)", automargin=True, zeroline=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", automargin=True)
    return fig
