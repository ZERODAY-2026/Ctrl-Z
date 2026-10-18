"""Backend wiring and session state shared by the three pages. No business logic lives here."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import streamlit as st

from config.settings import ROOT, DatasetConfig, Settings, load_settings
from src.orchestrator import DataMindResponse, Orchestrator, history_entry
from src.services import visualization_service
from src.services.gemma_service import GemmaService
from src.services.schema_service import SchemaService
from src.services.snowflake_service import SnowflakeService
from src.utils.errors import DataMindError


@dataclass
class Turn:
    """One question and its real backend response, plus UI-only bookkeeping."""
    resp: DataMindResponse
    asked_at: dt.datetime
    received_at: dt.datetime
    reruns: list[dict[str, Any]] = field(default_factory=list)  # results of "re-run SQL"


# ---------------------------------------------------------------- services
@st.cache_resource(show_spinner=False)
def _base() -> tuple[Settings, SnowflakeService, GemmaService]:
    settings = load_settings()
    return settings, SnowflakeService(settings.snowflake, settings.max_rows), GemmaService(settings.gemma)


def default_dataset_path() -> str:
    return str(Path(_base()[0].dataset.path).resolve())


@st.cache_resource(show_spinner=False)
def _dataset_services(path: str) -> tuple[Settings, SchemaService, Orchestrator]:
    settings, sf, gemma = _base()
    if path != default_dataset_path():
        # Other dataset files load as written; DATAMIND_DATABASE/SCHEMA overrides apply only to the configured one.
        settings = replace(settings, dataset=DatasetConfig.load(path))
    schema = SchemaService(sf, settings.dataset, settings.schema_cache_ttl_s)
    return settings, schema, Orchestrator(settings, sf, schema, gemma)


def available_datasets() -> dict[str, str]:
    """path -> display name for every dataset config in datasets/."""
    out = {default_dataset_path(): _base()[0].dataset.name}
    for p in sorted((ROOT / "datasets").glob("*.toml")):
        key = str(p.resolve())
        if key not in out:
            try:
                out[key] = DatasetConfig.load(p).name
            except DataMindError:
                continue
    return out


def services() -> tuple[Settings, SnowflakeService, SchemaService, Orchestrator]:
    """Raises DataMindError on configuration problems (caller shows it)."""
    path = st.session_state.get("dataset_path") or default_dataset_path()
    settings, schema, orch = _dataset_services(path)
    visualization_service.COLUMN_LABELS = settings.dataset.column_labels
    return settings, _base()[1], schema, orch


# ---------------------------------------------------------------- health (real round-trips, cached briefly)
@st.cache_data(ttl=300, show_spinner=False)
def snowflake_status() -> dict:
    try:
        return {"ok": True, **_base()[1].health_check(), "checked_at": _now()}
    except DataMindError as exc:
        return {"ok": False, "error": exc.user_message, "checked_at": _now()}


@st.cache_data(ttl=300, show_spinner=False)
def gemma_status() -> dict:
    return {**_base()[2].health_check(), "checked_at": _now()}


def run_health_checks() -> tuple[dict, dict]:
    sf, gm = snowflake_status(), gemma_status()
    st.session_state.health = {"snowflake": sf, "gemma": gm}
    return sf, gm


def recheck() -> None:
    snowflake_status.clear()
    gemma_status.clear()
    st.session_state.pop("health", None)


def known_health() -> dict | None:
    """Health results already gathered in this session, or None (never triggers a check)."""
    return st.session_state.get("health")


def _now() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- conversation
def init() -> None:
    ss = st.session_state
    ss.setdefault("turns", [])  # list[Turn]
    ss.setdefault("history", [])  # follow-up context sent to Gemma (question + plan of earlier turns)
    ss.setdefault("viz", {})  # turn index -> chart spec dict
    ss.setdefault("viz_turn", None)  # turn index shown in the studio
    ss.setdefault("viz_mode", "panel")  # "panel", "wide" or "hidden"
    ss.setdefault("pending", None)  # question queued by a button


def turns() -> list[Turn]:
    return st.session_state.turns


def record(resp: DataMindResponse, asked_at: dt.datetime) -> int:
    st.session_state.turns.append(Turn(resp, asked_at, dt.datetime.now()))
    entry = history_entry(resp)
    if entry:
        st.session_state.history.append(entry)
    i = len(st.session_state.turns) - 1
    if resp.evidence is not None and resp.evidence.rows:
        st.session_state.viz_turn = i
    return i


def clear_conversation() -> None:
    for k in ("turns", "history", "viz"):
        st.session_state[k] = [] if k != "viz" else {}
    st.session_state.viz_turn = None
    st.session_state.pop("analysis_turn", None)


def queue(question: str) -> None:
    st.session_state.pending = question


def context_turns() -> int:
    """How many earlier questions the backend will send as context (gemma_service uses the last 3)."""
    return min(3, len(st.session_state.history))


def partial(resp: DataMindResponse) -> bool:
    """True when the rows are a top-N cut or truncated, i.e. not the whole population."""
    ev = resp.evidence
    return bool(ev and (ev.truncated or (resp.plan is not None and resp.plan.limit)))
