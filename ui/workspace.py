"""Page 2 — Query workspace: data sidebar, conversation of answer cards, visualization studio."""

from __future__ import annotations

import datetime as dt
import html
import re

import streamlit as st

from config.settings import Settings
from src.orchestrator import STAGES, Orchestrator
from src.services.schema_service import SchemaSnapshot
from src.services.visualization_service import humanize
from src.utils.errors import DataMindError
from ui import answer, nav, state, studio, theme
from ui.status import chips
from ui.theme import svg_img

STAGE_LABELS = {"interpret": "Interpreting with Gemma", "validate": "Validating the plan and SQL",
                "query": "Querying Snowflake", "verify": "Verifying the result"}
_GROUPS = [("Rankings", re.compile(r"\b(top|highest|largest|most|biggest|lowest|which)\b", re.I)),
           ("Trends", re.compile(r"\b(change|changed|over time|monthly|yearly|since|trend|during)\b", re.I)),
           ("Comparisons", re.compile(r"\b(each|by|per|share|average|compare|vs)\b", re.I))]
_TABLE_ICON = svg_img('<svg class="tb" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.8" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/>'
               '<path d="M3 10h18M3 15h18M9 10v10"/></svg>', "#6B7A8D")
_CHEV = svg_img('<svg class="chev" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M3 1.5 7 5 3 8.5" fill="none" '
         'stroke="currentColor" stroke-width="1.5"/></svg>', "#6B7A8D")
_BULB = svg_img('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#FBBF24" stroke-width="2" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6M10 22h4"/>'
         '<path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.3h6c0-1 .4-1.8 1-2.3A7 7 0 0 0 12 2z"/></svg>')


# ---------------------------------------------------------------- header
def _header(settings: Settings, sf: dict, gm: dict) -> None:
    c1, c2, c3, c4 = st.columns([1.5, 2.1, 3.6, .45], vertical_alignment="center", gap="small")
    c1.html(theme.brand(tagline=True, size=36))
    datasets = state.available_datasets()
    paths = list(datasets)
    c2.selectbox("Dataset", paths, key="dataset_path",
                 format_func=lambda p: datasets[p], label_visibility="collapsed",
                 index=paths.index(state.default_dataset_path()), on_change=state.clear_conversation,
                 help=f"{settings.dataset.database}.{settings.dataset.schema} · switching dataset starts a new "
                      "conversation")
    c3.html(chips(sf, gm, settings.gemma.model))
    with c4.container(key="settings_pop"):
        with st.popover("", icon=":material/tune:", help="Settings"):
            st.markdown("**Connections**")
            st.caption(f"Snowflake checked {sf.get('checked_at', '—')} · Gemma checked {gm.get('checked_at', '—')}")
            if st.button("Re-check connections", icon=":material/sync:", width="stretch"):
                state.recheck()
                st.rerun()
            if st.button("Clear conversation", icon=":material/delete_sweep:", width="stretch",
                         disabled=not state.turns()):
                state.clear_conversation()
                st.rerun()
            st.toggle("Show visualization panel", value=st.session_state.viz_mode != "hidden", key="studio_on",
                      on_change=lambda: st.session_state.update(
                          viz_mode="panel" if st.session_state.studio_on else "hidden"))
            if st.button("Back to home", icon=":material/home:", width="stretch", key="go_home"):
                nav.go("home")
            if settings.dataset.license:
                with st.expander("Dataset source & licence"):
                    st.caption(settings.dataset.license)
            with st.expander("Configuration (redacted)"):
                st.json(settings.describe())


# ---------------------------------------------------------------- sidebar
_TYPE = [("num", "123", {"NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT", "SMALLINT", "FLOAT", "DOUBLE",
                         "REAL", "FIXED"}),
         ("txt", "Aa", {"TEXT", "VARCHAR", "CHAR", "STRING", "CHARACTER"}),
         ("dt", "D", {"DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP", "DATETIME", "TIME"}),
         ("bool", "T/F", {"BOOLEAN"})]


def _type_badge(data_type: str) -> str:
    t = data_type.upper()
    for css, label, names in _TYPE:
        if t in names:
            return f'<span class="dm-type {css}" title="{html.escape(data_type)}">{label}</span>'
    return f'<span class="dm-type oth" title="{html.escape(data_type)}">{{}}</span>'


