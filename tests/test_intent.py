"""Action-intent routing and the read-only execution boundary (no model or database needed).

Gemma is replaced by a fake that returns a fixed classification, so these tests check what the backend DOES with
each intent, not how well the model classifies (that is measured by scripts/run_intent_eval.py).
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from conftest import make_snapshot
from src.orchestrator import HYPOTHETICAL_HINT, Orchestrator, Status, history_entry
from src.schemas.intent import ActionIntent, IntentClassification
from src.schemas.query_plan import QueryPlan
from src.services.snowflake_service import QueryResult, SnowflakeService
from src.services.sql_service import SqlCompiler
from src.utils.errors import GemmaError, QueryValidationError

DB = "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1"
REVENUE_BY_REGION = {"intent": "aggregate", "tables": ["REGION"], "dimensions": [{"column": "REGION.R_NAME"}],
                     "measures": [{"alias": "revenue", "metric": "revenue"}]}


def intent(kind, **kw):
    return IntentClassification(intent=kind, **kw)


class FakeGemma:
    def __init__(self, classification=None, error=None, plan=REVENUE_BY_REGION):
        self.classification, self.error, self.plan_json = classification, error, plan
        self.calls, self.plan_questions = [], []

    def classify_intent(self, question, description, history=None):
        if self.error:
            raise self.error
        return self.classification

    def plan(self, question, context, history=None, feedback=None):
        self.plan_questions.append(question)
        return QueryPlan.model_validate(self.plan_json)

    def explain(self, question, evidence_text):
        return "The result is shown in the table."


class FakeSnowflake:
    def __init__(self):
        self.executed = []

    def run_validated(self, sql):
        self.executed.append(sql)
        return QueryResult(sql, "01-fake-query-id", ["R_NAME", "REVENUE"], ["TEXT", "NUMBER"],
                           [("ASIA", 10), ("EUROPE", 20)], 2, False, 5)


def orchestrator(gemma, tpch_dataset):
    orch = Orchestrator.__new__(Orchestrator)
    snap = make_snapshot()
    orch.settings = SimpleNamespace(dataset=tpch_dataset, max_rows=100)
    orch.sf = FakeSnowflake()
    orch.schema = SimpleNamespace(get=lambda: snap, prompt_context=lambda s, q: "schema")
    orch.gemma = gemma
    orch.compiler = SqlCompiler(tpch_dataset)
    return orch


# ---------------------------------------------------------------- schema validation
def test_classification_requires_supporting_fields():
    with pytest.raises(ValidationError):
        intent("MODIFY_DATA")  # must say what would change
    with pytest.raises(ValidationError):
        intent("AMBIGUOUS")  # must ask something
    with pytest.raises(ValidationError):
        intent("UNSUPPORTED")  # must give a reason
    with pytest.raises(ValidationError):
        intent("READ_ANALYSIS", confidence=1.5)
    with pytest.raises(ValidationError):
        IntentClassification.model_validate({"intent": "READ_ANALYSIS", "run_sql": "DELETE"})
    with pytest.raises(ValidationError):
        intent("DELETE_EVERYTHING")
    assert intent("READ_ANALYSIS").confidence == 0.5


def test_classification_text_is_bounded():
    c = intent("MODIFY_DATA", requested_change="x " * 1000)
    assert len(c.requested_change) <= 300


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("confidence", [0.0, 0.3, 0.99])
def test_modify_is_refused_without_any_query(confidence, tpch_dataset):
    # Confidence is self-reported and uncalibrated, so it must not change the decision.
    gemma = FakeGemma(intent("MODIFY_DATA", confidence=confidence, requested_change="set one value to another",
                             safe_alternative="Compare the two values"))
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("some request to change stored values")
    assert resp.status == Status.UNSUPPORTED and "read-only" in resp.answer
    assert "Compare the two values" in resp.answer
    assert gemma.plan_questions == [] and orch.sf.executed == [] and resp.evidence is None
    assert resp.action_intent.intent == ActionIntent.MODIFY_DATA


def test_ambiguous_asks_and_runs_nothing(tpch_dataset):
    gemma = FakeGemma(intent("AMBIGUOUS", clarification_question="Do you want the values compared or a what-if?"))
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("an unclear request")
    assert resp.status == Status.CLARIFY and resp.answer.startswith("Do you want")
    assert gemma.plan_questions == [] and orch.sf.executed == []
    # The clarification stays in the conversation so the user's short reply is understood.
    entry = history_entry(resp)
    assert entry and entry["question"] == "an unclear request" and "AMBIGUOUS" in entry["plan"]


def test_unsupported_runs_nothing(tpch_dataset):
    orch = orchestrator(FakeGemma(intent("UNSUPPORTED", reason="Sending email is outside DataMind.")), tpch_dataset)
    resp = orch.ask("email this to my manager")
    assert resp.status == Status.UNSUPPORTED and "email" in resp.answer and orch.sf.executed == []


def test_read_analysis_keeps_existing_pipeline(tpch_dataset):
    gemma = FakeGemma(intent("READ_ANALYSIS"))
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("revenue by region")
    assert resp.status == Status.ANSWERED and resp.evidence.query_id == "01-fake-query-id"
    assert gemma.plan_questions == ["revenue by region"]  # no hint added
    assert len(orch.sf.executed) == 1 and orch.sf.executed[0].lstrip().upper().startswith("SELECT")


def test_hypothetical_runs_read_only_with_hint_and_note(tpch_dataset):
    gemma = FakeGemma(intent("HYPOTHETICAL"))
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("what would revenue by region be if one value matched another")
    assert resp.status == Status.ANSWERED
    assert HYPOTHETICAL_HINT in gemma.plan_questions[0]
    assert resp.evidence.question == "what would revenue by region be if one value matched another"  # hint not shown
    assert any("No data was changed" in n for n in resp.notes)
    assert orch.sf.executed[0].lstrip().upper().startswith("SELECT")


@pytest.mark.parametrize("error", [GemmaError("unavailable", "down"), GemmaError("invalid", "bad json")])
def test_classifier_failure_falls_back_to_read_only_pipeline(error, tpch_dataset):
    gemma = FakeGemma(error=error)
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("revenue by region")
    assert resp.status == Status.ANSWERED and resp.action_intent is None
    assert any("intent check was unavailable" in n for n in resp.notes)


def test_misclassified_mutation_still_cannot_execute(tpch_dataset):
    """Even if the classifier and planner were fooled, model-written mutating SQL never reaches Snowflake."""
    gemma = FakeGemma(intent("READ_ANALYSIS"), plan={"intent": "advanced_sql"})
    gemma.draft_sql = lambda *a, **k: SimpleNamespace(sql=f"UPDATE {DB}.NATION SET N_NAME = 'X'", explanation="",
                                                       assumptions=[])
    orch = orchestrator(gemma, tpch_dataset)
    resp = orch.ask("anything")
    assert resp.status == Status.ERROR and resp.error_category == "query_validation"
    assert orch.sf.executed == []


# ---------------------------------------------------------------- execution boundary
@pytest.mark.parametrize("sql", [
    f"UPDATE {DB}.NATION SET N_NAME = 'X'",
    f"DELETE FROM {DB}.ORDERS",
    f"SELECT 1 FROM {DB}.ORDERS; DROP TABLE {DB}.ORDERS",
    f"WITH d AS (DELETE FROM {DB}.ORDERS) SELECT * FROM d",
    f"SELECT * INTO copy_of_orders FROM {DB}.ORDERS",
    f"EXECUTE IMMEDIATE 'DELETE FROM {DB}.ORDERS'",
    f"CREATE OR REPLACE TABLE {DB}.ORDERS AS SELECT * FROM {DB}.ORDERS",
    "UNDROP TABLE ORDERS",
])
def test_snowflake_execution_rejects_mutations_without_guard(sql):
    sf = SnowflakeService.__new__(SnowflakeService)
    sf._execute = lambda *a, **k: pytest.fail("mutating SQL reached the connector")
    with pytest.raises(QueryValidationError):
        sf.run_validated(sql)
