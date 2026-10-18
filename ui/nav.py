"""Page registry filled by app.py, so pages can link to each other without import cycles."""

from __future__ import annotations

import streamlit as st

PAGES: dict[str, st.Page] = {}


def go(name: str) -> None:
    st.switch_page(PAGES[name])
