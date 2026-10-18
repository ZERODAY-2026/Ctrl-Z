"""Answer card: question, honest status, answer, verification, assumptions and the evidence tabs.

Every field shown maps to a real DataMindResponse / Evidence field; absent fields are labelled as absent.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from config.settings import Settings
from src.orchestrator import DataMindResponse, Orchestrator, Status
from src.schemas.intent import ActionIntent
from src.services import chart_studio as cs
from src.services.followup_service import suggest
from src.services.grounding_service import fmt_number
from src.services.visualization_service import humanize, to_dataframe
from src.utils.errors import DataMindError, ErrorCategory
from ui import state, studio
from ui.theme import inline_svgs, svg_img

_CHECK = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2.2" '
          'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{p}</svg>')
ICON = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 '
             '1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "empty": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/>',
    "x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/>'
            '<path d="M3 18h.01"/>',
}


# Icon colour per status class (the icon sits in a tinted circle; see .dm-status .ic in theme.py).
STATUS_ICON_COLOR = {"ok": "#04201C", "assume": "#FCD34D", "warn": "#FCD34D", "info": "#A5B4FC", "bad": "#FDA4AF"}


def icon(name: str, color: str = "#98A6B8") -> str:
    return svg_img(_CHECK.format(c=color, p=ICON[name]))


def status_of(resp: DataMindResponse) -> tuple[str, str, str]:
    """(css class, icon, label) for the response's actual outcome."""
    ev = resp.evidence
    if resp.status == Status.ANSWERED:
        if ev is not None and ev.assumptions:
            return "assume", "alert", "Answered with assumptions"
        return "ok", "check", "Answered from live query"
    if resp.status == Status.EMPTY:
        return "info", "empty", "Query completed — no matching rows"
    if resp.status == Status.CLARIFY:
        return "warn", "help", "Needs clarification"
    if resp.status == Status.UNSUPPORTED:
        if resp.action_intent is not None and resp.action_intent.intent == ActionIntent.MODIFY_DATA:
            return "warn", "shield", "Change refused — DataMind is read-only"
        return "warn", "ban", "Unable to answer from this dataset"
    if resp.error_category == ErrorCategory.QUERY_VALIDATION.value:
        return "bad", "shield", "Query refused by safety guard"
    return "bad", "x", "Error — nothing was answered"


RECOVERY = {
    ErrorCategory.SNOWFLAKE_AUTH.value: "Check the Snowflake user and key/password in .env, then use Settings → Re-check connections.",
    ErrorCategory.SNOWFLAKE_PERMISSION.value: "The DataMind role cannot read this object. Grant SELECT on the dataset or pick another dataset.",
    ErrorCategory.SNOWFLAKE_CONNECTION.value: "Snowflake could not be reached. Check the network and retry.",
    ErrorCategory.SNOWFLAKE_WAREHOUSE.value: "The warehouse is suspended or unavailable. Resume it in Snowsight, then retry.",
    ErrorCategory.SNOWFLAKE_SQL.value: "Snowflake rejected the SQL. Try rephrasing the question more specifically.",
    ErrorCategory.QUERY_TIMEOUT.value: "The query hit the statement timeout. Narrow it with a filter (a year, region or segment).",
    ErrorCategory.QUERY_VALIDATION.value: "The SQL guard blocked it: only single read-only SELECTs over allowlisted tables run. Rephrase as a read-only question.",
    ErrorCategory.GEMMA_UNAVAILABLE.value: "Gemma did not respond. Check that Ollama is running, then retry.",
    ErrorCategory.GEMMA_INVALID_OUTPUT.value: "Gemma returned an unusable plan. Retrying or rephrasing usually helps.",
    ErrorCategory.INPUT.value: "Edit the question and send it again.",
}


partial = state.partial
_USER = svg_img('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
         'stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>', "#2DD4BF")


def question_bubble(i: int, question: str, asked_at) -> None:
    t = f'<time>{asked_at.strftime("%I:%M %p").lstrip("0")}</time>' if asked_at else ""
    with st.container(key=f"qb-{i}"):
        st.html(f'<div class="dm-qrow"><span class="av">{_USER}</span><b>{html.escape(question)}</b>{t}</div>')


