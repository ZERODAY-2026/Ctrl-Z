"""Page 1 — Home / landing. Static product content plus a clearly labelled sample preview.

The Snowflake / Gemma chips in the nav come from the real health checks, which run after the page has drawn.
"""

from __future__ import annotations

import html

import streamlit as st

from ui import nav, state, theme
from ui.status import chips
from ui.theme import inline_svgs, svg_img

REPO_URL = "https://github.com/ZERODAY-2026/Ctrl-Z"

_CSS = """
<style>
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"], footer { display:none !important; }
.stApp { background:
  radial-gradient(900px 600px at 20% 85%, rgba(45,212,191,.07), transparent 60%),
  radial-gradient(1000px 700px at 85% 20%, rgba(30,60,80,.35), transparent 65%), var(--dm-bg) !important; }
.block-container { max-width: 1440px !important; padding: 1.3rem 2.5rem 2rem !important; }
.dm-navrow { display:flex; align-items:center; gap:2.6rem; }
.dm-navlinks { display:flex; gap:1.7rem; font-size:1rem; }
.dm-navlinks a { color:var(--dm-muted) !important; text-decoration:none !important; padding:.3rem 0; position:relative;
  white-space:nowrap; }
.st-key-home_chips .dm-chips { flex-wrap:nowrap; }
.st-key-nav_open button p, [class*="st-key-cta"] button p { white-space:nowrap; }
.dm-navlinks a:hover { color:var(--dm-ink) !important; }
.dm-navlinks a.on { color:var(--dm-ink) !important; }
.dm-navlinks a.on::after { content:""; position:absolute; left:0; right:0; bottom:-.45rem; height:2px; border-radius:2px;
  background:var(--dm-accent); }
.st-key-nav_open button { border-radius:6px !important; background:#F4F7FB !important; border:0 !important; min-height:2.9rem; }
.st-key-nav_open button p { color:#0B0F14 !important; font-weight:600 !important; }
.st-key-nav_open button:hover { background:#FFFFFF !important; }

.dm-hero .eb { font-size:.95rem; letter-spacing:.32em; color:var(--dm-muted); font-weight:500; }
.dm-hero h1 { font-family: var(--dm-serif); font-weight:500; font-size: clamp(2.8rem, 6vw, 5.3rem); line-height:1.02;
  letter-spacing:-.025em; margin: 1.3rem 0 1.6rem; padding:0; color:var(--dm-ink); }
.dm-hero h1 em { font-style:normal; color: var(--dm-accent); }
.dm-hero p { color:var(--dm-muted); font-size:1.3rem; line-height:1.55; max-width:35rem; margin:0; }
a.dm-ghost { display:inline-flex; align-items:center; justify-content:center; gap:.6rem; min-height:3.35rem;
  width:100%; border-radius:6px; border:1px solid #5B6676; color:var(--dm-ink) !important;
  text-decoration:none !important; font-weight:500; font-size:1.02rem; transition: border-color .15s, background .15s; }
a.dm-ghost:hover { border-color:#8996A8; background:rgba(255,255,255,.03); }

/* product preview (sample data) */
.dm-prev { position:relative; border-radius:14px; border:1px solid var(--dm-line-2); overflow:hidden;
  background: #0E141B; box-shadow: 0 50px 100px -40px rgba(0,0,0,.9), 0 0 80px -30px rgba(45,212,191,.15);
  display:grid; grid-template-columns: 190px 1fr; font-size:.78rem; }
.dm-prev aside { border-right:1px solid var(--dm-line); padding:1rem .8rem; background:#0C1218; }
.dm-prev aside .nav { margin:1rem 0; display:grid; gap:.15rem; }
.dm-prev aside .nav div { display:flex; gap:.5rem; align-items:center; padding:.45rem .5rem; border-radius:7px; color:#C9D3DF; }
.dm-prev aside .nav div.on { background:var(--dm-s2); color:var(--dm-ink); }
.dm-prev aside h6 { font-size:.7rem; color:var(--dm-muted); margin:.9rem 0 .4rem; font-weight:500; padding:0; }
.dm-prev aside .ds { border:1px solid var(--dm-line); border-radius:7px; padding:.45rem .55rem; background:var(--dm-s1); }
.dm-prev aside .ds small { display:block; color:var(--dm-faint); font-size:.55rem; margin-top:.15rem; letter-spacing:.02em; }
.dm-prev aside ul { list-style:none; margin:0; padding:0; }
.dm-prev aside li { display:flex; gap:.45rem; align-items:center; padding:.32rem .1rem; font-size:.66rem; color:#C9D3DF; }
.dm-prev aside li span { margin-left:auto; color:var(--dm-faint); font-variant-numeric: tabular-nums; }
.dm-prev main { padding:1rem; min-width:0; }
.dm-prev .ask { display:flex; align-items:center; gap:.6rem; border:1px solid var(--dm-line-2); border-radius:9px;
  padding:.45rem .5rem .45rem .7rem; background:var(--dm-s1); color:var(--dm-ink); }
.dm-prev .ask b { margin-left:auto; width:1.8rem; height:1.8rem; border-radius:6px; background:var(--dm-accent);
  display:grid; place-items:center; color:var(--dm-accent-ink); }
.dm-prev .card { margin-top:.8rem; border:1px solid var(--dm-line); border-radius:10px; background:var(--dm-s1); padding:.8rem .9rem; }
.dm-prev .ok { display:flex; align-items:center; gap:.45rem; color:var(--dm-accent); font-weight:600; font-size:.74rem; }
.dm-prev .ok i { width:1.05rem; height:1.05rem; border-radius:50%; background:var(--dm-accent); color:var(--dm-accent-ink);
  display:grid; place-items:center; font-style:normal; font-size:.6rem; }
.dm-prev .ans { font-family:var(--dm-serif); font-size:1.02rem; line-height:1.45; color:var(--dm-ink); margin:.55rem 0 .6rem; }
.dm-prev .tabs { display:flex; gap:.2rem; margin-top:.7rem; flex-wrap:wrap; }
.dm-prev .tabs span { padding:.3rem .45rem; white-space:nowrap; font-size:.6rem !important; border-radius:6px; color:var(--dm-muted); font-size:.66rem; }
.dm-prev .tabs span.on { background:var(--dm-s2); color:var(--dm-ink); border:1px solid var(--dm-line-2); }
.dm-prev .tag { position:absolute; top:.7rem; right:.8rem; font-size:.58rem; letter-spacing:.14em; color:#FCD34D;
  border:1px solid rgba(251,191,36,.35); border-radius:5px; padding:.12rem .4rem; background:rgba(11,15,20,.85); }

/* sections */
.dm-sec { margin-top: 6rem; scroll-margin-top: 1.5rem; }
.dm-sec h2 { font-family:var(--dm-serif); font-size: clamp(1.9rem, 3.4vw, 2.8rem); letter-spacing:-.02em; font-weight:500;
  color:var(--dm-ink); margin:.5rem 0 .6rem; padding:0; }
.dm-sec > p { color:var(--dm-muted); max-width:40rem; margin:0; font-size:1.05rem; }
.dm-kicker { font-size:.72rem; letter-spacing:.26em; color:var(--dm-accent); font-weight:600; }
.dm-flow { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:1.2rem; margin-top:2rem; position:relative; }
.dm-flow::before { content:""; position:absolute; top:2.55rem; left:8%; right:8%; height:1px;
  background: linear-gradient(90deg, transparent, rgba(45,212,191,.5) 15%, rgba(45,212,191,.5) 85%, transparent); }
.dm-flow .st { position:relative; background:var(--dm-s1); border:1px solid var(--dm-line); border-radius:14px;
  padding:1.3rem 1.3rem 1.4rem; transition: transform .2s, border-color .2s; }
.dm-flow .st:hover { transform: translateY(-3px); border-color: rgba(45,212,191,.45); }
.dm-flow .num { display:inline-grid; place-items:center; width:2.6rem; height:2.6rem; border-radius:50%;
  font-size:.85rem; font-weight:600; color:var(--dm-accent); margin-bottom:.9rem; background:var(--dm-bg);
  border:1px solid rgba(45,212,191,.55); position:relative; }
.dm-flow h3 { font-size:1.2rem; margin:0 0 .35rem; padding:0; color:var(--dm-ink); font-weight:600; }
.dm-flow p { color:var(--dm-muted); font-size:1rem; line-height:1.6; margin:0; }
.dm-feat { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:1rem; margin-top:2rem; }
.dm-feat .f { background:var(--dm-s1); border:1px solid var(--dm-line); border-radius:14px; padding:1.25rem;
  transition: border-color .2s, transform .2s; }
.dm-feat .f:hover { border-color: rgba(45,212,191,.45); transform: translateY(-2px); }
.dm-feat .ic { width:2.8rem; height:2.8rem; border-radius:10px; display:grid; place-items:center; margin-bottom:.9rem;
  background:var(--dm-s2); border:1px solid var(--dm-line); }
.dm-feat h3 { font-size:1.1rem; margin:0 0 .3rem; padding:0; color:var(--dm-ink); font-weight:600; }
.dm-feat p { color:var(--dm-muted); font-size:.97rem; line-height:1.55; margin:0; }
.dm-ds-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:1rem; margin-top:2rem; }
.dm-ds-grid .d { background:var(--dm-s1); border:1px solid var(--dm-line); border-radius:14px; padding:1.2rem; }
.dm-ds-grid .d h3 { margin:0; padding:0; font-size:1.05rem; color:var(--dm-ink); font-weight:600; }
.dm-ds-grid .d code { color:var(--dm-muted); font-size:.74rem; background:none; padding:0; }
.dm-ds-grid .d p { color:var(--dm-muted); font-size:.95rem; line-height:1.55; margin:.6rem 0 0; }
.st-key-cta_close { margin-top:-6.2rem; position:relative; z-index:2; }
.dm-close { margin-top:6rem; text-align:center; border:1px solid var(--dm-line); border-radius:20px; padding:3.2rem 1.2rem 6.4rem;
  background: radial-gradient(600px 240px at 50% 0%, rgba(45,212,191,.14), transparent 70%), var(--dm-s1); }
.dm-close h2 { font-family:var(--dm-serif); font-size: clamp(2rem, 4vw, 3.1rem); letter-spacing:-.02em; font-weight:500;
  margin:0; padding:0; color:var(--dm-ink); }
.dm-close p { color:var(--dm-muted); margin:.7rem 0 0; }
.dm-foot { margin-top:3rem; padding-top:1.4rem; border-top:1px solid var(--dm-line); display:flex; flex-wrap:wrap;
  justify-content:space-between; gap:1rem; color:var(--dm-faint); font-size:.82rem; }
.dm-foot a { color:var(--dm-muted) !important; }
.dm-foot p { margin:.5rem 0 0; max-width:32rem; }

@media (max-width: 1400px) { .dm-navlinks { gap:1.3rem; font-size:.92rem; } .dm-navrow { gap:1.6rem; } }
@media (max-width: 1100px) {
  .dm-navlinks { display:none; }
  .dm-flow, .dm-feat { grid-template-columns: 1fr; }
  .dm-flow::before { display:none; }
  .dm-sec { margin-top: 3.5rem; }
  .block-container { padding: 1rem 1rem 2rem !important; }
}
@media (max-width: 560px) { .dm-prev { grid-template-columns: 1fr; } .dm-prev aside { display:none; } }
</style>
"""

