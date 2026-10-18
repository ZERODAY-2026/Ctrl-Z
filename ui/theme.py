"""Design tokens and CSS for the DataMind UI. Colours are defined once here and in .streamlit/config.toml."""

from __future__ import annotations

import base64
import re

import streamlit as st

_SVG = re.compile(r"<svg\b[^>]*>.*?</svg>", re.S)


def svg_img(markup: str, color: str = "#98A6B8") -> str:
    """Inline SVG -> <img> with a data URI. st.html sanitizes away <svg> elements, but keeps images.

    currentColor has no meaning inside an image, so it is replaced with `color`. Size and class carry over.
    """
    m = markup.strip().replace("currentColor", color)
    head = re.match(r"<svg\b([^>]*)>", m).group(1)
    if "xmlns=" not in head:
        m = m.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    attr = {k: v for k, v in re.findall(r'(?<![\w-])(width|height|class)="([^"]*)"', head)}
    style = "".join(f"{k}:{v}{'px' if v.replace('.', '').isdigit() else ''};" for k, v in attr.items()
                    if k in ("width", "height"))
    cls = f' class="{attr["class"]}"' if "class" in attr else ""
    data = base64.b64encode(m.encode()).decode()
    return f'<img src="data:image/svg+xml;base64,{data}" alt="" aria-hidden="true"{cls} style="{style}">'


def inline_svgs(body: str, color: str = "#98A6B8") -> str:
    """Convert every inline <svg> in an HTML fragment (see svg_img)."""
    return _SVG.sub(lambda m: svg_img(m.group(0), color), body)

TOKENS = """
:root {
  --dm-bg:#0B0F14; --dm-s1:#111821; --dm-s2:#161F2B; --dm-s3:#1B2533; --dm-line:#243040; --dm-line-2:#2E3B4D;
  --dm-ink:#F4F7FB; --dm-muted:#98A6B8; --dm-faint:#6B7A8D;
  --dm-accent:#2DD4BF; --dm-accent-2:#5EEAD4; --dm-accent-ink:#04201C;
  --dm-blue:#4778FF; --dm-violet:#A78BFA; --dm-coral:#FB7185; --dm-amber:#FBBF24; --dm-emerald:#34D399;
  --dm-ok:#34D399; --dm-warn:#FBBF24; --dm-bad:#FB7185; --dm-unknown:#98A6B8;
  --dm-mono:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --dm-sans:Inter, system-ui, -apple-system, 'Segoe UI', sans-serif;
  --dm-serif:'Source Serif 4', 'Source Serif Pro', Georgia, serif;
  --dm-radius:14px;
}
"""