def render(i: int, turn: state.Turn, settings: Settings, orch: Orchestrator, latest: bool, ready: bool) -> None:
    resp, ev = turn.resp, turn.resp.evidence
    css, ic, label = status_of(resp)
    question_bubble(i, resp.question, turn.asked_at)
    with st.container(key=f"card-{i}"):
        took = (turn.received_at - turn.asked_at).total_seconds()
        st.html(f'<div class="dm-head"><span class="dm-status {css}"><span class="ic">{icon(ic, STATUS_ICON_COLOR[css])}</span>{label}</span>'
                f'<span class="dm-time" title="Wall-clock time from question to answer">'
                f'{turn.received_at.strftime("%I:%M %p").lstrip("0")} · {took:,.1f}s</span></div>')

        if ev is not None and ev.fetched_rows == 1 and ev.result_columns and resp.status == Status.ANSWERED:
            _metrics(ev)
        with st.container(key=f"ans-{i}"):
            st.markdown(resp.answer)
        _verification(resp, settings)
        for n in resp.notes:
            st.caption(f"ℹ️ {n}")

        if resp.status == Status.CLARIFY:
            st.caption("Reply in the composer below. Your reply is sent with this question as context, so you can "
                       "answer briefly (for example, a year or a segment).")
        if resp.status == Status.ERROR:
            hint = RECOVERY.get(resp.error_category or "")
            if hint:
                st.caption(f"**How to recover:** {hint}")
            if resp.retryable and latest:
                if st.button("Retry question", key=f"retry-{i}", icon=":material/refresh:", disabled=not ready):
                    state.queue(resp.question)
                    st.rerun()

        if ev is not None:
            if ev.assumptions:
                with st.container(key=f"assume-{i}"):
                    with st.expander(f"Assumptions ({len(ev.assumptions)})"):
                        for a in ev.assumptions:
                            st.markdown(f"- {a}")
            if ev.limitations:
                with st.container(key=f"limits-{i}"):
                    with st.expander(f"Limitations and caveats ({len(ev.limitations)})"):
                        for lim in ev.limitations:
                            st.markdown(f"- {lim}")
            if ev.rows and ev.fetched_rows > 1:
                studio.chart(i, f"card{i}", height=260, quiet=True, modebar=False)
            _evidence_tabs(i, turn, settings)
            _actions(i, turn, orch, settings, ready)
        elif resp.plan is not None:
            with st.expander("Gemma plan"):
                st.json(resp.plan.model_dump(mode="json", exclude_defaults=True))

    if latest and ev is not None and ev.rows and resp.status == Status.ANSWERED:
        _follow_ups(i, resp, settings, ready)


display_frame, number_formats = studio.display_frame, studio.number_formats


def _metrics(ev) -> None:
    row = ev.rows[0]
    cells = "".join(f'<div class="dm-metric"><small>{html.escape(humanize(c))}</small><b>{html.escape(fmt_number(v))}'
                    f'</b></div>' for c, v in list(zip(ev.result_columns, row))[:4])
    st.html(f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;'
            f'margin:.5rem 0 .2rem">{cells}</div>')


def _verification(resp: DataMindResponse, settings: Settings) -> None:
    ev = resp.evidence
    if resp.answer_source == "gemma" and ev is not None:
        text = (f"Written by Gemma ({html.escape(settings.gemma.model)}). Every number and label in it was checked "
                "against the returned rows.")
        st.html(f'<div class="dm-verify">{icon("shield", "#34D399")}<span>{text}</span></div>')
    elif resp.answer_source == "deterministic" and ev is not None:
        extra = (f" Gemma's wording was withheld: {len(ev.unverified_claims)} value(s) were not in the result."
                 if ev.unverified_claims else "")
        st.html(f'<div class="dm-verify">{icon("list", "#93B4FF")}<span>Summary computed directly from the '
                f'returned rows (no model wording).{html.escape(extra)}</span></div>')
    elif resp.status in (Status.CLARIFY, Status.UNSUPPORTED) and resp.answer_source == "gemma":
        st.html(f'<div class="dm-verify">{icon("help", "#98A6B8")}<span>Gemma\'s interpretation; no query was '
                'run.</span></div>')