_I = ('<svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.9" '
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{p}</svg>')
FEATURES = [
    ("#5EEAD4", '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
     "Natural-language analytics", "Ask in plain English. Gemma turns the question into a strict, schema-checked "
     "query plan, and asks you to clarify when a question is ambiguous."),
    ("#29B5E8", '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/>'
     '<path d="M3 12a9 3 0 0 0 18 0"/>',
     "Real Snowflake execution", "Every answer comes from a live query on your warehouse. Each one has a real "
     "Snowflake query ID you can look up in Query History."),
    ("#A78BFA", '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
     "Interactive visualizations", "Bar, line, pie, area and scatter charts drawn from the returned rows, "
     "with a warning when a chart type would mislead."),
    ("#FB7185", '<polyline points="15 10 20 15 15 20"/><path d="M4 4v7a4 4 0 0 0 4 4h12"/>',
     "Context-aware follow-ups", "Refine a result in a few words. Your recent questions and Gemma's plans go along "
     "as context for each follow-up."),
    ("#FBBF24", '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
     "SQL transparency and evidence", "See the exact SQL, the rows, the joins, filters and calculations, and every "
     "assumption, next to the answer."),
    ("#34D399", '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-4"/>',
     "Safe, read-only execution", "A parser-based SQL guard allows a single SELECT over allowlisted tables, with a "
     "row limit. It runs a read-only role and a statement timeout."),
]

# Illustrative bars for the preview (teal -> blue -> sky -> sand -> orange, the DataMind chart look).
_BARS = [("5-LOW", 91, "#22C7B0"), ("2-HIGH", 88, "#1497C9"), ("1-URGENT", 84, "#5CC4EE"),
         ("4-NOT SPEC.", 80, "#E3CC8C"), ("3-MEDIUM", 76, "#EF9A4A")]


def _preview() -> str:
    bars = "".join(
        f'<rect x="{38 + k * 62}" y="{118 - h}" width="46" height="{h}" rx="3" fill="{c}"/>'
        f'<text x="{61 + k * 62}" y="132" text-anchor="middle" fill="#98A6B8" font-size="8.5">{lab}</text>'
        for k, (lab, h, c) in enumerate(_BARS))
    grid = "".join(f'<line x1="30" x2="350" y1="{y}" y2="{y}" stroke="rgba(152,166,184,.12)"/>' for y in (28, 58, 88, 118))
    tables = [("CUSTOMER", "150,000"), ("ORDERS", "1,500,000"), ("LINEITEM", "6,001,215"), ("PART", "200,000"),
              ("SUPPLIER", "10,000"), ("NATION", "25"), ("REGION", "5"), ("PARTSUPP", "800,000")]
    tbl = "".join(f'<li>▦ {t}<span>{n}</span></li>' for t, n in tables)
    return inline_svgs(f"""
<div class="dm-prev" role="img" aria-label="Illustrative preview of the DataMind workspace with sample data">
  <span class="tag">PREVIEW · SAMPLE</span>
  <aside>{theme.brand(size=22)}
    <div class="nav"><div class="on">⌂ New Conversation</div><div>◎ Explore Data</div><div>↺ Query History</div></div>
    <h6>Dataset</h6><div class="ds">TPC-H Sample<small>SNOWFLAKE_SAMPLE_DATA · TPCH_SF1</small></div>
    <h6>Tables</h6><ul>{tbl}</ul></aside>
  <main>
    <div class="ask">💬 What are the total sales by order priority?<b>→</b></div>
    <div class="card">
      <div class="ok"><i>✓</i>Answered from a live query</div>
      <div class="ans">The highest total sales are for 5-LOW, followed closely by 2-HIGH. All five priorities sit
        within a few percent of each other.</div>
      <div style="font-size:.7rem;color:var(--dm-ink);font-weight:600">Total sales by order priority</div>
      <svg viewBox="0 0 360 138" width="100%">{grid}{bars}</svg>
    </div>
    <div class="tabs"><span class="on">Results</span><span>SQL</span><span>How it was computed</span>
      <span>Query details</span><span>Gemma plan</span></div>
  </main>
</div>""")


def render() -> None:
    st.html(_CSS)
    n1, n3, n4 = st.columns([5, 3.5, 1.25], vertical_alignment="center")
    n1.html(f'<div class="dm-navrow">{theme.brand(size=34)}<nav class="dm-navlinks" aria-label="Sections">'
            '<a class="on" href="#home">Home</a><a href="#features">Features</a><a href="#how-it-works">How it works</a>'
            '<a href="#datasets">Datasets</a><a href="#about">About</a></nav></div>')
    status_slot = n3.container(key="home_chips").empty()
    known = state.known_health()
    status_slot.html(chips(known["snowflake"], known["gemma"]) if known else chips(None, None, unknown="Checking…"))
    if n4.button("Open Workspace", key="nav_open", width="stretch"):
        nav.go("workspace")

    st.html('<div id="home" style="height:3.5rem"></div>')
    left, right = st.columns([1, 1.12], gap="large", vertical_alignment="center")
    with left:
        st.html("""<section class="dm-hero"><div class="eb">ASK. QUERY. VERIFY.</div>
          <h1>Your data,<br>Your questions.<br><em>Clear answers.</em></h1>
          <p>Explore your data in plain English. DataMind turns your questions into validated SQL, runs real queries
          on Snowflake, and shows you clear, trustworthy answers with interactive visualizations.</p></section>""")
        st.html('<div style="height:1.6rem"></div>')
        b1, b2, _ = st.columns([1.2, 1, .25], gap="medium")
        with b1:
            if st.button("Let's Dive Into Your Data", key="cta_hero", width="stretch"):
                nav.go("workspace")
        b2.html(inline_svgs('<a class="dm-ghost" href="#how-it-works"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
                'stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="10"/>'
                '<path d="m10 8 6 4-6 4z" fill="currentColor"/></svg>See How It Works</a>', "#F4F7FB"))
    with right:
        st.html(_preview())

    st.html("""<section class="dm-sec" id="how-it-works"><span class="dm-kicker">HOW IT WORKS</span>
      <h2>From question to verified answer.</h2>
      <p>Three steps, and you can see all of them.</p>
      <div class="dm-flow">
        <div class="st"><span class="num">01</span><h3>Ask</h3><p>Describe the business question in plain English.</p></div>
        <div class="st"><span class="num">02</span><h3>Query</h3><p>Gemma interprets your question. DataMind validates
          the plan and executes safe SQL on Snowflake.</p></div>
        <div class="st"><span class="num">03</span><h3>Verify</h3><p>Inspect the answer, the SQL, the returned rows, and
          the query details.</p></div>
      </div></section>""")

    cards = "".join(f'<div class="f"><div class="ic">{svg_img(_I.format(c=c, p=p))}</div><h3>{html.escape(t)}</h3>'
                    f'<p>{html.escape(d)}</p></div>' for c, p, t, d in FEATURES)
    st.html(f"""<section class="dm-sec" id="features"><span class="dm-kicker">FEATURES</span>
      <h2>Built for answers you can check.</h2>
      <p>Each feature is there so you can check the answer you get.</p>
      <div class="dm-feat">{cards}</div></section>""")

    try:
        ds_cards = "".join(_dataset_card(path, name) for path, name in state.available_datasets().items())
    except Exception:  # noqa: BLE001 - configuration problems are reported in the workspace
        ds_cards = ""
    if ds_cards:
        st.html(f"""<section class="dm-sec" id="datasets"><span class="dm-kicker">DATASETS</span>
          <h2>Bring a dataset with one config file.</h2>
          <p>A dataset is a TOML file that lists its tables, join paths, metrics and descriptions. Switch between
          them from the workspace header.</p><div class="dm-ds-grid">{ds_cards}</div></section>""")

    st.html("""<section class="dm-sec" id="about"><span class="dm-kicker">ABOUT</span>
      <h2>Why DataMind exists.</h2>
      <p>Dashboards only answer the questions someone planned for, and generic chatbots make up numbers. DataMind
      sits in between. Google Gemma interprets the question, deterministic code builds and guards the SQL, Snowflake
      runs it, and every answer comes with the evidence it came from. If the data cannot answer a question,
      DataMind says so.</p></section>""")

    st.html('<section class="dm-close"><h2>Stop guessing. Start asking your data.</h2>'
            '<p>Open the workspace and ask your first question. It runs a real query.</p></section>')
    _, mid, _ = st.columns([1.2, 1, 1.2])
    with mid:
        if st.button("Let's Dive Into Your Data", key="cta_close", width="stretch"):
            nav.go("workspace")

    st.html(f"""<footer class="dm-foot"><div>{theme.brand(size=26)}
      <p>DataMind is natural-language analytics over Snowflake. Google Gemma plans each query, a read-only SQL guard
      validates it, and every answer shows its evidence.</p></div>
      <div style="text-align:right">Built with Google Gemma · Snowflake · Streamlit · Plotly<br>
      <a href="{REPO_URL}" target="_blank" rel="noopener">Source repository</a> · MIT licence<br>
      ZeroDay OSS Hacktoberfest 2026</div></footer>""")

    # Real health checks run last so the page is visible first; the chips update when they finish.
    if not known:
        try:
            sf, gm = state.run_health_checks()
        except Exception:  # noqa: BLE001 - e.g. missing configuration; the workspace explains it
            return
        status_slot.html(chips(sf, gm))


def _dataset_card(path: str, name: str) -> str:
    from config.settings import DatasetConfig
    d = DatasetConfig.load(path)
    desc = d.description.split("\n")[0].strip()
    return (f'<div class="d"><h3>{html.escape(name)}</h3><code>{html.escape(d.database)}.{html.escape(d.schema)}</code>'
            f'<p>{html.escape(desc)}</p><p style="font-size:.78rem">{len(d.tables)} tables · '
            f'{len(d.example_questions)} example questions</p></div>')