def _hl(text: str, q: str) -> str:
    t = html.escape(text)
    if not q:
        return t
    return re.sub(re.escape(html.escape(q)), lambda m: f'<mark class="dm-hit">{m.group(0)}</mark>', t, flags=re.I)


def _schema_tree(snapshot: SchemaSnapshot, query: str, expand_all: bool) -> None:
    tables = list(snapshot.tables.values())
    if not tables:
        st.caption("No allowlisted tables are visible to this role.")
        return
    ql = query.lower()
    out = []
    for t in tables:
        name_hit = ql in t.name.lower()
        cols = [c for c in t.columns.values() if not ql or name_hit or ql in c.name.lower()]
        if ql and not cols:
            continue
        rc = f"{t.row_count:,}" if t.row_count is not None else ""
        items = "".join(f'<li>{_type_badge(c.data_type)}<span class="n">{_hl(c.name, query)}</span>'
                        f'<small>{html.escape(c.data_type.upper())}</small></li>' for c in cols)
        is_open = " open" if expand_all or ql else ""
        tip = f' title="{html.escape(t.comment)}"' if t.comment else ""
        out.append(f'<details{is_open}><summary{tip}>{_CHEV}{_TABLE_ICON}<span>{_hl(t.name, query)}</span>'
                   f'<span class="cnt">{rc}</span></summary><ul class="dm-cols">{items}</ul></details>')
    if not out:
        st.caption(f"No table or column matches “{query}”.")
        return
    st.html(f'<div class="dm-tree" role="tree">{"".join(out)}</div>')
    total_cols = sum(len(t.columns) for t in snapshot.tables.values())
    st.caption(f"{len(snapshot.tables)} tables · {total_cols} columns · row counts from INFORMATION_SCHEMA")


def _sidebar(settings: Settings, schema_svc, sf: dict, ready: bool) -> SchemaSnapshot | None:
    ds = settings.dataset
    snapshot = None
    with st.sidebar:
        st.html('<div class="dm-h" style="font-size:1.3rem;margin:.2rem 0 .1rem">Your Data</div>')
        query = st.text_input("Search tables or columns", placeholder="Search tables or columns…",
                              label_visibility="collapsed", key="schema_q", icon=":material/search:").strip()
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        expand_all = c1.toggle("Expand all", key="schema_expand")
        refresh = c2.button("", icon=":material/refresh:", help="Refresh schema from INFORMATION_SCHEMA",
                            disabled=not sf.get("ok"), key="schema_refresh")
        if not sf.get("ok"):
            st.caption("Schema is unavailable until Snowflake connects.")
        else:
            try:
                with st.spinner("Discovering schema…"):
                    snapshot = schema_svc.get(force_refresh=refresh)
            except DataMindError as exc:
                st.error(exc.user_message, icon=":material/error:")
        if snapshot is not None:
            _schema_tree(snapshot, query, expand_all)

        if ds.example_questions:
            st.html(f'<div class="dm-h" style="font-size:1rem;display:flex;gap:.5rem;align-items:center;'
                    f'margin:1.1rem 0 .2rem;padding-top:1rem;border-top:1px solid var(--dm-line)">{_BULB}'
                    'Example Questions</div>')
            for j, q in enumerate(ds.example_questions[:6]):
                if st.button(q, key=f"sq-{j}", width="stretch", disabled=not ready):
                    state.queue(q)
                    st.rerun()

        if state.turns():
            st.html('<div class="dm-eyebrow" style="margin-top:1.1rem">Query history · this session</div>')
            for j, t in enumerate(state.turns()):
                st.caption(f"**Q{j + 1}.** {t.resp.question}  \n{answer.status_of(t.resp)[2]} · "
                           f"{t.asked_at.strftime('%H:%M')}")
            st.caption("History lasts for this browser session only; it is not saved on the server.")
    return snapshot


