"""LIVE integration tests. These hit real Snowflake and real Gemma; they skip (with a reason)
when credentials or the model are unavailable. Nothing here is mocked.

    pytest -m live -v
"""

import pytest

from config.settings import load_settings
from src.orchestrator import Orchestrator, Status
from src.security.sql_guard import validate
from src.services.gemma_service import GemmaService
from src.services.schema_service import SchemaService
from src.services.snowflake_service import SnowflakeService

pytestmark = pytest.mark.live
SETTINGS = load_settings()
SF_MISSING = SETTINGS.snowflake.missing()


@pytest.fixture(scope="module")
def sf():
    if SF_MISSING:
        pytest.skip(f"Snowflake credentials not configured: {', '.join(SF_MISSING)}")
    svc = SnowflakeService(SETTINGS.snowflake, SETTINGS.max_rows)
    yield svc
    svc.close()


@pytest.fixture(scope="module")
def gemma():
    g = GemmaService(SETTINGS.gemma)
    status = g.health_check()
    if not status.get("verified"):
        pytest.skip(f"Gemma not available: {status.get('error')}")
    return g


@pytest.fixture(scope="module")
def snapshot(sf):
    return SchemaService(sf, SETTINGS.dataset).get()


def test_snowflake_connection(sf):
    h = sf.health_check()
    assert h["query_id"] and h["user"]


def test_schema_discovery(snapshot):
    assert snapshot.tables
    assert all(t.columns for t in snapshot.tables.values())


def test_real_query_returns_query_id(sf, snapshot):
    table = next(iter(snapshot.tables))
    g = validate(f"SELECT COUNT(*) AS n FROM {snapshot.fqn(table)}", snapshot, 10)
    r = sf.run_validated(g.sql)
    assert r.query_id and r.rows[0][0] >= 0


def test_gemma_plan_is_valid(gemma, snapshot, sf):
    ctx = SchemaService(sf, SETTINGS.dataset).prompt_context(snapshot, "How many rows are in each table?")
    plan = gemma.plan("How many customers are there?", ctx)
    assert plan.intent.value in {"aggregate", "list", "clarify", "advanced_sql"}


def test_end_to_end(sf, gemma):
    orch = Orchestrator(SETTINGS, sf, SchemaService(sf, SETTINGS.dataset), gemma)
    resp = orch.ask(SETTINGS.dataset.example_questions[0])
    assert resp.status in (Status.ANSWERED, Status.EMPTY), resp.answer
    assert resp.evidence and resp.evidence.query_id and resp.evidence.sql


def test_write_request_never_executes(sf, gemma):
    orch = Orchestrator(SETTINGS, sf, SchemaService(sf, SETTINGS.dataset), gemma)
    resp = orch.ask("Delete every order placed in 1992")
    assert resp.status != Status.ANSWERED or "DELETE" not in (resp.evidence.sql.upper() if resp.evidence else "")