def _evidence_tabs(i: int, turn: state.Turn, settings: Settings) -> None:
    resp, ev = turn.resp, turn.resp.evidence
    tabs = st.tabs(["Results", "SQL", "How it was computed", "Query details", "Gemma plan"])
    with tabs[0]:
        if ev.rows:
            df = to_dataframe(ev.result_columns, ev.rows)
            st.dataframe(display_frame(df), hide_index=True, width="stretch", height=min(38 + 35 * len(df), 360),
                         column_config=number_formats(df))
        trunc = (f" · truncated: Snowflake produced {ev.total_rows:,} rows, the first {ev.fetched_rows:,} were fetched"
                 if ev.truncated and ev.total_rows else (" · truncated" if ev.truncated else ""))
        st.caption(f"{ev.fetched_rows:,} row(s) fetched{trunc}")
    with tabs[1]:
        origin = ("Compiled deterministically from Gemma's validated plan" if ev.sql_origin == "compiled_from_plan"
                  else "Written by Gemma, then validated by the SQL guard (model-written SQL)")
        st.caption(f"Exact SQL executed in Snowflake · {origin}")
        st.code(ev.sql, language="sql")
    with tabs[2]:
        for s in ev.steps:
            st.markdown(f"- {s}")
        rows = [("Tables", ", ".join(f"`{t}`" for t in ev.tables)), ("Joins", "; ".join(ev.joins)),
                ("Filters", "; ".join(ev.filters)), ("Grouped by", ", ".join(ev.groupings)),
                ("Calculations", "; ".join(f"`{c}`" for c in ev.calculations)),
                ("Columns", ", ".join(f"`{c}`" for c in ev.columns_used))]
        st.markdown("\n".join(f"**{k}:** {v}  " for k, v in rows if v))
    with tabs[3]:
        _details(turn, settings)
    with tabs[4]:
        if resp.plan is not None:
            st.json(resp.plan.model_dump(mode="json", exclude_defaults=True), expanded=2)
        else:
            st.caption("No structured plan was produced for this response.")


def _kv(rows: list[tuple[str, str | None]]) -> str:
    out = []
    for k, v in rows:
        out.append(f"<dt>{html.escape(k)}</dt>" + (f"<dd>{html.escape(v)}</dd>" if v not in (None, "")
                                                    else '<dd class="na">not provided by the backend</dd>'))
    return f'<dl class="dm-kv">{"".join(out)}</dl>'


def _details(turn: state.Turn, settings: Settings) -> None:
    resp, ev = turn.resp, turn.resp.evidence
    sf = (state.known_health() or {}).get("snowflake", {})
    st.html(_kv([
        ("Snowflake query ID", ev.query_id),
        ("Execution status", ev.status),
        ("Snowflake elapsed", f"{ev.elapsed_ms:,} ms"),
        ("Rows fetched", f"{ev.fetched_rows:,}"),
        ("Rows produced", f"{ev.total_rows:,}" if ev.total_rows is not None else None),
        ("Truncated", "yes" if ev.truncated else "no"),
        ("Session warehouse", sf.get("warehouse") if sf.get("ok") else None),
        ("Dataset", f"{settings.dataset.database}.{settings.dataset.schema}"),
        ("Answer received", turn.received_at.strftime("%Y-%m-%d %H:%M:%S") + " (local clock)"),
        ("Bytes / partitions scanned", None),
    ]))
    if resp.timings_ms:
        st.caption("Stage timings: " + " · ".join(f"{k} {v:,} ms" for k, v in resp.timings_ms.items()))
    if resp.gemma_calls:
        st.caption("Gemma calls: " + " · ".join(f"{c.task} {c.latency_ms:,} ms ({c.model}, {c.attempts} attempt"
                                                f"{'s' if c.attempts != 1 else ''})" for c in resp.gemma_calls))
    if ev.query_id:
        st.caption("Look the query ID up in Snowsight → Monitoring → Query History to confirm it ran.")


def report_markdown(resp: DataMindResponse, settings: Settings) -> str:
    ev = resp.evidence
    parts = [f"# DataMind answer\n\n**Question:** {resp.question}\n", f"**Dataset:** {settings.dataset.name} "
             f"(`{settings.dataset.database}.{settings.dataset.schema}`)\n", f"**Status:** {status_of(resp)[2]}\n",
             f"## Answer\n\n{resp.answer}\n"]
    if ev is not None:
        if ev.assumptions:
            parts.append("## Assumptions\n\n" + "\n".join(f"- {a}" for a in ev.assumptions) + "\n")
        if ev.limitations:
            parts.append("## Limitations\n\n" + "\n".join(f"- {a}" for a in ev.limitations) + "\n")
        parts.append("## How it was computed\n\n" + "\n".join(f"- {s}" for s in ev.steps) + "\n")
        parts.append(f"## SQL ({ev.sql_origin})\n\n```sql\n{ev.sql}\n```\n")
        parts.append(f"## Query details\n\n- Snowflake query ID: `{ev.query_id}`\n- Rows fetched: {ev.fetched_rows}"
                     f"{' (truncated)' if ev.truncated else ''}\n- Snowflake elapsed: {ev.elapsed_ms} ms\n")
        if ev.rows:
            df = to_dataframe(ev.result_columns, ev.rows).head(50)
            parts.append("## Result rows" + (" (first 50)" if ev.fetched_rows > 50 else "") + "\n\n"
                         + df.to_csv(index=False))
    return "\n".join(parts)


