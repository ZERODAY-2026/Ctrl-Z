"""DataMind — Ask. Query. Verify.  Streamlit entry point and router (presentation only; logic lives in src/).

Pages: Home (landing), Workspace (ask + evidence + visualization studio), Analysis (one result, full width).
Run:  streamlit run app.py
"""

from __future__ import annotations

import logging

import streamlit as st

from ui import analysis, home, nav, theme, workspace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
st.set_page_config(page_title="DataMind — Ask. Query. Verify.", page_icon=":material/query_stats:", layout="wide")
theme.apply()

nav.PAGES.update({
    "home": st.Page(home.render, title="DataMind — Ask. Query. Verify.", url_path="home", default=True),
    "workspace": st.Page(workspace.render, title="DataMind — Workspace", url_path="workspace"),
    "analysis": st.Page(analysis.render, title="DataMind — Analysis", url_path="analysis"),
})
page = st.navigation(list(nav.PAGES.values()), position="hidden")

if st.query_params.get("view") == "app":  # old deep link to the workspace
    st.query_params.clear()
    nav.go("workspace")

page.run()
