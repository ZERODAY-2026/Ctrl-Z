"""Visualization studio: chart-type tiles, chart settings and the chart itself.

Charts are built by src/services/chart_studio.py from the selected turn's returned rows only.
The same spec (st.session_state.viz[turn]) drives the answer card, the side panel and the Analysis page.
"""

from __future__ import annotations

import html
from decimal import Decimal

import pandas as pd
import streamlit as st

from src.services import chart_studio as cs
from src.services.grounding_service import is_identifier_column
from src.services.visualization_service import humanize, to_dataframe
from ui import state
from ui.state import partial
from ui.theme import svg_img

TILES = ("bar", "line", "donut", "area", "scatter", "table")
TILE_LABELS = {**{k: v.title() for k, v in cs.KIND_LABELS.items()}, "table": "Data Table"}
TILE_HINTS = {**cs.KIND_HINTS, "table": "Every returned row, as fetched"}
_ICON = {
    "bar": '<rect x="5" y="13" width="4.5" height="13" rx="1.5" fill="#2DD4BF"/><rect x="13.75" y="5" width="4.5" '
           'height="21" rx="1.5" fill="#2DD4BF"/><rect x="22.5" y="10" width="4.5" height="16" rx="1.5" fill="#2DD4BF"/>',
    "line": '<polyline points="4,22 11,14 17,18 28,7" fill="none" stroke="#A78BFA" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="14" r="2.2" fill="#A78BFA"/>'
            '<circle cx="17" cy="18" r="2.2" fill="#A78BFA"/><circle cx="28" cy="7" r="2.2" fill="#A78BFA"/>',
    "donut": '<circle cx="16" cy="16" r="11" fill="#7C6CF0"/><path d="M16 5a11 11 0 0 1 11 11H16z" fill="#C4B5FD"/>',
    "area": '<path d="M4 26 L4 19 L11 12 L17 17 L22 9 L28 14 L28 26 Z" fill="#7C6CF0"/>',
    "scatter": "".join(f'<circle cx="{x}" cy="{y}" r="2.2" fill="{c}"/>' for x, y, c in [
        (6, 24, "#A78BFA"), (11, 18, "#7C6CF0"), (15, 22, "#A78BFA"), (17, 12, "#7C6CF0"), (22, 15, "#A78BFA"),
        (24, 8, "#7C6CF0"), (27, 11, "#A78BFA"), (8, 13, "#7C6CF0")]),
    "table": '<rect x="4" y="6" width="24" height="20" rx="2.5" fill="none" stroke="#98A6B8" stroke-width="1.8"/>'
             '<path d="M4 12h24M4 19h24M12 12v14M20 12v14" stroke="#98A6B8" stroke-width="1.5"/>',
}


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy for on-screen tables: exact Decimals become floats so they can be formatted. CSV export keeps the
    exact values from the original frame."""
    out = df.copy()
    for c in out.columns:
        if out[c].map(lambda v: isinstance(v, Decimal)).any():
            out[c] = out[c].map(lambda v: float(v) if isinstance(v, Decimal) else v)
    return out


def number_formats(df: pd.DataFrame) -> dict:
    """Thousands separators for measure columns; identifiers and years stay unformatted."""
    cfg = {}
    for c in df.columns:
        vals = [v for v in df[c] if v is not None]
        if vals and all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in vals)                 and not is_identifier_column(c) and "YEAR" not in c.upper():
            cfg[c] = st.column_config.NumberColumn(c, format="localized")
    return cfg


def chart_turns() -> list[int]:
    return [i for i, t in enumerate(state.turns()) if t.resp.evidence is not None and t.resp.evidence.rows]


def _profile(i: int) -> cs.ResultProfile:
    ev = state.turns()[i].resp.evidence
    return cs.profile(ev.result_columns, ev.result_types, ev.rows)


def _spec(i: int) -> cs.ChartSpec:
    p = _profile(i)
    saved = st.session_state.viz.get(i)
    if saved:
        spec = cs.ChartSpec(**saved)
        return spec if spec.kind == "table" else cs.coerce(spec, p)
    return cs.default_spec(p) or cs.ChartSpec("table")


def _save(i: int, spec: cs.ChartSpec) -> None:
    st.session_state.viz[i] = spec.as_dict()


def kind_cards(i: int, where: str, per_row: int = 3) -> None:
    spec = _spec(i)
    for start in range(0, len(TILES), per_row):
        cols = st.columns(per_row, gap="small")
        for col, k in zip(cols, TILES[start:start + per_row]):
            on = k == spec.kind
            with col.container(key=f"vk-{where}-{k}-{'selected' if on else 'idle'}"):
                st.html(svg_img(f'<svg width="30" height="30" viewBox="0 0 32 32">{_ICON[k]}</svg>'))
                if st.button(TILE_LABELS[k], key=f"vkb-{where}-{k}", width="stretch", help=TILE_HINTS[k]):
                    _save(i, cs.ChartSpec(k, spec.x, spec.y, spec.series, spec.sort, spec.top_n, spec.sort_by,
                                          spec.labels))
                    st.rerun()


def controls(i: int, where: str, compact: bool) -> cs.ChartSpec:
    """Chart settings: axes, sort by / order, series, top N, value labels. Saves and returns the spec."""
    spec = _spec(i)
    if spec.kind == "table":
        return spec
    ev = state.turns()[i].resp.evidence
    p = _profile(i)
    xs, ys = cs.x_options(spec.kind, p), cs.y_options(spec.kind, p)
    k = f"{where}-{i}-{spec.kind}"
    r1 = st.columns(2 if compact else 4, gap="small")
    x = r1[0].selectbox("X-axis" if spec.kind != "donut" else "Slices", xs, index=xs.index(spec.x)
                        if spec.x in xs else 0, key=f"x-{k}") if xs else None
    y_choices = [c for c in ys if spec.kind != "scatter" or c != x]
    y = r1[1].selectbox("Y-axis" if spec.kind != "donut" else "Size", y_choices,
                        index=y_choices.index(spec.y) if spec.y in y_choices else 0, key=f"y-{k}") if y_choices else None
    r2 = st.columns(2, gap="small") if compact else r1[2:]
    time_axis = spec.kind in ("line", "area")
    sort_opts = ["(query order)", *dict.fromkeys(c for c in (y, x) if c)]
    current = "(query order)" if spec.sort == "query" else (spec.sort_by or y)
    sort_by = r2[0].selectbox("Sort by", sort_opts, index=sort_opts.index(current) if current in sort_opts else 0,
                              key=f"o-{k}", disabled=time_axis,
                              help="Line and area charts always follow the time axis." if time_axis else
                              "Rows keep Snowflake's order unless you pick a column.")
    order = r2[1].selectbox("Order", ["Descending", "Ascending"], index=1 if spec.sort == "asc" else 0,
                            key=f"d-{k}", disabled=time_axis or sort_by == "(query order)")
    r3 = st.columns(2, gap="small")
    series_opts = [None, *cs.series_options(spec.kind, p, x)]
    series = r3[0].selectbox("Series", series_opts, index=series_opts.index(spec.series)
                             if spec.series in series_opts else 0, key=f"s-{k}",
                             format_func=lambda c: "None" if c is None else c, disabled=len(series_opts) == 1,
                             help="Split rows into coloured series by a text column (no aggregation).")
    top = r3[1].number_input("Top N", min_value=0, max_value=len(ev.rows), value=spec.top_n or 0, step=1,
                             key=f"n-{k}", help="0 = all returned rows. Applied after sorting; never re-queries.")
    labels = st.toggle("Show value labels", value=spec.labels, key=f"l-{k}")
    if time_axis or sort_by == "(query order)":
        sort, by = "query", None
    else:
        sort, by = ("asc" if order == "Ascending" else "desc"), sort_by
    new = cs.ChartSpec(spec.kind, x, y, series, sort, int(top) or None, by, labels)
    if new != spec:
        _save(i, new)
    return new


def title(spec: cs.ChartSpec) -> str:
    if spec.kind == "table" or not spec.y:
        return "Returned rows"
    if spec.kind == "scatter":
        return f"{humanize(spec.y)} vs {humanize(spec.x).lower()}"
    return f"{humanize(spec.y)} by {humanize(spec.x).lower()}" if spec.x else humanize(spec.y)


def build(i: int, height: int) -> cs.StudioChart:
    turn = state.turns()[i]
    ev = turn.resp.evidence
    return cs.build(_spec(i), ev.result_columns, ev.result_types, ev.rows, partial=partial(turn.resp), height=height)


def chart(i: int, where: str, height: int, quiet: bool = False, modebar: bool = True) -> None:
    """Render the turn's chart. quiet=True (answer cards) skips anything that is not a fitting chart."""
    spec = _spec(i)
    ev = state.turns()[i].resp.evidence
    if spec.kind == "table":
        if not quiet:
            df = to_dataframe(ev.result_columns, ev.rows)
            st.dataframe(display_frame(df), hide_index=True, width="stretch", column_config=number_formats(df),
                         height=min(38 + 35 * len(ev.rows), height))
            st.caption(f"{len(ev.rows):,} returned row(s), exactly as fetched")
        return
    result = build(i, height)
    if result.problem:
        if quiet:
            return
        alt = (f"<br><br>Try a <b>{cs.KIND_LABELS[result.suggestion].lower()}</b> instead."
               if result.suggestion else "")
        st.html(f'<div class="dm-problem" role="status"><b>A {cs.KIND_LABELS[spec.kind].lower()} doesn\'t fit this '
                f'result.</b><br>{html.escape(result.problem)}{alt}</div>')
        if result.suggestion and st.button(f"Switch to {cs.KIND_LABELS[result.suggestion].lower()}",
                                           key=f"alt-{where}-{i}", icon=":material/swap_horiz:"):
            _save(i, cs.ChartSpec(result.suggestion, spec.x, spec.y, None, spec.sort, spec.top_n, spec.sort_by,
                                  spec.labels))
            st.rerun()
        return
    st.html(f'<div class="dm-ctitle">{html.escape(title(result.spec))}</div>')
    # theme=None: Streamlit's theme rewrites "#000001".."#000010" inside figure JSON (see tests/test_charts.py).
    st.plotly_chart(result.figure, width="stretch", theme=None, key=f"plot-{where}-{i}",
                    config={"displaylogo": False, "displayModeBar": "hover" if modebar else False,
                            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                            "toImageButtonOptions": {"format": "png", "filename": f"datamind_q{i + 1}_{spec.kind}",
                                                     "scale": 2}})
    if quiet:
        st.caption(result.caption)
    else:
        st.caption(result.caption)
        for n in result.notes:
            st.caption(f"ℹ️ {n}")


def chart_html(i: int, question: str) -> bytes | None:
    """Standalone interactive HTML of the current chart (Plotly loaded from its CDN), or None."""
    if _spec(i).kind == "table":
        return None
    result = build(i, 520)
    if result.figure is None:
        return None
    fig = result.figure
    fig.update_layout(paper_bgcolor="#111821", plot_bgcolor="#111821",
                      title=dict(text=html.escape(question), x=0.01, font=dict(color="#F4F7FB", size=16)),
                      margin=dict(t=60, l=20, r=20, b=20))
    return fig.to_html(include_plotlyjs="cdn", full_html=True).encode()


def picker(where: str) -> int | None:
    """Select which answered result the studio shows. Returns the turn index or None."""
    options = chart_turns()
    if not options:
        return None
    current = st.session_state.viz_turn if st.session_state.viz_turn in options else options[-1]
    chosen = st.selectbox("Result", options, index=options.index(current),
                          key=f"pick-{where}-{current}",  # re-keyed so outside changes (Visualize) win
                          format_func=lambda j: f"Q{j + 1} · {state.turns()[j].resp.question[:70]}",
                          label_visibility="collapsed")
    if chosen != st.session_state.viz_turn:
        st.session_state.viz_turn = chosen
    return chosen