def _actions(i: int, turn: state.Turn, orch: Orchestrator, settings: Settings, ready: bool) -> None:
    resp, ev = turn.resp, turn.resp.evidence
    row = st.container(horizontal=True, gap="small", key=f"actions-{i}")
    if ev.rows:
        df = to_dataframe(ev.result_columns, ev.rows)
        row.download_button("Download CSV", df.to_csv(index=False).encode(), f"datamind_q{i + 1}.csv", "text/csv",
                            key=f"dl-{i}", icon=":material/download:", type="tertiary")
    row.download_button("Report", report_markdown(resp, settings).encode(), f"datamind_q{i + 1}.md",
                        "text/markdown", key=f"rep-{i}", icon=":material/description:", type="tertiary",
                            help="Question, answer, assumptions, SQL and query details as Markdown")
    if ev.rows and row.button("Visualize", key=f"viz-{i}", icon=":material/insert_chart:", type="tertiary"):
        st.session_state.viz_turn = i
        if st.session_state.viz_mode == "hidden":
            st.session_state.viz_mode = "panel"
        st.rerun()
    if row.button("Re-run SQL", key=f"rerun-{i}", icon=":material/replay:", type="tertiary",
                      disabled=not ready, help="Runs the same validated SQL again in Snowflake (no Gemma call) and "
                                               "reports whether the rows changed"):
        with st.spinner("Re-running the same SQL in Snowflake…"):
            try:
                res = orch.rerun(ev, resp.plan)
                same = res.columns == ev.result_columns and res.rows == list(ev.rows)
                turn.reruns.append({"ok": True, "same": same, "query_id": res.query_id, "rows": res.fetched_rows,
                                    "elapsed_ms": res.elapsed_ms, "result": res})
            except DataMindError as exc:
                turn.reruns.append({"ok": False, "error": exc.user_message})
    for r in turn.reruns[-1:]:
        if not r["ok"]:
            st.error(f"Re-run failed: {r['error']}")
        elif r["same"]:
            st.success(f"Re-run returned identical rows ({r['rows']:,}) · new query ID `{r['query_id']}` · "
                       f"{r['elapsed_ms']:,} ms", icon=":material/check_circle:")
        else:
            st.warning(f"The data changed: the same SQL now returns different rows ({r['rows']:,} fetched) · "
                       f"query ID `{r['query_id']}`. Ask the question again for an updated answer.",
                       icon=":material/difference:")
            res = r["result"]
            st.dataframe(pd.DataFrame(res.rows, columns=res.columns), hide_index=True, width="stretch",
                         height=min(38 + 35 * len(res.rows), 260))


def _follow_ups(i: int, resp: DataMindResponse, settings: Settings, ready: bool) -> None:
    ev = resp.evidence
    asks = suggest(resp.question, ev, settings.dataset.categorical_columns)
    # Chart actions change only the visualization of this result; they never send a query.
    local: dict[str, str] = {}
    p = cs.profile(ev.result_columns, ev.result_types, ev.rows)
    for kind, text in (("donut", "Show this as a pie chart"), ("line", "Show this as a line chart")):
        spec = cs.coerce(cs.ChartSpec(kind), p)
        if cs.check(spec, p, ev.result_columns, ev.rows)[0] is None and len(local) < 1:
            local[text] = kind
    options = [*local, *asks][:5]
    if not options:
        return
    key = f"fu-{i}"

    def picked() -> None:
        choice = st.session_state.get(key)
        st.session_state[key] = None
        if choice in local:
            st.session_state.viz[i] = cs.ChartSpec(local[choice]).as_dict()
            st.session_state.viz_turn = i
            st.session_state.viz_mode = "panel" if st.session_state.viz_mode == "hidden" else st.session_state.viz_mode
        elif choice:
            state.queue(choice)

    st.html(inline_svgs('<div class="dm-verify" style="margin-top:.6rem;font-size:.84rem;color:var(--dm-ink)">'
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2DD4BF" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.9 4.6L18.5 9l-4.6 '
            '1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z"/><path d="M19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></svg>'
            'Suggested follow-up questions</div>'))
    st.pills("Suggested follow-up questions", options, key=key, on_change=picked, label_visibility="collapsed",
             disabled=not ready, format_func=lambda o: f":material/insert_chart: {o}" if o in local else o)
