"""Deterministic ambiguity / planning-slip checks (no model or database needed)."""

from src.orchestrator import Orchestrator, Status, plan_sanity_problem
from src.schemas.query_plan import QueryPlan


def p(**kw):
    return QueryPlan.model_validate(kw)


TOTAL = {"intent": "aggregate", "measures": [{"alias": "revenue", "metric": "revenue"}]}


def test_breakdown_without_dimension_is_flagged():
    assert plan_sanity_problem("top 5 nations by revenue", p(**TOTAL))
    assert plan_sanity_problem("revenue per month", p(**TOTAL))


def test_plain_total_not_flagged():
    assert plan_sanity_problem("What is the total revenue?", p(**TOTAL)) is None
    assert plan_sanity_problem("revenue from nearby stores", p(**TOTAL)) is None  # word boundary


def test_top_within_group_routes_to_advanced():
    plan = p(intent="aggregate", dimensions=[{"column": "REGION.R_NAME"}, {"column": "NATION.N_NAME"}],
             measures=[{"alias": "revenue", "metric": "revenue"}])
    assert "advanced_sql" in plan_sanity_problem("For each region, which nation has the highest revenue?", plan)


def test_percentage_without_share_of_total_is_flagged():
    by_mode = {"intent": "aggregate", "dimensions": [{"column": "LINEITEM.L_SHIPMODE"}]}
    raw = p(**by_mode, measures=[{"alias": "revenue", "metric": "revenue"}])
    share = p(**by_mode, measures=[{"alias": "revenue", "metric": "revenue", "share_of_total": True}])
    assert "share_of_total" in plan_sanity_problem("What percentage of revenue comes from each ship mode?", raw)
    assert "share_of_total" in plan_sanity_problem("Revenue % by ship mode", raw)
    assert plan_sanity_problem("What percentage of revenue comes from each ship mode?", share) is None
    assert plan_sanity_problem("Revenue by ship mode", raw) is None


def test_columns_moved_to_dimensions_for_aggregates():
    plan = p(intent="aggregate", columns=["CUSTOMER.C_MKTSEGMENT", "measures[].x"],
             measures=[{"alias": "n", "function": "COUNT", "column": "*"}])
    assert [d.column for d in plan.dimensions] == ["CUSTOMER.C_MKTSEGMENT"] and plan.columns == []


class _NoCallGemma:
    calls: list = []

    def plan(self, *a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("Gemma should not be called for invalid input")


def test_input_validation_short_circuits():
    orch = Orchestrator.__new__(Orchestrator)
    orch.gemma = _NoCallGemma()
    assert orch.ask("").status == Status.ERROR
    assert orch.ask("x" * 600).status == Status.ERROR