# ---------------------------------------------------------------- conversation
def progress_html(current: str | None, done: set[str]) -> str:
    cells = []
    for n, s in enumerate(STAGES, 1):
        css = "done" if s in done else ("active" if s == current else "")
        mark = "✓" if s in done else f"0{n}"
        cells.append(f'<div class="dm-step {css}"><i>{"" if css == "active" else mark}</i>{STAGE_LABELS[s]}</div>')
    return f'<div class="dm-steps" role="status" aria-live="polite">{"".join(cells)}</div>'


def _run(question: str, orch: Orchestrator) -> None:
    asked = dt.datetime.now()
    answer.question_bubble(len(state.turns()), question, asked)
    with st.container(key="card-pending"):
        box = st.empty()
        done: set[str] = set()
        seen: list[str] = []
        box.html(progress_html(None, done))

        def on_stage(name: str) -> None:  # called by the orchestrator as each stage really starts
            if seen:
                done.add(seen[-1])
            seen.append(name)
            box.html(progress_html(name, done))

        resp = orch.ask(question, st.session_state.history, progress=on_stage)
    state.record(resp, asked)
    st.rerun()


def _examples(snapshot: SchemaSnapshot | None, settings: Settings) -> list[tuple[str, str]]:
    """One example per intent group, from the dataset's curated questions plus a schema-derived data-quality check."""
    out: list[tuple[str, str]] = []
    pool = list(settings.dataset.example_questions)
    for name, rx in _GROUPS:
        q = next((q for q in pool if rx.search(q)), None)
        if q:
            pool.remove(q)
            out.append((name, q))
    if snapshot is not None:
        nullable = [(t.name, c.name) for t in snapshot.tables.values() for c in t.columns.values()
                    if c.nullable and not c.name.upper().endswith("KEY")]
        if nullable:
            t, c = nullable[0]
            out.append(("Data quality", f"How many {t.lower()} rows have no {humanize(c).lower()} value?"))
        elif settings.dataset.categorical_columns:
            col = settings.dataset.categorical_columns[0].split(".", 1)[1]
            out.append(("Data quality", f"How many distinct {humanize(col).lower()} values are there?"))
    return out[:4]


def _empty_state(snapshot: SchemaSnapshot | None, settings: Settings, ready: bool) -> None:
    st.html("""<div style="text-align:center;margin:3rem 0 1.6rem">
      <div class="dm-eyebrow">ASK · QUERY · VERIFY</div>
      <h2 style="font-family:var(--dm-serif);font-size:2.4rem;letter-spacing:-.02em;font-weight:500;margin:.3rem 0 .5rem;
      padding:0">What would you like to discover?</h2>
      <p class="dm-muted" style="font-size:1rem;max-width:34rem;margin:0 auto">Ask a question about your Snowflake data.
      Explore the answer, inspect the SQL, and visualize the evidence.</p></div>""")
    ex = _examples(snapshot, settings)
    cols = st.columns(2, gap="small")
    for j, (group, q) in enumerate(ex):
        with cols[j % 2]:
            st.html(f'<div class="dm-eyebrow">{group}</div>')
            if st.button(q, key=f"ex-{j}", width="stretch", disabled=not ready):
                state.queue(q)
                st.rerun()
    st.caption("Each question runs a real query in Snowflake (it uses warehouse credits).")