BASE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600&display=swap');
""" + TOKENS + """
.stApp { background: var(--dm-bg); }
.stApp, .stMarkdown, button, input, textarea { font-family: var(--dm-sans); }
code, pre, .dm-mono { font-family: var(--dm-mono) !important; }
[data-testid="stMain"] { scroll-behavior: smooth; }
.block-container { padding-top: 1.1rem !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
:focus-visible { outline: 2px solid var(--dm-accent) !important; outline-offset: 2px; }
.tnum { font-variant-numeric: tabular-nums; }

/* ---------- brand ---------- */
.dm-brand { display:flex; align-items:center; gap:.55rem; color:var(--dm-ink); text-decoration:none; }
.dm-brand img { flex:none; display:block; }
.dm-word { font-weight:650; font-size:1.3rem; letter-spacing:-.02em; line-height:1; }
.dm-brand small { display:block; font-family:var(--dm-sans); font-size:.56rem; letter-spacing:.24em; color:var(--dm-muted);
                  margin-top:.3rem; font-weight:500; }

/* ---------- status chips (icon + text, never colour alone) ---------- */
.dm-chip { display:inline-flex; align-items:center; gap:.45rem; padding:.45rem .85rem; border-radius:10px;
           font-size:.86rem; font-weight:550; border:1px solid var(--dm-line-2); background:var(--dm-s1);
           color:var(--dm-ink); white-space:nowrap; max-width:100%; overflow:hidden; text-overflow:ellipsis; }
.dm-chip .st { display:inline-flex; align-items:center; gap:.3rem; font-weight:550; }
.dm-chip.ok .st { color:var(--dm-ok); }
.dm-chip.bad .st { color:var(--dm-bad); }
.dm-chip.unknown .st { color:var(--dm-muted); }
.dm-chip .dot { width:.45rem; height:.45rem; border-radius:50%; flex:none; background:currentColor; }
.dm-chip.unknown .dot { background:transparent; border:1.5px dashed currentColor; }
.dm-chip small { color:var(--dm-muted); font-weight:450; font-size:.72rem; }
.dm-chips { display:flex; flex-wrap:wrap; gap:.5rem; justify-content:flex-end; }

/* ---------- buttons ---------- */
.stButton button[kind="primary"], .stDownloadButton button[kind="primary"] {
  background: var(--dm-accent) !important; color: var(--dm-accent-ink) !important; border:0 !important; font-weight:600 !important; }
.stButton button[kind="primary"]:hover { background: var(--dm-accent-2) !important; }
.stButton button[kind="primary"] p { color: var(--dm-accent-ink) !important; font-weight:600 !important; }
[class*="st-key-cta"] button { background: var(--dm-accent) !important; border:0 !important; border-radius:6px !important;
  padding:.85rem 1.6rem !important; box-shadow: 0 14px 40px -14px rgba(45,212,191,.7) !important;
  transition: transform .18s ease, box-shadow .18s ease, background .18s !important; }
[class*="st-key-cta"] button p { color: var(--dm-accent-ink) !important; font-weight:600 !important; font-size:1.02rem !important; }
[class*="st-key-cta"] button:hover { background: var(--dm-accent-2) !important; transform: translateY(-2px);
  box-shadow: 0 20px 48px -14px rgba(45,212,191,.85) !important; }
[class*="st-key-cta"] button:active { transform: translateY(0); }
[class*="st-key-cta"] button:focus-visible { outline: 2px solid var(--dm-ink) !important; outline-offset: 3px; }

/* ---------- conversation ---------- */
[class*="st-key-qb-"] { background: var(--dm-s1); border:1px solid var(--dm-line); border-radius: 12px; padding:.8rem 1rem !important; }
.dm-qrow { display:flex; align-items:center; gap:.8rem; }
.dm-qrow .av { width:1.9rem; height:1.9rem; border-radius:50%; display:grid; place-items:center; flex:none;
  background: rgba(45,212,191,.16); color: var(--dm-accent); }
.dm-qrow b { flex:1; font-weight:500; font-size:1.05rem; color:var(--dm-ink); line-height:1.4; overflow-wrap:anywhere; }
.dm-qrow time, .dm-time { color:var(--dm-faint); font-size:.76rem; white-space:nowrap; font-variant-numeric: tabular-nums; }
[class*="st-key-card-"] { background: var(--dm-s1); border:1px solid var(--dm-line) !important; border-radius: var(--dm-radius);
  padding: 1.1rem 1.2rem 1.15rem !important; }
.dm-head { display:flex; align-items:center; justify-content:space-between; gap:.6rem; flex-wrap:wrap; }
.dm-status { display:inline-flex; align-items:center; gap:.5rem; font-size:.86rem; font-weight:600; }
.dm-status .ic { width:1.35rem; height:1.35rem; border-radius:50%; display:grid; place-items:center; flex:none; }
.dm-status.ok { color:var(--dm-accent); } .dm-status.ok .ic { background:var(--dm-accent); color:var(--dm-accent-ink); }
.dm-status.assume, .dm-status.warn { color:#FCD34D; } .dm-status.assume .ic, .dm-status.warn .ic { background:rgba(251,191,36,.18); }
.dm-status.info { color:#A5B4FC; } .dm-status.info .ic { background:rgba(129,140,248,.18); }
.dm-status.bad { color:#FDA4AF; } .dm-status.bad .ic { background:rgba(251,113,133,.18); }
.dm-answer, [class*="st-key-ans-"] p, [class*="st-key-ans-"] li { font-family: var(--dm-serif) !important;
  font-size: 1.32rem !important; line-height: 1.5 !important; color: var(--dm-ink); font-variant-numeric: tabular-nums;
  letter-spacing: -.005em; }
.dm-verify { display:flex; align-items:center; gap:.45rem; color:var(--dm-muted); font-size:.86rem; margin:.1rem 0 .3rem; }
.dm-verify img { flex:none; }
.dm-ctitle { font-size:.95rem; font-weight:600; color:var(--dm-ink); margin:.3rem 0 -.2rem; }
[class*="st-key-assume-"] details { border-color: rgba(251,191,36,.35) !important; background: rgba(251,191,36,.05); }
[class*="st-key-assume-"] summary p { color: #FCD34D !important; }
[class*="st-key-limits-"] details { border-color: rgba(251,113,133,.30) !important; background: rgba(251,113,133,.04); }
.dm-kv { display:grid; grid-template-columns: minmax(8rem, max-content) 1fr; gap:.35rem 1rem; font-size:.84rem; }
.dm-kv dt { color:var(--dm-muted); }
.dm-kv dd { margin:0; color:var(--dm-ink); font-family:var(--dm-mono); font-size:.8rem; overflow-wrap:anywhere; }
.dm-kv dd.na { color:var(--dm-faint); font-family:var(--dm-sans); font-style:italic; }
.dm-metric { background:var(--dm-s2); border:1px solid var(--dm-line); border-radius:12px; padding:.7rem .9rem; }
.dm-metric small { color:var(--dm-muted); font-size:.75rem; display:block; }
.dm-metric b { font-size:1.6rem; font-weight:650; letter-spacing:-.02em; font-variant-numeric: tabular-nums; }
.dm-eyebrow { font-size:.68rem; letter-spacing:.16em; color:var(--dm-muted); text-transform:uppercase; margin:.2rem 0 .45rem;
  font-weight:600; }
.dm-h { font-size:1.25rem; font-weight:650; letter-spacing:-.015em; color:var(--dm-ink); margin:0; line-height:1.2; }
.dm-muted { color:var(--dm-muted); font-size:.84rem; }

/* tabs as outlined pills */
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:.45rem; border-bottom:0 !important; flex-wrap:wrap; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] [data-baseweb="tab-border"] { display:none; }
[data-testid="stTabs"] button[role="tab"] { border:1px solid var(--dm-line-2); border-radius:8px; padding:.45rem 1rem !important;
  height:auto; background:var(--dm-s1); }
[data-testid="stTabs"] button[role="tab"] p { font-size:.9rem; color:var(--dm-muted); }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] { border-color:var(--dm-accent); background:rgba(45,212,191,.08); }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p { color:var(--dm-ink); font-weight:600; }

/* follow-up pills */
[class*="st-key-fu-"] [data-testid="stButtonGroup"] button { border-radius:999px !important; background:var(--dm-s2) !important;
  border:1px solid var(--dm-line-2) !important; }
[class*="st-key-fu-"] [data-testid="stButtonGroup"] button:hover { border-color: var(--dm-accent) !important; }
[class*="st-key-fu-"] [data-testid="stButtonGroup"] button p { font-size:.82rem !important; }

/* example prompts (empty state) */
[class*="st-key-ex-"] button { background: var(--dm-s1) !important; border:1px solid var(--dm-line) !important; border-radius:12px !important;
  text-align:left !important; justify-content:flex-start !important; min-height:3.2rem; transition: border-color .15s, background .15s !important; }
[class*="st-key-ex-"] button:hover { border-color: var(--dm-accent) !important; background: rgba(45,212,191,.06) !important; }
[class*="st-key-ex-"] button p { font-size:.95rem !important; text-align:left; }
[class*="st-key-ex-"] button > div, [class*="st-key-sq-"] button > div { justify-content:flex-start !important; width:100%; }
[class*="st-key-ex-"] button [data-testid="stMarkdownContainer"],
[class*="st-key-sq-"] button [data-testid="stMarkdownContainer"] { text-align:left; width:100%; }

/* composer */
[data-testid="stChatInput"] { border-radius: 14px !important; background: var(--dm-s1) !important; border:1px solid var(--dm-line-2) !important; }
[data-testid="stChatInput"] textarea { font-size: .98rem !important; }
[data-testid="stChatInputSubmitButton"] { background: var(--dm-accent) !important; border-radius:10px !important; color:var(--dm-accent-ink) !important; }
[data-testid="stChatInputSubmitButton"] svg { fill: var(--dm-accent-ink) !important; color: var(--dm-accent-ink) !important; }
[data-testid="stChatInputSubmitButton"]:disabled { background: var(--dm-s3) !important; }

/* ---------- sidebar: Your Data ---------- */
[data-testid="stSidebar"] { border-right:1px solid var(--dm-line); }
/* Wider only while open: when collapsed, Streamlit slides the sidebar out by its own width, so a forced
   width there would leave a strip on screen and keep the main area offset. */
@media (min-width: 900px) {
  section[data-testid="stSidebar"][aria-expanded="true"] { width: 340px !important; min-width: 340px !important; }
}
/* Streamlit's top bar is opaque and would cover the workspace header; keep only its buttons clickable. */
header[data-testid="stHeader"] { background: transparent !important; pointer-events: none; }
header[data-testid="stHeader"] * { pointer-events: none; }
header[data-testid="stHeader"] button, header[data-testid="stHeader"] button *,
header[data-testid="stHeader"] a, header[data-testid="stHeader"] a * { pointer-events: auto; }
[data-testid="stSidebarHeader"] { height: 1rem; min-height:1rem; }
.dm-tree details { border-radius:8px; }
.dm-tree summary { list-style:none; display:flex; align-items:center; gap:.55rem; padding:.5rem .5rem; border-radius:8px;
  cursor:pointer; font-size:.92rem; color:var(--dm-ink); font-weight:500; }
.dm-tree summary::-webkit-details-marker { display:none; }
.dm-tree summary:hover { background: var(--dm-s2); }
.dm-tree details[open] > summary { background: var(--dm-s2); }
.dm-tree summary .chev { width:.7rem; color:var(--dm-faint); transition: transform .15s; flex:none; }
.dm-tree details[open] > summary .chev { transform: rotate(90deg); }
.dm-tree summary .cnt { margin-left:auto; color:var(--dm-muted); font-size:.78rem; font-variant-numeric: tabular-nums; font-weight:400; }
.dm-tree summary img.tb { flex:none; }
.dm-cols { list-style:none; margin:.15rem 0 .35rem; padding:0 0 0 1.6rem; }
.dm-cols li { display:flex; align-items:center; gap:.5rem; padding:.26rem .3rem; font-size:.84rem; color:var(--dm-ink); }
.dm-cols li span.n { overflow-wrap:anywhere; }
.dm-cols li small { margin-left:auto; color:var(--dm-faint); font-size:.68rem; letter-spacing:.02em; white-space:nowrap; }
.dm-type { display:inline-grid; place-items:center; min-width:1.45rem; height:1.1rem; border-radius:4px;
           font-family:var(--dm-mono); font-size:.58rem; font-weight:600; flex:none; }
.dm-type.num { color:#93B4FF; background:rgba(71,120,255,.14); }
.dm-type.txt { color:#5EEAD4; background:rgba(45,212,191,.12); }
.dm-type.dt { color:#C4B5FD; background:rgba(167,139,250,.14); }
.dm-type.bool { color:#FCD34D; background:rgba(251,191,36,.12); }
.dm-type.oth { color:var(--dm-muted); background:rgba(152,166,184,.12); }
mark.dm-hit { background: rgba(45,212,191,.28); color: inherit; border-radius:3px; padding:0 1px; }
[class*="st-key-sq-"] button { background: transparent !important; border:0 !important; border-bottom:1px solid var(--dm-line) !important;
  border-radius:0 !important; justify-content:flex-start !important; padding:.55rem .25rem !important; min-height:0 !important; }
[class*="st-key-sq-"] button p { font-size:.92rem !important; text-align:left; color:var(--dm-ink); }
[class*="st-key-sq-"] button::after { content:"→"; margin-left:auto; padding-left:.5rem; color:var(--dm-faint); }
[class*="st-key-sq-"] button:hover { background: var(--dm-s2) !important; }
[class*="st-key-sq-"] button:hover::after { color: var(--dm-accent); }

/* ---------- header ---------- */
[class*="st-key-dataset_path"] [data-baseweb="select"] > div { background: var(--dm-s1); border-color: var(--dm-line-2) !important;
  min-height: 3rem; }
[class*="st-key-settings_pop"] button { border-radius:50% !important; width:2.6rem; height:2.6rem; padding:0 !important; }

/* ---------- pipeline progress ---------- */
.dm-steps { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:.5rem; margin:.5rem 0 .2rem; }
.dm-step { border:1px solid var(--dm-line); border-radius:10px; padding:.55rem .65rem; font-size:.84rem; color:var(--dm-faint);
           background:var(--dm-s1); display:flex; gap:.45rem; align-items:center; min-width:0; }
.dm-step i { font-style:normal; font-family:var(--dm-mono); font-size:.66rem; }
.dm-step.active { color:var(--dm-ink); border-color:var(--dm-accent); background:rgba(45,212,191,.07); }
.dm-step.active i::before { content:""; display:inline-block; width:.55rem; height:.55rem; border-radius:50%;
           border:2px solid var(--dm-accent); border-right-color:transparent; animation: dm-spin .8s linear infinite;
           margin-right:.25rem; vertical-align:-1px; }
.dm-step.done { color:var(--dm-muted); }
.dm-step.done i { color:var(--dm-ok); }
@keyframes dm-spin { to { transform: rotate(360deg); } }
@media (max-width: 640px) { .dm-steps { grid-template-columns: repeat(2, minmax(0,1fr)); } }

/* ---------- visualization studio ---------- */
[class*="st-key-studio-panel"] { background: var(--dm-s1); border:1px solid var(--dm-line); border-radius: 16px; padding: 1.1rem !important; }
[class*="st-key-vk-"] { border:1px solid var(--dm-line-2); border-radius:12px; padding:.75rem .4rem .35rem !important;
  background: var(--dm-s2); transition: border-color .15s, transform .15s, background .15s; gap:.1rem !important; }
[class*="st-key-vk-"]:hover { border-color: #3B4B60; transform: translateY(-1px); }
[class*="st-key-vk-"][class*="-selected"] { border-color: var(--dm-accent); background: rgba(45,212,191,.07);
  box-shadow: 0 0 0 1px var(--dm-accent), 0 10px 24px -14px rgba(45,212,191,.8); }
[class*="st-key-vk-"] img { display:block; margin:0 auto; width:30px; height:30px; }
[class*="st-key-vk-"] button { background:transparent !important; border:0 !important; min-height:1.8rem !important; padding:.1rem .2rem !important; }
[class*="st-key-vk-"] button p { font-size:.86rem !important; color:var(--dm-ink); }
[class*="st-key-chartcard"] { background: var(--dm-bg); border:1px solid var(--dm-line); border-radius: 12px; padding: .8rem .9rem !important; }
.dm-problem { border:1px dashed rgba(251,191,36,.45); background:rgba(251,191,36,.05); border-radius:12px;
              padding:.8rem .9rem; font-size:.85rem; color:var(--dm-ink); }
.dm-problem b { color:#FCD34D; }

/* compact action row under the evidence tabs */
[class*="st-key-actions-"] button { border:1px solid var(--dm-line-2) !important; border-radius:8px !important;
  padding:.3rem .8rem !important; background:var(--dm-s1) !important; }
[class*="st-key-actions-"] button:hover { border-color: var(--dm-accent) !important; }
[class*="st-key-actions-"] button p { font-size:.86rem !important; }

/* tables */
[data-testid="stDataFrame"] { border:1px solid var(--dm-line); border-radius:10px; }
</style>
"""

LOGO = """<svg width="{s}" height="{s}" viewBox="0 0 32 32" fill="none" aria-hidden="true">
<rect x="4" y="12" width="5.5" height="16" rx="2.75" fill="#2DD4BF"/>
<rect x="13.25" y="3" width="5.5" height="25" rx="2.75" fill="#2DD4BF"/>
<rect x="22.5" y="15" width="5.5" height="13" rx="2.75" fill="#2DD4BF"/></svg>"""


def brand(tagline: bool = False, size: int = 32) -> str:
    tag = "<small>ASK. QUERY. VERIFY.</small>" if tagline else ""
    return f'<div class="dm-brand">{svg_img(LOGO.format(s=size))}<div><span class="dm-word">DataMind</span>{tag}</div></div>'


SNOWFLAKE_ICON = svg_img('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#29B5E8" stroke-width="2" '
                  'stroke-linecap="round" aria-hidden="true"><path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7"/>'
                  '<path d="m9.5 3.5 2.5 2 2.5-2M9.5 20.5l2.5-2 2.5 2M3.6 10.6l3-1-.6-3M18 17.4l.6-3 3-1M3.6 13.4l3 1-.6 3'
                  'M18 6.6l.6 3 3 1"/></svg>')
GEMMA_ICON = svg_img('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#C084FC" stroke-width="1.8" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.5 2A2.5 2.5 0 0 0 7 4.5v.5'
              'a3 3 0 0 0-2.5 4.5A3 3 0 0 0 6 15a3 3 0 0 0 3.5 4.5A2.5 2.5 0 0 0 12 22V4.5A2.5 2.5 0 0 0 9.5 2z"/>'
              '<path d="M14.5 2A2.5 2.5 0 0 1 17 4.5v.5a3 3 0 0 1 2.5 4.5A3 3 0 0 1 18 15a3 3 0 0 1-3.5 4.5A2.5 2.5 0 0 1 '
              '12 22"/></svg>')


def apply() -> None:
    st.html(BASE_CSS)
