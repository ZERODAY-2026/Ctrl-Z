"""Page 3 — Expanded analysis: one result, full-width chart, configuration, insights and rows.

It reuses the result already in session state; it never re-queries Snowflake. After a browser refresh
the session is gone, so it shows an empty state instead of anything stale or unrelated.
"""

from __future__ import annotations

import html

import streamlit as st

from src.services import chart_studio as cs
from src.services.visualization_service import to_dataframe
from src.utils.errors import DataMindError
from ui import answer, nav, state, studio, theme


def render() -> None:
    state.init()
    st.html("<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl']{display:none!important}</style>")
    top1, top2, top3 = st.columns([1.2, 3, 2.2], vertical_alignment="center")
    if top1.button("Back to workspace", icon=":material/arrow_back:", key="back_ws"):
        nav.go("workspace")
    top2.html(theme.brand(tagline=True))
    with top3:
        i = studio.picker("page")

    if i is None:
        st.html("""<div style="text-align:center;margin:5rem auto;max-width:30rem">
          <div class="dm-eyebrow">NO RESULT SELECTED</div>
          <h2 style="font-size:1.7rem;letter-spacing:-.02em;margin:.3rem 0 .5rem;padding:0">Nothing to analyse yet</h2>
          <p class="dm-muted">Results live in your workspace session. A browser refresh clears them, so no result can be
          shown here. Ask a question in the workspace, then choose <b>Open full analysis</b>.</p></div>""")
        _, mid, _ = st.columns([1, 1, 1])
        with mid:
            if st.button("Go to workspace", key="cta_empty", width="stretch"):
                nav.go("workspace")
        return

    try:
        settings = state.services()[0]
    except DataMindError as exc:
        st.error(exc.user_message)
        return
    turn = state.turns()[i]
    resp, ev = turn.resp, turn.resp.evidence
    css, ic, label = answer.status_of(resp)
    qid = f'<span class="dm-chip"><small>Query ID</small><span class="dm-mono" style="font-size:.72rem">' \
          f'{html.escape(ev.query_id or "not provided")}</span></span>'
    st.html(f"""<div style="margin:1.2rem 0 .6rem">
      <h1 style="font-family:var(--dm-serif);font-size:clamp(1.7rem,3.2vw,2.5rem);letter-spacing:-.02em;font-weight:500;
      margin:0 0 .7rem;padding:0">
      {html.escape(resp.question)}</h1>
      <div class="dm-chips" style="justify-content:flex-start">
        <span class="dm-status {css}"><span class="ic">{answer.icon(ic, answer.STATUS_ICON_COLOR[css])}</span>{label}</span>
        <span class="dm-chip"><small>Dataset</small>{html.escape(settings.dataset.name)}</span>{qid}
        <span class="dm-chip"><small>Rows</small><span class="tnum">{ev.fetched_rows:,}{" (truncated)" if ev.truncated else ""}</span></span>
        <span class="dm-chip"><small>Received</small>{turn.received_at.strftime("%H:%M:%S")}</span>
      </div></div>""")

    with st.container(key="studio-panel-page"):
        studio.kind_cards(i, "page", per_row=6)
        spec = studio.controls(i, "page", compact=False)
        studio.chart(i, "page", height=520)

    left, right = st.columns([1, 1.6], gap="large")
    with left:
        st.html('<div class="dm-eyebrow" style="margin-top:1rem">Insights · computed from the returned rows</div>')
        facts = cs.insights(spec, ev.result_columns, ev.rows, partial=answer.partial(resp))
        if facts:
            st.markdown("\n".join(f"- {f}" for f in facts))
        else:
            st.caption("No automatic insights for this chart configuration.")
        st.html('<div class="dm-eyebrow" style="margin-top:1rem">Answer</div>')
        with st.container(key="ans-page"):
            st.markdown(resp.answer)
    with right:
        st.html('<div class="dm-eyebrow" style="margin-top:1rem">Underlying data</div>')
        df = to_dataframe(ev.result_columns, ev.rows)
        st.dataframe(studio.display_frame(df), hide_index=True, width="stretch", height=min(38 + 35 * len(df), 420),
                     column_config=studio.number_formats(df))
        c1, c2 = st.columns(2)
        c1.download_button("Download CSV", df.to_csv(index=False).encode(), f"datamind_q{i + 1}.csv", "text/csv",
                           key="page-dl", icon=":material/download:", width="stretch")
        c2.download_button("Download report", answer.report_markdown(resp, settings).encode(),
                           f"datamind_q{i + 1}.md", "text/markdown", key="page-rep", icon=":material/description:",
                           width="stretch")
        with st.expander("SQL"):
            st.code(ev.sql, language="sql")