def _studio_panel(wide: bool) -> None:
    with st.container(key="studio-panel"):
        h1, h2 = st.columns([6, 1], vertical_alignment="top", gap="small")
        h1.html('<div class="dm-h">Visualize Your Data</div>'
                '<div class="dm-muted" style="margin-top:.25rem">Choose a chart type for this result</div>')
        if h2.button("", key="studio_hide", icon=":material/close:", help="Close visualization panel"):
            st.session_state.viz_mode = "hidden"
            st.rerun()
        i = studio.picker("panel")
        if i is None:
            st.html('<div class="dm-problem" style="border-color:var(--dm-line);background:none">'
                    '<b style="color:var(--dm-ink)">No result to chart yet.</b><br>Ask a question that returns rows. '
                    'Charts are built only from the rows Snowflake returns.</div>')
            return
        studio.kind_cards(i, "panel", per_row=6 if wide else 3)
        spec = studio._spec(i)
        with st.container(key="chartcard"):
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            c1.html(f'<div class="dm-h" style="font-size:1.02rem">{studio.TILE_LABELS[spec.kind]}</div>')
            if c2.button("", key="studio_size", icon=":material/close_fullscreen:" if wide else ":material/open_in_full:",
                         help="Narrow the panel" if wide else "Widen the panel"):
                st.session_state.viz_mode = "panel" if wide else "wide"
                st.rerun()
            # The chart is drawn into this slot after the settings below have run, so it reflects them
            # in the same script run while still appearing above them.
            chart_box = st.container()
        if spec.kind != "table":
            st.html('<div class="dm-h" style="font-size:.95rem;margin-top:.4rem">Chart settings</div>')
            studio.controls(i, "panel", compact=not wide)
        with chart_box:
            studio.chart(i, "panel", height=440 if wide else 300)
        b1, b2 = st.columns(2, gap="small")
        data = studio.chart_html(i, state.turns()[i].resp.question)
        b1.download_button("Download Chart", data or b"", f"datamind_q{i + 1}_chart.html", "text/html",
                           key="dl-chart", icon=":material/download:", width="stretch", disabled=data is None,
                           help="Interactive HTML of this chart (the camera icon on the chart saves a PNG)")
        if b2.button("Expand View", key="open_analysis", icon=":material/open_in_new:", width="stretch"):
            st.session_state.viz_turn = i
            nav.go("analysis")


def render() -> None:
    state.init()
    try:
        settings, _sf, schema_svc, orch = state.services()
    except DataMindError as exc:
        st.error(f"Configuration error: {exc.user_message}")
        return
    with st.spinner("Checking Snowflake and Gemma…"):
        sf_stat, gm_stat = state.run_health_checks()
    ready = bool(sf_stat.get("ok")) and bool(gm_stat.get("verified"))

    _header(settings, sf_stat, gm_stat)
    if not sf_stat.get("ok"):
        st.error(f"**Snowflake:** {sf_stat.get('error')}  \nDataMind answers only from live Snowflake queries, so "
                 "questions are disabled until the connection works. See README → *Snowflake setup*.",
                 icon=":material/cloud_off:")
    if not gm_stat.get("verified"):
        st.error(f"**Gemma:** {gm_stat.get('error', 'inference check failed')}  \nQuestions are disabled because "
                 "Gemma interprets every question. See README → *Gemma setup*.", icon=":material/smart_toy:")
    mode = st.session_state.viz_mode
    show_studio = mode != "hidden" and bool(state.turns())
    if show_studio:
        center, right = st.columns([1.15, 1] if mode == "wide" else [1.75, 1], gap="medium")
    else:
        center, right = st.container(), None

    if right is not None:
        with right:  # filled first: its chart settings must be saved before answer cards draw their charts
            _studio_panel(wide=mode == "wide")

    with center:
        convo, composer = st.container(), st.container()
        with composer:
            placeholder = ("Ask a follow-up question… (e.g. “show only the top 3”)" if state.turns()
                           else "Ask anything about your data…")
            typed = st.chat_input(placeholder, disabled=not ready, max_chars=500, key="composer")
            if state.turns():
                n = state.context_turns()
                st.caption(f"Follow-ups send your last {n} question{'s' if n != 1 else ''} and Gemma's "
                           f"plan{'s' if n != 1 else ''} as context. Result rows are not sent.")
        question = typed or st.session_state.pop("pending", None)
        st.session_state.pending = None
        empty = not state.turns() and not question
        with convo:
            for i, turn in enumerate(state.turns()):
                answer.render(i, turn, settings, orch, latest=i == len(state.turns()) - 1, ready=ready)
            if question and ready:
                _run(question, orch)
            if mode == "hidden" and studio.chart_turns():
                if st.button("Show visualization panel", icon=":material/insert_chart:", key="studio_show"):
                    st.session_state.viz_mode = "panel"
                    st.rerun()

    # The sidebar is drawn after the conversation: schema discovery can take seconds on a cold cache and the
    # answers should not wait for it. Only the empty state needs the snapshot (for its data-quality example).
    snapshot = _sidebar(settings, schema_svc, sf_stat, ready)
    if empty:
        with convo:
            _empty_state(snapshot, settings, ready)
