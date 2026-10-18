"""Connection status chips. They show only results of real health checks; unchecked means "status unknown"."""

from __future__ import annotations

import html

from ui.theme import GEMMA_ICON, SNOWFLAKE_ICON


def _chip(icon: str, name: str, css: str, state: str, title: str) -> str:
    return (f'<span class="dm-chip {css}" title="{html.escape(title)}">{icon}<span>{html.escape(name)}</span>'
            f'<span class="st"><span class="dot"></span>{html.escape(state)}</span></span>')


def chips(sf: dict | None, gm: dict | None, gemma_model: str = "Gemma", unknown: str = "Status unknown") -> str:
    """`unknown` labels a check that has not finished (e.g. "Checking…" while it runs)."""
    if sf is None:
        a = _chip(SNOWFLAKE_ICON, "Snowflake", "unknown", unknown, "No health check has finished yet")
    elif sf.get("ok"):
        a = _chip(SNOWFLAKE_ICON, "Snowflake", "ok", "Connected",
                  f"Live query succeeded at {sf.get('checked_at')} · {sf.get('warehouse')} · {sf.get('role')}")
    else:
        a = _chip(SNOWFLAKE_ICON, "Snowflake", "bad", "Not connected", sf.get("error", ""))
    name = (gm or {}).get("model") or gemma_model
    name = name[:1].upper() + name[1:]
    if gm is None:
        b = _chip(GEMMA_ICON, name, "unknown", unknown, "No health check has finished yet")
    elif gm.get("verified"):
        b = _chip(GEMMA_ICON, name, "ok", "Online",
                  f"Inference check answered in {gm.get('latency_ms', 0):,} ms at {gm.get('checked_at')}")
    else:
        b = _chip(GEMMA_ICON, name, "bad", "Offline", gm.get("error", "inference check failed"))
    return f'<div class="dm-chips">{a}{b}</div>'
